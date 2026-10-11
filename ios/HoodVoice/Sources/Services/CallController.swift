import AVFoundation
import CallKit
import CryptoKit
import Foundation
import HoodVoiceCore
import SwiftUI

extension UUID {
    /// CallKit identifies a call by UUID. Hood identifies it by `call_<hex>`. Deriving one from the other means a
    /// duplicate push for the same call maps to the same CallKit call instead of ringing twice.
    static func forHoodCall(_ callId: String) -> UUID {
        let digest = Array(SHA256.hash(data: Data(("hood-call:" + callId).utf8)))
        var b = Array(digest.prefix(16))
        b[6] = (b[6] & 0x0F) | 0x50      // version 5 style
        b[8] = (b[8] & 0x3F) | 0x80
        return UUID(uuid: (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]))
    }
}

/// Owns one call at a time: runs the pure `CallStateMachine`, performs its effects against Hood's API, CallKit
/// and LiveKit, and publishes what the call screen shows. It contains no mission or approval logic — spoken
/// requests are handled by Hood on the server; the phone only carries audio and signs confirmations.
@MainActor
final class CallController: ObservableObject {
    @Published private(set) var phase: CallPhase = .idle
    @Published private(set) var transcript: [TranscriptLine] = []
    @Published private(set) var isMuted = false
    @Published private(set) var speakerOn = false
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var callerName = "Hood"
    @Published private(set) var reason = ""
    @Published private(set) var directionIsIncoming = false
    @Published var banner: String?

    /// Set by the app model: where to talk to Hood, and what to do when Hood asks for a confirmation.
    var apiProvider: () -> HoodAPIClient? = { nil }
    var languageProvider: () -> String? = { nil }
    var onConfirmationRequested: (() -> Void)?
    var onCallFinished: (() -> Void)?

    private var machine = CallStateMachine()
    private var feed = CallEventFeed()
    private var transcriptBuffer = TranscriptBuffer()
    private let callKit: CallKitManager
    private let room = LiveKitRoomClient()
    private var uuid: UUID?
    private var startGate: Task<Bool, Never>?
    private var pushedJoin: JoinGrant?
    private var connectedAt: Date?
    private var pollTask: Task<Void, Never>?
    private var tickTask: Task<Void, Never>?
    private var ringTask: Task<Void, Never>?

    init(callKit: CallKitManager) {
        self.callKit = callKit
        wireCallKit()
        wireRoom()
    }

    // MARK: user actions
    func pressCall() {
        guard phase.isOver else { return }
        reset(direction: false)
        let id = UUID()
        uuid = id
        startGate = Task { [callKit] in await callKit.startOutgoing(uuid: id, name: "Hood") }
        run(.pressCall)
    }

    func callBack(missedCallId: String) {
        guard phase.isOver, let api = apiProvider() else { return }
        reset(direction: false)
        let id = UUID(); uuid = id
        let gate = Task { [callKit] in await callKit.startOutgoing(uuid: id, name: "Hood") }
        _ = machine.send(.pressCall)
        phase = machine.phase
        Task {
            guard await gate.value else { run(.startFailed("iOS could not start the call.")); return }
            do {
                let r = try await api.callBack(missedCallId: missedCallId)
                run(.startSucceeded(r.join, callId: r.call.callId))
            } catch { run(.startFailed(Self.describe(error))) }
        }
    }

    /// In-app Accept button (the lock screen and banner use CallKit's own UI, which ends up in the same place).
    func accept() {
        guard phase == .ringing, let id = uuid else { return }
        callKit.requestAnswer(uuid: id) { [weak self] in Task { @MainActor in self?.run(.userAnswered) } }
    }

    func dismissBanner() { banner = nil }

    func hangUp() {
        guard let id = uuid else { run(.userEnded); return }
        // CallKit calls back into onEnd, which feeds the state machine; if iOS no longer knows the call, end here.
        callKit.requestEnd(uuid: id) { [weak self] in
            Task { @MainActor in self?.run(self?.phase == .ringing ? .userDeclined : .userEnded) }
        }
    }

    func toggleMute() {
        guard let id = uuid else { return }
        let target = !isMuted
        callKit.requestMute(uuid: id, muted: target) { [weak self] in
            Task { @MainActor in self?.isMuted = target; await self?.room.setMuted(target) }
        }
    }

    func toggleSpeaker() {
        speakerOn.toggle()
        try? AVAudioSession.sharedInstance().overrideOutputAudioPort(speakerOn ? .speaker : .none)
    }

    // MARK: Hood calls the owner (from the VoIP push)
    /// Called synchronously from the PushKit callback. Whatever the payload says, a call is reported to CallKit,
    /// because iOS punishes VoIP pushes that do not produce a call.
    func handleIncomingPush(_ payload: [AnyHashable: Any], done: @escaping () -> Void) {
        switch PushPayload.parse(payload) {
        case .incomingCall(let call):
            let callUUID = UUID.forHoodCall(call.callId)
            let isDuplicate = !phase.isOver && machine.callId == call.callId     // the same ring delivered twice
            let busy = !phase.isOver && !isDuplicate                              // already on another Hood call
            callKit.reportIncoming(uuid: callUUID, caller: call.caller) { [weak self] error in
                DispatchQueue.main.async {
                    defer { done() }
                    guard let self, !isDuplicate else { return }
                    if busy { self.callKit.reportEnded(uuid: callUUID, reason: .failed); return }
                    // iOS refused to ring (Do Not Disturb filter, block list): nothing can be answered, so this is a
                    // missed call; the server sends the "Missed call from Hood" notification when the ring expires.
                    if error != nil, self.uuid == callUUID, self.phase == .ringing { self.run(.ringTimeout) }
                }
            }
            if !isDuplicate && !busy {
                reset(direction: true)
                uuid = callUUID; pushedJoin = call.join
                callerName = call.caller; reason = call.reason
                run(.incomingCall(callId: call.callId))
                armRingTimer()
            }
        case .notification, .unknown:
            let throwaway = UUID()
            callKit.reportIncoming(uuid: throwaway, caller: "Hood") { [weak self] _ in
                self?.callKit.reportEnded(uuid: throwaway, reason: .failed)
                done()
            }
        }
    }

    // MARK: CallKit wiring
    private func wireCallKit() {
        callKit.onAnswer = { [weak self] id in Task { @MainActor in if id == self?.uuid { self?.run(.userAnswered) } } }
        callKit.onEnd = { [weak self] id in
            Task { @MainActor in
                guard let self, id == self.uuid else { return }
                self.run(self.phase == .ringing ? .userDeclined : .userEnded)
            }
        }
        callKit.onMute = { [weak self] id, muted in
            Task { @MainActor in
                guard let self, id == self.uuid else { return }
                self.isMuted = muted
                await self.room.setMuted(muted)
            }
        }
        callKit.onReset = { [weak self] in Task { @MainActor in self?.run(.userEnded) } }
    }

    private func wireRoom() {
        room.onConnected = { [weak self] in Task { @MainActor in self?.run(.roomConnected) } }
        room.onUnexpectedDisconnect = { [weak self] in Task { @MainActor in self?.run(.roomDisconnected) } }
        room.onTranscript = { [weak self] id, speaker, text, final in
            Task { @MainActor in self?.applyTranscript(id: id, speaker: speaker, text: text, final: final) }
        }
        room.onHoodEvent = { [weak self] obj in
            Task { @MainActor in self?.handleDataEvent(obj) }
        }
    }

    // MARK: state machine driver
    private func run(_ input: CallInput) {
        let wasOver = phase.isOver
        let effects = machine.send(input)
        phase = machine.phase
        for e in effects { perform(e) }
        if case .failed = phase { finishCallKit(.failed) }
        if phase.isOver { stopTimers(); if !wasOver { onCallFinished?() } }
        else if phase.isInCall { startTimers() }
    }

    private func perform(_ effect: CallEffect) {
        switch effect {
        case .apiStart:
            guard let api = apiProvider() else { run(.startFailed("This phone is not paired with Hood.")); return }
            let language = languageProvider()
            let gate = startGate
            Task {
                if let gate, await gate.value == false { run(.startFailed("iOS could not start the call.")); return }
                do {
                    let r = try await api.startCall(language: language)
                    run(.startSucceeded(r.join, callId: r.call.callId))
                } catch { run(.startFailed(Self.describe(error))) }
            }
        case .apiAnswer(let callId):
            let fallback = pushedJoin
            Task {
                do {
                    guard let api = apiProvider() else { throw HoodAPIError.unauthorized }
                    let r = try await api.answer(callId: callId)
                    run(.answerSucceeded(r.join))
                } catch {
                    // Hood's API may be unreachable from here for a moment; the ring push carried a short-lived grant.
                    if let fallback, JoinTokenInspector.isUsable(fallback) { run(.answerSucceeded(fallback)) }
                    else { run(.answerFailed(Self.describe(error))) }
                }
            }
        case .apiDecline(let callId):
            Task { try? await apiProvider()?.decline(callId: callId) }
        case .apiEnd(let callId):
            Task { try? await apiProvider()?.end(callId: callId) }
        case .apiRejoin(let callId, let delay):
            Task {
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                guard case .reconnecting = machine.phase else { return }
                do {
                    guard let api = apiProvider() else { throw HoodAPIError.unauthorized }
                    run(.rejoinSucceeded(try await api.rejoin(callId: callId).join))
                } catch { run(.rejoinFailed(Self.describe(error))) }
            }
        case .apiMarkConnected(let callId):
            connectedAt = connectedAt ?? Date()
            if let id = uuid, !directionIsIncoming { callKit.reportConnected(uuid: id) }
            Task { try? await apiProvider()?.markConnected(callId: callId) }
            startPolling(callId: callId)
        case .connectRoom(let grant):
            if let id = uuid, !directionIsIncoming { callKit.reportConnecting(uuid: id) }
            Task {
                await callKit.waitForAudio()
                do { try await room.connect(grant) }
                catch { run(.roomDisconnected) }
            }
        case .disconnectRoom:
            Task { await room.disconnect() }
        case .reportEnded(let cause):
            finishCallKit(Self.callKitReason(for: cause))
            if cause == .missed { banner = "Missed call from Hood." }
        case .notify(let message):
            banner = message
        }
    }

    // MARK: server events
    private func startPolling(callId: String) {
        pollTask?.cancel()
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard let self, !Task.isCancelled else { return }
                await self.pollOnce(callId: callId)
            }
        }
    }

    private func pollOnce(callId: String) async {
        guard let api = apiProvider(), machine.callId == callId, !phase.isOver else { return }
        guard let detail = try? await api.callDetail(callId: callId, afterEvent: feed.cursor) else { return }
        ingest(detail.events)
        if detail.call.state.isTerminal, !phase.isOver {
            run(.serverEnded(reason: feed.endedReason ?? detail.call.endReason ?? "ended"))
        }
    }

    private func handleDataEvent(_ obj: [String: Any]) {
        guard let kind = obj["type"] as? String else { return }
        if kind == "confirm_request" { onConfirmationRequested?() }
        if kind == "hood_event", let summary = obj["summary"] as? String { banner = summary }
        if kind == "ended", let reason = obj["reason"] as? String, !phase.isOver { run(.serverEnded(reason: reason)) }
    }

    private func ingest(_ events: [CallEvent]) {
        let fresh = feed.ingest(events)
        if fresh.contains(where: { $0.kind == "confirm_request" }) { onConfirmationRequested?() }
        if let hood = fresh.last(where: { $0.kind == "hood_event" })?.detail["summary"]?.stringValue { banner = hood }
    }

    private func applyTranscript(id: String, speaker: TranscriptLine.Speaker, text: String, final: Bool) {
        transcriptBuffer.apply(id: id, speaker: speaker, text: text, isFinal: final)
        transcript = transcriptBuffer.lines
    }

    // MARK: timers
    private func armRingTimer() {
        ringTask?.cancel()
        ringTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 40_000_000_000)
            guard !Task.isCancelled, self?.phase == .ringing else { return }
            self?.run(.ringTimeout)
        }
    }

    private func startTimers() {
        ringTask?.cancel()
        guard tickTask == nil else { return }
        tickTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self else { return }
                if let start = self.connectedAt { self.elapsed = Date().timeIntervalSince(start) }
                if case .reconnecting = self.phase { self.run(.tick(Date())) } else { _ = self.machine.send(.tick(Date())) }
            }
        }
    }

    private func stopTimers() {
        tickTask?.cancel(); tickTask = nil
        pollTask?.cancel(); pollTask = nil
        ringTask?.cancel(); ringTask = nil
    }

    // MARK: helpers
    private func reset(direction incoming: Bool) {
        machine = CallStateMachine()
        feed = CallEventFeed()
        transcriptBuffer.clear()
        transcript = []
        phase = .idle
        isMuted = false; speakerOn = false; elapsed = 0; connectedAt = nil
        pushedJoin = nil; banner = nil; reason = ""
        directionIsIncoming = incoming
        callerName = "Hood"
    }

    /// `nil` reason = the end was requested through CallKit itself, which has already removed the call.
    private func finishCallKit(_ reason: CXCallEndedReason?) {
        guard let id = uuid else { return }
        uuid = nil
        if let reason { callKit.reportEnded(uuid: id, reason: reason) }
    }

    static func callKitReason(for cause: EndCause) -> CXCallEndedReason? {
        switch cause {
        case .missed: return .unanswered
        case .networkLost, .error: return .failed
        case .remoteHangup, .limitReached: return .remoteEnded
        case .declined, .userHangup: return nil
        }
    }

    static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}
