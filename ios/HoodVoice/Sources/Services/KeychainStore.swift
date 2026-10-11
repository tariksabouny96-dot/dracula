import Foundation
import Security

/// Small Keychain wrapper. Items are readable after the first unlock (a VoIP push can arrive while the phone
/// is locked) and never leave this device or its backups.
struct KeychainStore {
    enum Key: String { case serverURL, deviceId, deviceSecret, signerKind, signerKey }
    private let service = "com.hoodx.voice"

    func set(_ data: Data, for key: Key) throws {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                   kSecAttrAccount as String: key.rawValue]
        SecItemDelete(base as CFDictionary)
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError.status(status) }
    }

    func set(_ string: String, for key: Key) throws { try set(Data(string.utf8), for: key) }

    func data(for key: Key) -> Data? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: key.rawValue, kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    func string(for key: Key) -> String? { data(for: key).flatMap { String(data: $0, encoding: .utf8) } }

    func remove(_ key: Key) {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                       kSecAttrAccount as String: key.rawValue] as CFDictionary)
    }

    func removeAll() { [Key.serverURL, .deviceId, .deviceSecret, .signerKind, .signerKey].forEach(remove) }
}

enum KeychainError: Error, LocalizedError {
    case status(OSStatus)
    var errorDescription: String? { if case .status(let s) = self { return "Keychain error \(s)" }; return nil }
}
