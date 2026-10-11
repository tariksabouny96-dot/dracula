import Foundation

/// Reads the expiry out of a LiveKit join token (a JWT) WITHOUT verifying it (only the server can). Used to
/// decide whether to ask Hood for a fresh token before reconnecting. Never log the token itself.
public enum JoinTokenInspector {
    public static func expiry(of token: String) -> Date? {
        let parts = token.split(separator: ".")
        guard parts.count == 3 else { return nil }
        var b64 = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        b64 += String(repeating: "=", count: (4 - b64.count % 4) % 4)
        guard let data = Data(base64Encoded: b64),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let exp = obj["exp"] as? Double else { return nil }
        return Date(timeIntervalSince1970: exp)
    }

    public static func isUsable(_ grant: JoinGrant, now: Date = Date(), margin: TimeInterval = 10) -> Bool {
        let exp = expiry(of: grant.token) ?? Date(timeIntervalSince1970: grant.expiresAt)
        return exp.timeIntervalSince(now) > margin
    }

    /// Hide a token in logs/UI: keeps only the first and last 4 characters.
    public static func redacted(_ token: String) -> String {
        token.count <= 12 ? "<token>" : String(token.prefix(4)) + "…" + String(token.suffix(4))
    }
}
