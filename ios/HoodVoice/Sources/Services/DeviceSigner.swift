import Foundation
import CryptoKit
import LocalAuthentication
import HoodVoiceCore

/// The phone's decision-signing key. On a real iPhone it is a Secure Enclave key whose use requires Face ID /
/// Touch ID *for every signature* (`.biometryCurrentSet`: it is invalidated if the enrolled biometrics change,
/// and the phone must be re-paired). Simulators and devices without a Secure Enclave get a software key that is
/// gated by the device passcode/biometrics in code; Settings shows which kind is in use.
final class DeviceKeyManager {
    enum Kind: String { case secureEnclave, software }

    private let keychain = KeychainStore()
    private(set) var kind: Kind = .software

    var isSecureEnclaveAvailable: Bool { SecureEnclave.isAvailable }

    /// Returns the existing signer or creates one. Pairing sends `publicKeySPKI()` to Hood.
    func signer(reason: String = "Confirm this decision with Hood") throws -> ChallengeSigner {
        if let stored = keychain.data(for: .signerKey), let kindRaw = keychain.string(for: .signerKind), let k = Kind(rawValue: kindRaw) {
            kind = k
            return try load(kind: k, data: stored, reason: reason)
        }
        return try create(reason: reason)
    }

    func hasKey() -> Bool { keychain.data(for: .signerKey) != nil }

    func reset() { keychain.remove(.signerKey); keychain.remove(.signerKind) }

    private func create(reason: String) throws -> ChallengeSigner {
        if SecureEnclave.isAvailable {
            var error: Unmanaged<CFError>?
            guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly,
                                                               [.privateKeyUsage, .biometryCurrentSet], &error) else {
                throw error!.takeRetainedValue() as Error
            }
            let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access)
            try keychain.set(key.dataRepresentation, for: .signerKey)
            try keychain.set(Kind.secureEnclave.rawValue, for: .signerKind)
            kind = .secureEnclave
            return SecureEnclaveSigner(key: key)
        }
        let key = P256.Signing.PrivateKey()
        try keychain.set(key.rawRepresentation, for: .signerKey)
        try keychain.set(Kind.software.rawValue, for: .signerKind)
        kind = .software
        return PresenceGatedSigner(inner: SoftwareP256Signer(privateKey: key), reason: reason)
    }

    private func load(kind: Kind, data: Data, reason: String) throws -> ChallengeSigner {
        switch kind {
        case .secureEnclave:
            let context = LAContext()
            context.localizedReason = reason
            return SecureEnclaveSigner(key: try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: data, authenticationContext: context))
        case .software:
            return PresenceGatedSigner(inner: SoftwareP256Signer(privateKey: try P256.Signing.PrivateKey(rawRepresentation: data)), reason: reason)
        }
    }
}

struct SecureEnclaveSigner: ChallengeSigner {
    let key: SecureEnclave.P256.Signing.PrivateKey
    func publicKeySPKI() throws -> Data { key.publicKey.derRepresentation }
    /// Triggers the Face ID / Touch ID prompt.
    func sign(_ message: Data) throws -> Data { try key.signature(for: message).derRepresentation }
}

/// Software key, but never used without the owner being present (biometrics or passcode).
struct PresenceGatedSigner: ChallengeSigner {
    let inner: SoftwareP256Signer
    let reason: String
    func publicKeySPKI() throws -> Data { try inner.publicKeySPKI() }
    func sign(_ message: Data) throws -> Data {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { throw error ?? SignerError.noAuthentication }
        let semaphore = DispatchSemaphore(value: 0)
        var allowed = false
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, _ in allowed = ok; semaphore.signal() }
        semaphore.wait()
        guard allowed else { throw SignerError.cancelled }
        return try inner.sign(message)
    }
}

enum SignerError: Error, LocalizedError {
    case noAuthentication, cancelled
    var errorDescription: String? {
        switch self {
        case .noAuthentication: return "Set a passcode or Face ID on this phone to confirm decisions."
        case .cancelled: return "Confirmation cancelled."
        }
    }
}
