import XCTest
#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif
@testable import HoodVoiceCore

final class ChallengeSigningTests: XCTestCase {
    func testCanonicalTextEqualsWhatTheServerProduced() throws {
        // challenge.json came from the Python server: its own `signing_string` must equal the string the app
        // rebuilds from the individual fields. This is the contract that makes signatures verifiable.
        let c = try decodeFixture(Challenge.self, "challenge")
        XCTAssertEqual(c.canonicalSigningString, c.signingString)
        XCTAssertTrue(c.canonicalSigningString.hasPrefix("hood-confirm-v1\nchl_"))
    }

    func testValidateRefusesTextTheUserWasNotShown() throws {
        let c = try decodeFixture(Challenge.self, "challenge")
        let before = Date(timeIntervalSince1970: c.expires - 30)
        XCTAssertNoThrow(try c.validate(forDevice: c.deviceId, now: before))
        let tampered = Challenge(challengeId: c.challengeId, deviceId: c.deviceId, kind: c.kind, subject: c.subject,
                                 decision: "approve", subjectHash: c.subjectHash, nonce: c.nonce, expires: c.expires,
                                 signingString: c.signingString.replacingOccurrences(of: "approve", with: "reject"),
                                 title: c.title, detail: c.detail, algorithm: c.algorithm)
        XCTAssertThrowsError(try tampered.validate(forDevice: c.deviceId, now: before)) { XCTAssertEqual($0 as? ChallengeError, .textDoesNotMatchFields) }
        XCTAssertThrowsError(try c.validate(forDevice: "dev_someone_else", now: before)) { XCTAssertEqual($0 as? ChallengeError, .wrongDevice) }
        XCTAssertThrowsError(try c.validate(forDevice: c.deviceId, now: Date(timeIntervalSince1970: c.expires + 1))) { XCTAssertEqual($0 as? ChallengeError, .expired) }
    }

    func testSoftwareSignerProducesAVerifiableDERSignature() throws {
        let c = try decodeFixture(Challenge.self, "challenge")
        let signer = SoftwareP256Signer()
        let sig = try c.signature(using: signer, forDevice: c.deviceId, now: Date(timeIntervalSince1970: c.expires - 5))
        let pub = try P256.Signing.PublicKey(derRepresentation: try signer.publicKeySPKI())
        let parsed = try P256.Signing.ECDSASignature(derRepresentation: sig)
        XCTAssertTrue(pub.isValidSignature(parsed, for: Data(c.canonicalSigningString.utf8)))
        XCTAssertFalse(pub.isValidSignature(parsed, for: Data("something else".utf8)))
    }

    func testSignatureFromThePythonServerSideVerifiesInSwift() throws {
        // python_signed_vector.json was signed by Python's `cryptography`; Swift must accept it.
        struct Vec: Decodable { let fields: [String: JSONValue]; let publicKeySpkiB64: String; let signingString: String; let signatureDerB64: String }
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        let v = try d.decode(Vec.self, from: try fixtureData("python_signed_vector"))
        func s(_ k: String) -> String { v.fields[k]?.stringValue ?? "" }
        guard case .number(let exp)? = v.fields["expires"] else { return XCTFail("expires") }
        let c = Challenge(challengeId: s("challenge_id"), deviceId: s("device_id"), kind: s("kind"), subject: s("subject"),
                          decision: s("decision"), subjectHash: s("subject_hash"), nonce: s("nonce"), expires: exp,
                          signingString: v.signingString, title: "", detail: "", algorithm: "ECDSA-P256-SHA256")
        XCTAssertEqual(c.canonicalSigningString, v.signingString, "Swift and Python must build identical canonical text (incl. float expiry)")
        let pub = try P256.Signing.PublicKey(derRepresentation: Data(base64Encoded: v.publicKeySpkiB64)!)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: Data(base64Encoded: v.signatureDerB64)!)
        XCTAssertTrue(pub.isValidSignature(sig, for: Data(v.signingString.utf8)))
    }

    /// Writes a Swift-signed vector for the Python test (`test_swift_signed_vector_verifies_on_the_server_side`).
    /// Run with HOOD_WRITE_INTEROP=/path/to/swift_signed_vector.json
    func testWriteSwiftSignedVectorForPython() throws {
        guard let out = ProcessInfo.processInfo.environment["HOOD_WRITE_INTEROP"] else { throw XCTSkip("set HOOD_WRITE_INTEROP to write the vector") }
        let signer = SoftwareP256Signer()
        let fields: [String: Any] = ["challenge_id": "chl_ffeeddccbbaa99887766aa55", "device_id": "dev_aabbccddeeff001122334455",
                                     "kind": "mission_plan", "subject": "agm_" + String(repeating: "7", count: 32), "decision": "approve",
                                     "subject_hash": String(repeating: "ef", count: 32), "nonce": String(repeating: "12", count: 16),
                                     "expires": 1790000090.5]
        let c = Challenge(challengeId: fields["challenge_id"] as! String, deviceId: fields["device_id"] as! String, kind: "mission_plan",
                          subject: fields["subject"] as! String, decision: "approve", subjectHash: fields["subject_hash"] as! String,
                          nonce: fields["nonce"] as! String, expires: 1790000090.5, signingString: "", title: "", detail: "", algorithm: "")
        let text = c.canonicalSigningString
        let sig = try signer.sign(Data(text.utf8))
        let vector: [String: Any] = ["fields": fields, "public_key_spki_b64": try signer.publicKeySPKI().base64EncodedString(),
                                     "signing_string": text, "signature_der_b64": sig.base64EncodedString()]
        try JSONSerialization.data(withJSONObject: vector, options: [.prettyPrinted, .sortedKeys]).write(to: URL(fileURLWithPath: out))
    }
}

final class JoinTokenInspectorTests: XCTestCase {
    private func token(exp: Double) -> String {
        func b64(_ d: Data) -> String { d.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
        let head = b64(Data(#"{"alg":"HS256"}"#.utf8)), body = b64(Data(#"{"exp":\#(Int(exp))}"#.utf8))
        return "\(head).\(body).sig"
    }
    func testExpiryIsReadWithoutVerifyingAndUsabilityHasAMargin() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        let g = JoinGrant(url: "wss://x", token: token(exp: 1_000_100), room: "r", identity: "i", expiresAt: 0)
        XCTAssertEqual(JoinTokenInspector.expiry(of: g.token), Date(timeIntervalSince1970: 1_000_100))
        XCTAssertTrue(JoinTokenInspector.isUsable(g, now: now))
        XCTAssertFalse(JoinTokenInspector.isUsable(g, now: Date(timeIntervalSince1970: 1_000_095)), "inside the 10 s margin")
        XCTAssertNil(JoinTokenInspector.expiry(of: "not-a-jwt"))
    }
    func testTokensAreRedactedForLogs() {
        XCTAssertEqual(JoinTokenInspector.redacted("abcdefghijklmnopqrstuvwxyz"), "abcd…wxyz")
        XCTAssertEqual(JoinTokenInspector.redacted("short"), "<token>")
    }
}
