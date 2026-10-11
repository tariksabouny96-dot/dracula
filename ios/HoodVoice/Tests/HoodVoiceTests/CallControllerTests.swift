import CallKit
import HoodVoiceCore
import XCTest
@testable import HoodVoice

// MARK: - fakes (SIMULATED: no CallKit, LiveKit or network; these test the controller's decisions, not iOS)

final class FakeCallKit: CallKitControlling {
    var onAnswer: ((UUID) -> Void)?
    var onEnd: ((UUID) -> Void)?
    var onMute: ((UUID, Bool) -> Void)?
    var onReset: (() -> Void)?

    var incoming: [(uuid: UUID, caller: String)] = []
    var incomingErrors: [Error?] = []              // consumed one per reportIncoming call
    var startAccepted = true
    var started: [UUID] = []
    var ended: [(uuid: UUID, reason: CXCallEndedReason)] = []
    var connected = 0
    var refuseEnd = false                          // iOS "does not know" the call: requestEnd calls the fallback
    var refuseMute = false

    func reportIncoming(uuid: UUID, caller: String, completion: @escaping (Error?) -> Void) {
        incoming.append((uuid, caller))
        completion(incomingErrors.isEmpty ? nil : incomingErrors.removeFirst())
    }
    func startOutgoing(uuid: UUID, name: String) async -> Bool { started.append(uuid); return startAccepted }
    func reportConnecting(uuid: UUID) {}
    func reportConnected(uuid: UUID) { connected += 1 }
    func reportEnded(uuid: UUID, reason: CXCallEndedReason) { ended.append((uuid, reason)) }
    func requestEnd(uuid: UUID, fallback: @escaping () -> Void) {
        if refuseEnd { fallback() } else { onEnd?(uuid) }
    }
    func requestMute(uuid: UUID, muted: Bool, fallback: @escaping () -> Void) {
        if refuseMute { fallback() } else { onMute?(uuid, muted) }
    }
    func requestAnswer(uuid: UUID, fallback: @escaping () -> Void) { onAnswer?(uuid) }
    func waitForAudio(timeout: TimeInterval) async {}
}

final class FakeRoom: RoomControlling {
    var onConnected: (() -> Void)?
    var onUnexpectedDisconnect: (() -> Void)?
    var onHoodEvent: (([String: Any]) -> Void)?
    var onTranscript: ((String, TranscriptLine.Speaker, String, Bool) -> Void)?
    var joined: [JoinGrant] = []
    var disconnects = 0
    var muted: [Bool] = []
    func connect(_ grant: JoinGrant) async throws { joined.append(grant) }
    func disconnect() async { disconnects += 1 }
    func setMuted(_ muted: Bool) async { self.muted.append(muted) }
}

final class FakeTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var routes: [String: (Int, String)] = [:]
    private var seen: [String] = []
    func on(_ key: String, status: Int = 200, json: String) { lock.lock(); routes[key] = (status, json); lock.unlock() }
    func count(_ key: String) -> Int { lock.lock(); defer { lock.unlock() }; return seen.filter { $0 == key }.count }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let key = "\(request.httpMethod ?? "GET") \(request.url!.path)"
        lock.lock(); seen.append(key); let route = routes[key] ?? (404, #"{"error":"no route"}"#); lock.unlock()
        return (Data(route.1.utf8), HTTPURLResponse(url: request.url!, statusCode: route.0, httpVersion: nil, headerFields: nil)!)
    }
}

// MARK: - helpers

func callId(_ n: Int) -> String { "call_" + String(format: "%020x", n) }

func b64url(_ s: String) -> String {
    Data(s.utf8).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}
/// A join-token-shaped string whose expiry the app can read (the signature is not checked by the app).
func fakeJWT(expiresIn seconds: TimeInterval, tag: String) -> String {
    "\(b64url(#"{"alg":"HS256"}"#)).\(b64url(#"{"exp":\#(Int(Date().timeIntervalSince1970 + seconds)),"tag":"\#(tag)"}"#)).sig"
}
func grantJSON(token: String, room: String) -> String {
    #"{"url":"ws://127.0.0.1:7880","token":"\#(token)","room":"\#(room)","identity":"dev_1","expires_at":2000000000}"#
}
func startJSON(id: String, direction: String = "owner_to_hood", state: String = "CONNECTING", token: String) -> String {
    #"{"call":{"call_id":"\#(id)","direction":"\#(direction)","state":"\#(state)","room":"\#(id)"},"join":\#(grantJSON(token: token, room: id))}"#
}
func ringPayload(id: String, token: String?) -> [AnyHashable: Any] {
    var p: [AnyHashable: Any] = ["type": "hood.incoming_call", "call_id": id, "caller": "Hood", "reason": "Disk is full"]
    if let token { p["join"] = ["url": "ws://127.0.0.1:7880", "token": token, "room": id, "identity": "dev_1", "expires_at": 2_000_000_000] }
    return p
}

@MainActor
func eventually(timeout: TimeInterval = 3, _ condition: @MainActor () -> Bool) async -> Bool {
    let end = Date().addingTimeInterval(timeout)
    while Date() < end {
        if condition() { return true }
        try? await Task.sleep(nanoseconds: 20_000_000)
    }
    return condition()
}

@MainActor
final class Harness {
    let callKit = FakeCallKit(), room = FakeRoom(), transport = FakeTransport()
    let controller: CallController
    init() {
        controller = CallController(callKit: callKit, room: room)
        let api = HoodAPIClient(baseURL: URL(string: "http://127.0.0.1:1")!,
                                credentials: DeviceCredentials(deviceId: "dev_1", secret: "not-a-real-secret"), transport: transport)
        controller.apiProvider = { api }
    }
    func isFailed() -> Bool { if case .failed = controller.phase { return true }; return false }
}

// MARK: - tests

@MainActor
final class CallControllerOwnerCallTests: XCTestCase {
    func testOwnerCallHappyPathThenHangUp() async {
        let h = Harness(), id = callId(1)
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: id, token: fakeJWT(expiresIn: 600, tag: "own")))
        h.transport.on("POST /api/voicecall/calls/\(id)/connected", json: #"{"state":"ACTIVE"}"#)
        h.transport.on("POST /api/voicecall/calls/\(id)/end",
                       json: #"{"call":{"call_id":"\#(id)","direction":"owner_to_hood","state":"ENDED","room":"\#(id)"}}"#)
        h.controller.pressCall()
        XCTAssertEqual(h.controller.phase, .requesting)
        let joined = await eventually { h.room.joined.count == 1 }
        XCTAssertTrue(joined)
        XCTAssertEqual(h.room.joined.first?.room, id)
        h.room.onConnected?()
        let active = await eventually { h.controller.phase == .active }
        XCTAssertTrue(active)
        let reported = await eventually { h.transport.count("POST /api/voicecall/calls/\(id)/connected") == 1 && h.callKit.connected == 1 }
        XCTAssertTrue(reported)
        h.controller.hangUp()
        let ended = await eventually { h.controller.phase == .ended(.userHangup) }
        XCTAssertTrue(ended)
        let told = await eventually { h.transport.count("POST /api/voicecall/calls/\(id)/end") == 1 && h.room.disconnects >= 1 }
        XCTAssertTrue(told)
        XCTAssertTrue(h.callKit.ended.isEmpty, "an end the user requested through CallKit is already complete")
    }

    func testIfIOSRefusesTheCallNoHoodSessionIsOpened() async {
        let h = Harness()
        h.callKit.startAccepted = false
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: callId(2), token: fakeJWT(expiresIn: 600, tag: "x")))
        h.controller.pressCall()
        let failed = await eventually { h.isFailed() }
        XCTAssertTrue(failed)
        XCTAssertEqual(h.transport.count("POST /api/voicecall/calls/start"), 0, "must not open a server call it cannot carry audio for")
        XCTAssertEqual(h.callKit.ended.first?.reason, .failed)
    }

    func testHangUpStillWorksWhenIOSDoesNotKnowTheCall() async {
        let h = Harness(), id = callId(3)
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: id, token: fakeJWT(expiresIn: 600, tag: "x")))
        h.transport.on("POST /api/voicecall/calls/\(id)/end", json: #"{"call":{"call_id":"\#(id)","direction":"owner_to_hood","state":"ENDED","room":"\#(id)"}}"#)
        h.controller.pressCall()
        let joined = await eventually { h.room.joined.count == 1 }
        XCTAssertTrue(joined)
        h.callKit.refuseEnd = true
        h.controller.hangUp()
        let ended = await eventually { h.controller.phase == .ended(.userHangup) }
        XCTAssertTrue(ended, "the Hang up button must never be dead")
    }

    func testMuteGoesThroughCallKitAndMutesTheMicrophone() async {
        let h = Harness()
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: callId(4), token: fakeJWT(expiresIn: 600, tag: "x")))
        h.controller.pressCall()
        let joined = await eventually { h.room.joined.count == 1 }
        XCTAssertTrue(joined)
        h.controller.toggleMute()
        let muted = await eventually { h.controller.isMuted && h.room.muted == [true] }
        XCTAssertTrue(muted)
    }

    func testTranscriptPartialsReplaceThemselvesUntilFinal() async {
        let h = Harness()
        h.room.onTranscript?("seg1", .hood, "Hel", false)
        h.room.onTranscript?("seg1", .hood, "Hello there", true)
        h.room.onTranscript?("seg1", .hood, "ignored late partial", false)
        let shown = await eventually { h.controller.transcript.count == 1 && h.controller.transcript[0].text == "Hello there" }
        XCTAssertTrue(shown)
        XCTAssertTrue(h.controller.transcript[0].isFinal)
    }

    func testServerEndingTheCallReachesThePhone() async {
        let h = Harness(), id = callId(5)
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: id, token: fakeJWT(expiresIn: 600, tag: "x")))
        h.transport.on("POST /api/voicecall/calls/\(id)/connected", json: #"{"state":"ACTIVE"}"#)
        h.transport.on("GET /api/voicecall/calls/\(id)",
                       json: #"{"call":{"call_id":"\#(id)","direction":"owner_to_hood","state":"ENDED","room":"\#(id)","end_reason":"max_duration"},"events":[]}"#)
        h.controller.pressCall()
        _ = await eventually { h.room.joined.count == 1 }
        h.room.onConnected?()
        let ended = await eventually(timeout: 8) { h.controller.phase == .ended(.limitReached) }   // the app polls every 3 s
        XCTAssertTrue(ended)
        XCTAssertGreaterThanOrEqual(h.room.disconnects, 1)
        XCTAssertEqual(h.callKit.ended.first?.reason, .remoteEnded)
        XCTAssertEqual(h.controller.banner, "The call reached its limit.")
    }
}

@MainActor
final class CallControllerIncomingCallTests: XCTestCase {
    func testRingIsReportedToCallKitOnceAndRepeatsDoNotDisturbIt() async {
        let h = Harness(), id = callId(10)
        var done = 0
        h.controller.handleIncomingPush(ringPayload(id: id, token: fakeJWT(expiresIn: 100, tag: "ring"))) { done += 1 }
        XCTAssertEqual(h.controller.phase, .ringing, "reported and ringing synchronously, as PushKit requires")
        XCTAssertEqual(h.callKit.incoming.count, 1)
        XCTAssertEqual(h.callKit.incoming[0].uuid, UUID.forHoodCall(id))
        XCTAssertEqual(h.controller.reason, "Disk is full")
        // the same ring delivered again: CallKit refuses a duplicate UUID; the live ring must survive that
        h.callKit.incomingErrors = [NSError(domain: CXErrorDomainIncomingCall, code: CXErrorCodeIncomingCallError.callUUIDAlreadyExists.rawValue)]
        h.controller.handleIncomingPush(ringPayload(id: id, token: fakeJWT(expiresIn: 100, tag: "ring"))) { done += 1 }
        let both = await eventually { done == 2 }
        XCTAssertTrue(both, "PushKit's completion handler is called for every push")
        XCTAssertEqual(h.controller.phase, .ringing)
        XCTAssertTrue(h.callKit.ended.isEmpty)
    }

    func testAnUnreadablePushStillReportsACallAndEndsItAtOnce() async {
        let h = Harness()
        var done = 0
        h.controller.handleIncomingPush(["type": "hood.incoming_call", "call_id": "../../etc/passwd"]) { done += 1 }
        let finished = await eventually { done == 1 }
        XCTAssertTrue(finished)
        XCTAssertEqual(h.callKit.incoming.count, 1, "iOS requires every VoIP push to be reported as a call")
        XCTAssertEqual(h.callKit.ended.count, 1)
        XCTAssertEqual(h.controller.phase, .idle, "and it never becomes a Hood call")
    }

    func testRingWhileAlreadyOnACallIsTurnedAwayWithoutDisturbingIt() async {
        let h = Harness()
        h.transport.on("POST /api/voicecall/calls/start", json: startJSON(id: callId(11), token: fakeJWT(expiresIn: 600, tag: "x")))
        h.controller.pressCall()
        _ = await eventually { h.room.joined.count == 1 }
        var done = 0
        let other = callId(12)
        h.controller.handleIncomingPush(ringPayload(id: other, token: nil)) { done += 1 }
        let finished = await eventually { done == 1 && h.callKit.ended.contains(where: { $0.uuid == UUID.forHoodCall(other) }) }
        XCTAssertTrue(finished)
        XCTAssertEqual(h.controller.phase, .connecting, "the call in progress is untouched")
    }

    func testAcceptJoinsWithTheGrantHoodReturns() async {
        let h = Harness(), id = callId(13)
        h.transport.on("POST /api/voicecall/calls/\(id)/answer",
                       json: startJSON(id: id, direction: "hood_to_owner", token: fakeJWT(expiresIn: 600, tag: "from-api")))
        h.controller.handleIncomingPush(ringPayload(id: id, token: fakeJWT(expiresIn: 100, tag: "from-push"))) {}
        h.controller.accept()
        let joined = await eventually { h.room.joined.count == 1 }
        XCTAssertTrue(joined)
        XCTAssertEqual(JoinTokenInspector.expiry(of: h.room.joined[0].token).map { $0.timeIntervalSinceNow > 300 }, true,
                       "the long-lived grant from the answer call, not the 2-minute ring grant")
    }

    func testAcceptFallsBackToTheRingGrantWhenHoodsAPIIsUnreachable() async {
        let h = Harness(), id = callId(14)
        let ringToken = fakeJWT(expiresIn: 100, tag: "from-push")
        h.transport.on("POST /api/voicecall/calls/\(id)/answer", status: 503, json: #"{"error":"unavailable"}"#)
        h.controller.handleIncomingPush(ringPayload(id: id, token: ringToken)) {}
        h.controller.accept()
        let joined = await eventually { h.room.joined.count == 1 }
        XCTAssertTrue(joined)
        XCTAssertEqual(h.room.joined[0].token, ringToken)
    }

    func testAcceptFailsCleanlyWhenThereIsNoUsableGrantAtAll() async {
        let h = Harness(), id = callId(15)
        h.transport.on("POST /api/voicecall/calls/\(id)/answer", status: 503, json: #"{"error":"unavailable"}"#)
        h.controller.handleIncomingPush(ringPayload(id: id, token: fakeJWT(expiresIn: -30, tag: "expired"))) {}
        h.controller.accept()
        let failed = await eventually { h.isFailed() }
        XCTAssertTrue(failed)
        XCTAssertTrue(h.room.joined.isEmpty)
        XCTAssertEqual(h.callKit.ended.last?.reason, .failed, "the system call UI is cleared, not left ringing or 'in call'")
    }

    func testDecliningTellsHoodAndLeavesNoCallUIBehind() async {
        let h = Harness(), id = callId(16)
        h.transport.on("POST /api/voicecall/calls/\(id)/decline",
                       json: #"{"call":{"call_id":"\#(id)","direction":"hood_to_owner","state":"DECLINED","room":"\#(id)"}}"#)
        h.controller.handleIncomingPush(ringPayload(id: id, token: nil)) {}
        h.controller.hangUp()
        let ended = await eventually { h.controller.phase == .ended(.declined) }
        XCTAssertTrue(ended)
        let told = await eventually { h.transport.count("POST /api/voicecall/calls/\(id)/decline") == 1 }
        XCTAssertTrue(told)
        XCTAssertTrue(h.room.joined.isEmpty)
    }

    func testARingIOSRefusesToShowCountsAsAMissedCall() async {
        let h = Harness(), id = callId(17)
        h.callKit.incomingErrors = [NSError(domain: "test", code: 1)]            // e.g. filtered by Do Not Disturb
        var done = 0
        h.controller.handleIncomingPush(ringPayload(id: id, token: nil)) { done += 1 }
        let missed = await eventually { h.controller.phase == .ended(.missed) && done == 1 }
        XCTAssertTrue(missed)
        XCTAssertEqual(h.controller.banner, "Missed call from Hood.")
    }
}
