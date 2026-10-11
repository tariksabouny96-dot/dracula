import XCTest
@testable import HoodVoiceCore

final class PushPayloadTests: XCTestCase {
    private let cid = "call_" + String(repeating: "ab12", count: 5)       // 25 chars like the server's call ids

    func testIncomingCallPayloadFromTheServerIsParsed() {
        let p = PushPayload.parse(["aps": ["content-available": 1], "type": "hood.incoming_call", "call_id": cid, "caller": "Hood",
                                   "reason": "A decision is blocking a mission",
                                   "join": ["url": "wss://x.livekit.cloud", "token": "t.t.t", "room": cid, "identity": "dev_1", "expires_at": 1790000120.5]])
        guard case .incomingCall(let c) = p else { return XCTFail("\(p)") }
        XCTAssertEqual(c.callId, cid); XCTAssertEqual(c.caller, "Hood"); XCTAssertEqual(c.join?.room, cid)
        XCTAssertEqual(c.join?.expiresAt, 1790000120.5)
    }

    func testMalformedOrHostileIncomingCallsNeverBecomeCalls() {
        XCTAssertEqual(PushPayload.parse(["type": "hood.incoming_call"]), .unknown)                              // no id
        XCTAssertEqual(PushPayload.parse(["type": "hood.incoming_call", "call_id": "call_../../etc"]), .unknown)
        XCTAssertEqual(PushPayload.parse(["type": "hood.incoming_call", "call_id": "CALL_" + String(repeating: "A", count: 20)]), .unknown)
        XCTAssertEqual(PushPayload.parse(["type": "something.else", "call_id": cid]), .unknown)
        XCTAssertEqual(PushPayload.parse([:]), .unknown)
    }

    func testJoinGrantWithABadSchemeIsDroppedButTheRingStillParses() {
        let p = PushPayload.parse(["type": "hood.incoming_call", "call_id": cid,
                                   "join": ["url": "https://evil", "token": "t", "room": cid, "identity": "d", "expires_at": 1.0]])
        guard case .incomingCall(let c) = p else { return XCTFail() }
        XCTAssertNil(c.join, "only ws/wss rooms are accepted")
    }

    func testLongTextIsClippedAndMissedCallNotificationCarriesTheCallbackAction() {
        let p = PushPayload.parse(["type": "hood.incoming_call", "call_id": cid, "caller": String(repeating: "x", count: 500),
                                   "reason": String(repeating: "y", count: 500)])
        guard case .incomingCall(let c) = p else { return XCTFail() }
        XCTAssertEqual(c.caller.count, 40); XCTAssertEqual(c.reason.count, 140)
        let n = PushPayload.parse(["type": "hood.notification", "call_id": cid, "event_id": "evt_1", "action": "callback"])
        XCTAssertEqual(n, .notification(NotificationPush(eventId: "evt_1", callId: cid, action: "callback")))
    }
}
