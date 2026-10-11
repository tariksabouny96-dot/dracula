import CallKit
import HoodVoiceCore
import XCTest
@testable import HoodVoice

@MainActor
final class AppModelTests: XCTestCase {
    func testServerAddressIsNormalised() {
        XCTAssertEqual(AppModel.normalizedServerURL("hood.example.com")?.absoluteString, "https://hood.example.com")
        XCTAssertEqual(AppModel.normalizedServerURL("  http://192.168.1.20:8080/ui?x=1  ")?.absoluteString, "http://192.168.1.20:8080")
        XCTAssertEqual(AppModel.normalizedServerURL("https://hood.example.com/some/path")?.absoluteString, "https://hood.example.com")
    }

    func testServerAddressRejectsGarbageAndOtherSchemes() {
        XCTAssertNil(AppModel.normalizedServerURL(""))
        XCTAssertNil(AppModel.normalizedServerURL("   "))
        XCTAssertNil(AppModel.normalizedServerURL("ftp://hood.example.com"))
        XCTAssertNil(AppModel.normalizedServerURL("javascript:alert(1)"))
    }
}

@MainActor
final class CallIdentityTests: XCTestCase {
    func testSameHoodCallAlwaysMapsToTheSameCallKitUUID() {
        let a = UUID.forHoodCall("call_0123456789abcdef0123")
        XCTAssertEqual(a, UUID.forHoodCall("call_0123456789abcdef0123"))
        XCTAssertNotEqual(a, UUID.forHoodCall("call_0123456789abcdef0124"))
    }

    func testEndReasonsMapToCallKitReasons() {
        XCTAssertEqual(CallController.callKitReason(for: .missed), .unanswered)
        XCTAssertEqual(CallController.callKitReason(for: .networkLost), .failed)
        XCTAssertEqual(CallController.callKitReason(for: .remoteHangup), .remoteEnded)
        XCTAssertEqual(CallController.callKitReason(for: .limitReached), .remoteEnded)
        // ends the user requested through CallKit are already complete; reporting them again would be wrong
        XCTAssertNil(CallController.callKitReason(for: .userHangup))
        XCTAssertNil(CallController.callKitReason(for: .declined))
    }
}

final class PushPayloadAppIntegrationTests: XCTestCase {
    func testMalformedRingPayloadIsNeverTreatedAsACall() {
        XCTAssertEqual(PushPayload.parse(["type": "hood.incoming_call", "call_id": "../../etc"]), .unknown)
        XCTAssertEqual(PushPayload.parse([:]), .unknown)
    }
}
