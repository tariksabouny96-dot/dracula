import XCTest
@testable import HoodVoiceCore

final class CallStateMachineTests: XCTestCase {
    let grant = JoinGrant(url: "wss://x", token: "t", room: "call_1", identity: "dev", expiresAt: 9e9)
    let t0 = Date(timeIntervalSince1970: 1_000)

    func testOwnerCallHappyPath() {
        var m = CallStateMachine()
        XCTAssertEqual(m.send(.pressCall), [.apiStart]); XCTAssertEqual(m.phase, .requesting)
        XCTAssertEqual(m.send(.startSucceeded(grant, callId: "call_1")), [.connectRoom(grant)]); XCTAssertEqual(m.phase, .connecting)
        XCTAssertEqual(m.send(.roomConnected), [.apiMarkConnected(callId: "call_1")]); XCTAssertEqual(m.phase, .active)
        XCTAssertEqual(m.send(.userEnded), [.disconnectRoom, .apiEnd(callId: "call_1"), .reportEnded(.userHangup)])
        XCTAssertEqual(m.phase, .ended(.userHangup))
    }

    func testStartFailureIsShownAndRecoverable() {
        var m = CallStateMachine()
        _ = m.send(.pressCall)
        XCTAssertEqual(m.send(.startFailed("Daily voice budget reached")), [.notify("Daily voice budget reached")])
        XCTAssertEqual(m.phase, .failed("Daily voice budget reached"))
        XCTAssertEqual(m.send(.pressCall), [.apiStart], "the owner can try again")
    }

    func testIncomingCallAccept() {
        var m = CallStateMachine()
        XCTAssertEqual(m.send(.incomingCall(callId: "call_2")), []); XCTAssertEqual(m.phase, .ringing)
        XCTAssertEqual(m.send(.userAnswered), [.apiAnswer(callId: "call_2")])
        XCTAssertEqual(m.send(.answerSucceeded(grant)), [.connectRoom(grant)])
        XCTAssertEqual(m.send(.roomConnected), [.apiMarkConnected(callId: "call_2")]); XCTAssertEqual(m.phase, .active)
    }

    func testIncomingCallDeclineAndMissed() {
        var m = CallStateMachine()
        _ = m.send(.incomingCall(callId: "call_3"))
        XCTAssertEqual(m.send(.userDeclined), [.apiDecline(callId: "call_3"), .reportEnded(.declined)]); XCTAssertEqual(m.phase, .ended(.declined))
        var n = CallStateMachine()
        _ = n.send(.incomingCall(callId: "call_4"))
        XCTAssertEqual(n.send(.ringTimeout), [.reportEnded(.missed)]); XCTAssertEqual(n.phase, .ended(.missed))
    }

    func testAnswerFailureClearsTheSystemCallScreen() {
        var m = CallStateMachine()
        _ = m.send(.incomingCall(callId: "call_5")); _ = m.send(.userAnswered)
        let fx = m.send(.answerFailed("This call already ended"))
        XCTAssertTrue(fx.contains(.reportEnded(.error)), "CallKit must not be left showing a dead call")
        XCTAssertEqual(m.phase, .failed("This call already ended"))
    }

    func testDroppedMediaTriggersRejoinWithBackoffThenRecovers() {
        var m = CallStateMachine(); _ = m.send(.tick(t0))
        _ = m.send(.pressCall); _ = m.send(.startSucceeded(grant, callId: "call_6")); _ = m.send(.roomConnected)
        XCTAssertEqual(m.send(.roomDisconnected), [.apiRejoin(callId: "call_6", after: 1)]); XCTAssertEqual(m.phase, .reconnecting(attempt: 1))
        XCTAssertEqual(m.send(.rejoinSucceeded(grant)), [.connectRoom(grant)])
        XCTAssertEqual(m.send(.roomConnected), [.apiMarkConnected(callId: "call_6")]); XCTAssertEqual(m.phase, .active)
    }

    func testReconnectGivesUpAfterTheAttemptsAreSpent() {
        var m = CallStateMachine(); _ = m.send(.tick(t0))
        _ = m.send(.pressCall); _ = m.send(.startSucceeded(grant, callId: "call_7")); _ = m.send(.roomConnected); _ = m.send(.roomDisconnected)
        var delays: [TimeInterval] = []
        var last: [CallEffect] = []
        for _ in 0..<10 {
            last = m.send(.rejoinFailed("offline"))
            if case .apiRejoin(_, let d)? = last.first { delays.append(d) } else { break }
        }
        XCTAssertEqual(delays, [2, 4, 8, 15], "exponential backoff, capped")
        XCTAssertEqual(m.phase, .ended(.networkLost))
        XCTAssertTrue(last.contains(.apiEnd(callId: "call_7")) && last.contains(.disconnectRoom) && last.contains(.reportEnded(.networkLost)))
    }

    func testReconnectWindowMatchesTheServersSixtySeconds() {
        var m = CallStateMachine(); _ = m.send(.tick(t0))
        _ = m.send(.pressCall); _ = m.send(.startSucceeded(grant, callId: "call_8")); _ = m.send(.roomConnected); _ = m.send(.roomDisconnected)
        XCTAssertEqual(m.send(.tick(t0.addingTimeInterval(59))), [])
        let fx = m.send(.tick(t0.addingTimeInterval(61)))
        XCTAssertEqual(m.phase, .ended(.networkLost)); XCTAssertTrue(fx.contains(.apiEnd(callId: "call_8")))
    }

    func testServerEndedMapsReasonsAndNeverCallsEndAgain() {
        for (reason, cause) in [("max_duration", EndCause.limitReached), ("budget", .limitReached), ("hood_hangup", .remoteHangup),
                                ("worker_lost", .networkLost), ("device_revoked", .remoteHangup)] {
            var m = CallStateMachine()
            _ = m.send(.pressCall); _ = m.send(.startSucceeded(grant, callId: "c")); _ = m.send(.roomConnected)
            let fx = m.send(.serverEnded(reason: reason))
            XCTAssertEqual(m.phase, .ended(cause), reason)
            XCTAssertTrue(fx.contains(.disconnectRoom) && fx.contains(.reportEnded(cause)))
            XCTAssertFalse(fx.contains(.apiEnd(callId: "c")), "the server already ended it")
        }
    }

    func testLateAndDuplicateEventsAreIgnored() {
        var m = CallStateMachine()
        XCTAssertEqual(m.send(.roomConnected), []); XCTAssertEqual(m.send(.userEnded), []); XCTAssertEqual(m.phase, .idle)
        _ = m.send(.pressCall); _ = m.send(.startSucceeded(grant, callId: "c")); _ = m.send(.roomConnected); _ = m.send(.userEnded)
        XCTAssertEqual(m.send(.roomDisconnected), [], "a disconnect after we hung up must not start a reconnect")
        XCTAssertEqual(m.send(.serverEnded(reason: "owner_hangup")), [])
    }

    func testUserCanCancelWhileRequestingOrRinging() {
        var m = CallStateMachine(); _ = m.send(.pressCall)
        XCTAssertEqual(m.send(.userEnded), [.reportEnded(.userHangup)])
        var n = CallStateMachine(); _ = n.send(.incomingCall(callId: "c"))
        XCTAssertEqual(n.send(.serverEnded(reason: "no_answer")), [.reportEnded(.missed)])
    }
}

final class TranscriptBufferTests: XCTestCase {
    func testPartialsReplaceThemselvesAndFinalsAreFrozen() {
        var b = TranscriptBuffer()
        b.apply(id: "1", speaker: .owner, text: "what is", isFinal: false)
        b.apply(id: "1", speaker: .owner, text: "what is the status", isFinal: true)
        b.apply(id: "1", speaker: .owner, text: "late partial must not overwrite", isFinal: false)
        XCTAssertEqual(b.lines.count, 1); XCTAssertEqual(b.lines[0].text, "what is the status"); XCTAssertTrue(b.lines[0].isFinal)
    }
    func testEmptyTextIsDroppedAndTheBufferIsBounded() {
        var b = TranscriptBuffer(limit: 3)
        b.apply(id: "e", speaker: .hood, text: "   ", isFinal: true)
        XCTAssertTrue(b.lines.isEmpty)
        for i in 0..<10 { b.apply(id: "\(i)", speaker: .hood, text: "line \(i)", isFinal: true) }
        XCTAssertEqual(b.lines.map(\.id), ["7", "8", "9"])
        b.clear(); XCTAssertTrue(b.lines.isEmpty)
    }
}
