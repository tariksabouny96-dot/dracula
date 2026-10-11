import AVFoundation
import CallKit
import Foundation
import LiveKit

/// What the call controller needs from the system call UI (a seam so the controller is unit-testable without iOS).
protocol CallKitControlling: AnyObject {
    var onAnswer: ((UUID) -> Void)? { get set }
    var onEnd: ((UUID) -> Void)? { get set }
    var onMute: ((UUID, Bool) -> Void)? { get set }
    var onReset: (() -> Void)? { get set }
    func reportIncoming(uuid: UUID, caller: String, completion: @escaping (Error?) -> Void)
    func startOutgoing(uuid: UUID, name: String) async -> Bool
    func reportConnecting(uuid: UUID)
    func reportConnected(uuid: UUID)
    func reportEnded(uuid: UUID, reason: CXCallEndedReason)
    func requestEnd(uuid: UUID, fallback: @escaping () -> Void)
    func requestMute(uuid: UUID, muted: Bool, fallback: @escaping () -> Void)
    func requestAnswer(uuid: UUID, fallback: @escaping () -> Void)
    func waitForAudio(timeout: TimeInterval) async
}

/// The system call UI. Incoming Hood calls show the native incoming-call screen; outgoing calls appear as a normal
/// call (lock screen, Recents). The LiveKit audio engine is only allowed to run inside CallKit's activation window.
final class CallKitManager: NSObject, CXProviderDelegate, CallKitControlling {
    var onAnswer: ((UUID) -> Void)?
    var onEnd: ((UUID) -> Void)?
    var onMute: ((UUID, Bool) -> Void)?
    var onAudioActive: (() -> Void)?
    var onReset: (() -> Void)?

    private let provider: CXProvider
    private let controller = CXCallController()
    private var audioWaiters: [CheckedContinuation<Void, Never>] = []
    private(set) var audioIsActive = false

    override init() {
        let config = CXProviderConfiguration()           // the app name comes from the bundle
        config.supportsVideo = false
        config.maximumCallsPerCallGroup = 1
        config.maximumCallGroups = 1
        config.supportedHandleTypes = [.generic]
        config.includesCallsInRecents = true
        provider = CXProvider(configuration: config)
        super.init()
        provider.setDelegate(self, queue: nil)
    }

    /// Call as early as possible, before any Room is created (LiveKit's documented CallKit setup).
    static func prepareAudioEngine() {
        AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false
        try? AudioManager.shared.setEngineAvailability(.none)
    }

    // MARK: reporting
    /// MUST be reached synchronously from the PushKit callback: iOS terminates apps that receive a VoIP push and
    /// do not report a call.
    func reportIncoming(uuid: UUID, caller: String, completion: @escaping (Error?) -> Void) {
        let update = CXCallUpdate()
        update.remoteHandle = CXHandle(type: .generic, value: caller)
        update.localizedCallerName = caller
        update.hasVideo = false
        update.supportsHolding = false
        update.supportsGrouping = false
        update.supportsUngrouping = false
        update.supportsDTMF = false
        provider.reportNewIncomingCall(with: uuid, update: update, completion: completion)
    }

    /// Asks iOS to start an outgoing call. Returns false when iOS refuses (for example another call is active),
    /// in which case the app must not open a Hood session it cannot carry audio for.
    func startOutgoing(uuid: UUID, name: String) async -> Bool {
        let handle = CXHandle(type: .generic, value: name)
        let action = CXStartCallAction(call: uuid, handle: handle)
        action.isVideo = false
        return await withCheckedContinuation { c in
            controller.request(CXTransaction(action: action)) { error in c.resume(returning: error == nil) }
        }
    }

    func reportConnected(uuid: UUID) { provider.reportOutgoingCall(with: uuid, connectedAt: Date()) }
    func reportConnecting(uuid: UUID) { provider.reportOutgoingCall(with: uuid, startedConnectingAt: Date()) }

    func reportEnded(uuid: UUID, reason: CXCallEndedReason) { provider.reportCall(with: uuid, endedAt: Date(), reason: reason) }

    /// Accept from inside the app: goes through CallKit so the audio session is activated exactly as for a lock-screen answer.
    func requestAnswer(uuid: UUID, fallback: @escaping () -> Void) {
        controller.request(CXTransaction(action: CXAnswerCallAction(call: uuid))) { error in
            if error != nil { DispatchQueue.main.async { fallback() } }
        }
    }

    /// `fallback` runs if iOS does not know the call (so the Hang up button can never be dead).
    func requestEnd(uuid: UUID, fallback: @escaping () -> Void) {
        controller.request(CXTransaction(action: CXEndCallAction(call: uuid))) { error in
            if error != nil { DispatchQueue.main.async { fallback() } }
        }
    }

    func requestMute(uuid: UUID, muted: Bool, fallback: @escaping () -> Void) {
        controller.request(CXTransaction(action: CXSetMutedCallAction(call: uuid, muted: muted))) { error in
            if error != nil { DispatchQueue.main.async { fallback() } }
        }
    }

    /// Suspends until CallKit has activated the audio session (or `timeout` passes, so a call never hangs).
    func waitForAudio(timeout: TimeInterval = 6) async {
        if audioIsActive { return }
        await withTaskGroup(of: Void.self) { group in
            group.addTask { [weak self] in await withCheckedContinuation { c in self?.audioWaiters.append(c) } }
            group.addTask { try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000)) }
            await group.next()
            group.cancelAll()
        }
    }

    // MARK: CXProviderDelegate
    func providerDidReset(_ provider: CXProvider) { audioIsActive = false; onReset?() }

    func provider(_ provider: CXProvider, perform action: CXStartCallAction) {
        let update = CXCallUpdate()
        update.remoteHandle = action.handle
        update.localizedCallerName = "Hood"
        provider.reportCall(with: action.callUUID, updated: update)
        action.fulfill()
    }

    func provider(_ provider: CXProvider, perform action: CXAnswerCallAction) { onAnswer?(action.callUUID); action.fulfill() }
    func provider(_ provider: CXProvider, perform action: CXEndCallAction) { onEnd?(action.callUUID); action.fulfill() }
    func provider(_ provider: CXProvider, perform action: CXSetMutedCallAction) { onMute?(action.callUUID, action.isMuted); action.fulfill() }

    func provider(_ provider: CXProvider, didActivate audioSession: AVAudioSession) {
        do {
            try audioSession.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth])
            try AudioManager.shared.setEngineAvailability(.default)
        } catch { /* the call continues; LiveKit reports media errors through the room delegate */ }
        audioIsActive = true
        let waiters = audioWaiters; audioWaiters.removeAll()
        waiters.forEach { $0.resume() }
        onAudioActive?()
    }

    func provider(_ provider: CXProvider, didDeactivate audioSession: AVAudioSession) {
        audioIsActive = false
        try? AudioManager.shared.setEngineAvailability(.none)
    }
}
