import XCTest
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import HoodVoiceCore

final class ModelsDecodeServerFixtures: XCTestCase {
    func testStartCallDecodesTheRealServerReply() throws {
        let r = try decodeFixture(StartCallResponse.self, "start_call")
        XCTAssertTrue(r.call.callId.hasPrefix("call_"))
        XCTAssertEqual(r.call.direction, .ownerToHood)
        XCTAssertEqual(r.call.state, .connecting)
        XCTAssertEqual(r.call.language, "fr")
        XCTAssertEqual(r.join.room, r.call.callId)
        XCTAssertFalse(r.join.token.isEmpty)
        XCTAssertNotNil(r.limits?["max_call_seconds"])
    }

    func testAnswerReplyForAHoodInitiatedCall() throws {
        let r = try decodeFixture(StartCallResponse.self, "answer_call")
        XCTAssertEqual(r.call.direction, .hoodToOwner)
        XCTAssertEqual(r.call.state, .connecting)
        XCTAssertEqual(r.call.eventType, "decision_blocking")
        XCTAssertTrue(r.call.briefing?.hasPrefix("This is Hood") ?? false)
    }

    func testHomeDecodes() throws {
        let h = try decodeFixture(HomeData.self, "home")
        XCTAssertTrue(h.hood.voiceEnabled)
        XCTAssertEqual(h.activeMissions.count, 2)                     // RUNNING and AWAITING_PLAN_APPROVAL
        XCTAssertGreaterThanOrEqual(h.pendingConfirmations, 1)
        XCTAssertNotNil(h.activeCall)
        XCTAssertFalse(h.notifications.isEmpty)
        XCTAssertGreaterThan(h.spend.limits["monthly_usd"] ?? 0, 0)
    }

    func testChallengeConfirmationsSettingsAndPairing() throws {
        let c = try decodeFixture(Challenge.self, "challenge")
        XCTAssertEqual(c.algorithm, "ECDSA-P256-SHA256")
        let list = try decodeFixture(ConfirmationList.self, "confirmations")
        XCTAssertTrue(list.items.contains { $0.kind == "approval" && $0.phoneConfirmable })
        let s = try decodeFixture(HoodSettings.self, "settings")
        XCTAssertEqual(s.quietStart, "22:00")
        XCTAssertTrue(s.eventToggles["approval_required"] ?? false)
        let p = try decodeFixture(PairResult.self, "pair_result")
        XCTAssertTrue(p.deviceId.hasPrefix("dev_"))
        let r = try decodeFixture(SubmitResult.self, "submit_result")
        XCTAssertTrue(r.applied)
    }

    func testCallDetailEventsMergeIntoTheFeed() throws {
        let d = try decodeFixture(CallDetail.self, "call_detail")
        XCTAssertFalse(d.events.isEmpty)
        var feed = CallEventFeed()
        XCTAssertEqual(feed.ingest(d.events).count, d.events.count)
        XCTAssertEqual(feed.ingest(d.events).count, 0, "the same events must not be shown twice")
        XCTAssertEqual(feed.cursor, d.events.map(\.id).max())
    }
}

final class HoodAPIClientTests: XCTestCase {
    func testStartCallSendsTheDeviceCredentialAndNothingElse() async throws {
        let t = MockTransport(); try t.enqueue(fixture: "start_call")
        let api = HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t)
        let r = try await api.startCall(language: "fr")
        XCTAssertEqual(r.call.language, "fr")
        let req = try XCTUnwrap(t.requests.first)
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.url?.absoluteString, "http://hood.test:8990/api/voicecall/calls/start")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "HoodDevice dev_0123456789abcdef01234567.s3cret-value")
        let body = try JSONSerialization.jsonObject(with: try XCTUnwrap(req.httpBody)) as? [String: String]
        XCTAssertEqual(body, ["language": "fr"])
    }

    func testPairingSendsNoCredentialButTheCodeAndPublicKey() async throws {
        let t = MockTransport(); try t.enqueue(fixture: "pair_result")
        let key = Data([1, 2, 3, 4])
        let r = try await HoodAPIClient.pair(baseURL: testBase, code: "ABCDE23456", name: "My iPhone", publicKeySPKI: key,
                                             appVersion: "1.0", transport: t)
        XCTAssertTrue(r.deviceSecret.count > 10)
        let req = try XCTUnwrap(t.requests.first)
        XCTAssertNil(req.value(forHTTPHeaderField: "Authorization"))
        XCTAssertTrue(req.url!.absoluteString.hasSuffix("/api/voicecall/pair"))
        let body = try JSONSerialization.jsonObject(with: try XCTUnwrap(req.httpBody)) as? [String: String]
        XCTAssertEqual(body?["code"], "ABCDE23456"); XCTAssertEqual(body?["public_key"], key.base64EncodedString())
    }

    func testCallDetailUsesTheEventCursor() async throws {
        let t = MockTransport(); try t.enqueue(fixture: "call_detail")
        _ = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t).callDetail(callId: "call_x", afterEvent: 7)
        XCTAssertEqual(t.requests.first?.url?.query, "after=7")
        XCTAssertEqual(t.requests.first?.httpMethod, "GET")
        XCTAssertNil(t.requests.first?.httpBody)
    }

    func testSubmitSendsTheBase64Signature() async throws {
        let t = MockTransport(); try t.enqueue(fixture: "submit_result")
        let sig = Data([9, 8, 7])
        let r = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t)
            .submit(challengeId: "chl_1", signatureDER: sig)
        XCTAssertTrue(r.applied)
        let body = try JSONSerialization.jsonObject(with: try XCTUnwrap(t.requests.first?.httpBody)) as? [String: String]
        XCTAssertEqual(body?["challenge_id"], "chl_1"); XCTAssertEqual(body?["signature"], sig.base64EncodedString())
    }

    func testErrorStatusesMapToTypedErrors() async throws {
        let cases: [(Int, HoodAPIError)] = [
            (401, .unauthorized), (402, .budget("x")), (403, .forbidden("x")), (404, .notFound),
            (409, .conflict("x")), (429, .rateLimited("x")), (503, .unavailable("x")), (500, .server(500, "x"))]
        for (status, expected) in cases {
            let t = MockTransport(); t.enqueue(status: status, json: #"{"error":"x"}"#)
            do { _ = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t).home(); XCTFail("should throw for \(status)") }
            catch let e as HoodAPIError { XCTAssertEqual(e, expected, "status \(status)") }
        }
    }

    func testServerMessageIsSurfacedNotHidden() async {
        let t = MockTransport(); t.enqueue(status: 402, json: #"{"error":"Daily voice budget ($3.00) would be exceeded"}"#)
        do { _ = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t).startCall(); XCTFail() }
        catch { XCTAssertTrue((error as? HoodAPIError)?.errorDescription?.contains("Daily voice budget") ?? false) }
    }

    func testNetworkAndDecodingFailuresAreClassified() async throws {
        let t = MockTransport(); t.enqueue(error: URLError(.notConnectedToInternet))
        do { _ = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t).home(); XCTFail() }
        catch let e as HoodAPIError { if case .network = e { XCTAssertTrue(e.isTransient) } else { XCTFail("\(e)") } }
        let t2 = MockTransport(); t2.enqueue(status: 200, json: #"{"unexpected":true}"#)
        do { _ = try await HoodAPIClient(baseURL: testBase, credentials: testCreds, transport: t2).home(); XCTFail() }
        catch let e as HoodAPIError { if case .decoding = e { XCTAssertFalse(e.isTransient) } else { XCTFail("\(e)") } }
    }

    func testUnauthorizedIsNeverTransient() { XCTAssertFalse(HoodAPIError.unauthorized.isTransient) }
}
