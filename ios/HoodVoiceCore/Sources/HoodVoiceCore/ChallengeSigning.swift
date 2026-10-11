import Foundation
#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif

/// Signs the canonical challenge text. On the iPhone this is a Secure Enclave key that needs Face ID; here a
/// software key is provided for tests and for devices without a Secure Enclave (never in production builds
/// that handle real decisions unless explicitly chosen).
public protocol ChallengeSigner {
    /// SPKI DER public key (what Hood stores when the phone is paired).
    func publicKeySPKI() throws -> Data
    /// ECDSA P-256 / SHA-256 signature, DER encoded.
    func sign(_ message: Data) throws -> Data
}

public enum ChallengeError: Error, Equatable, LocalizedError {
    case textDoesNotMatchFields
    case expired
    case wrongDevice
    public var errorDescription: String? {
        switch self {
        case .textDoesNotMatchFields: return "Hood sent text that does not match the decision shown. Nothing was signed."
        case .expired: return "This confirmation expired. Ask again."
        case .wrongDevice: return "This confirmation was issued for another device."
        }
    }
}

extension Challenge {
    /// The text the device signs. It is rebuilt from the individual fields, so a server-supplied
    /// `signingString` that differs (i.e. text we were not shown) is refused instead of signed blindly.
    public var canonicalSigningString: String {
        ["hood-confirm-v1", challengeId, deviceId, kind, subject, decision, subjectHash, nonce, String(Int(expires))]
            .joined(separator: "\n")
    }

    public func validate(forDevice deviceId: String, now: Date = Date()) throws {
        guard signingString == canonicalSigningString else { throw ChallengeError.textDoesNotMatchFields }
        guard self.deviceId == deviceId else { throw ChallengeError.wrongDevice }
        guard now.timeIntervalSince1970 < expires else { throw ChallengeError.expired }
    }

    /// Validate, then sign the canonical text with the supplied (biometric-gated) signer.
    public func signature(using signer: ChallengeSigner, forDevice deviceId: String, now: Date = Date()) throws -> Data {
        try validate(forDevice: deviceId, now: now)
        return try signer.sign(Data(canonicalSigningString.utf8))
    }
}

/// Software P-256 signer: tests, simulators and the (optional) fallback for devices without a Secure Enclave.
public struct SoftwareP256Signer: ChallengeSigner {
    public let privateKey: P256.Signing.PrivateKey      // gitleaks:allow (a type name, not a secret)
    public init(privateKey: P256.Signing.PrivateKey = P256.Signing.PrivateKey()) { self.privateKey = privateKey }   // gitleaks:allow
    public func publicKeySPKI() throws -> Data { privateKey.publicKey.derRepresentation }
    public func sign(_ message: Data) throws -> Data { try privateKey.signature(for: message).derRepresentation }
}
