import Foundation

public struct IncomingCall: Equatable, Sendable {
    public let callId: String
    public let caller: String
    public let reason: String
    /// Short-lived, room-scoped join token carried in the VoIP push so the call can connect even when the
    /// phone cannot reach Hood's private API at the moment the owner taps Accept.
    public let join: JoinGrant?
}

public struct NotificationPush: Equatable, Sendable {
    public let eventId: String?
    public let callId: String?
    public let action: String?          // "callback" on a missed-call notification
}

public enum PushPayload: Equatable, Sendable {
    case incomingCall(IncomingCall)
    case notification(NotificationPush)
    case unknown

    /// Strict parser for what Hood's server sends. Anything malformed is `.unknown`, never a half-trusted call.
    public static func parse(_ dict: [AnyHashable: Any]) -> PushPayload {
        guard let type = dict["type"] as? String else { return .unknown }
        switch type {
        case "hood.incoming_call":
            guard let callId = dict["call_id"] as? String, isCallId(callId) else { return .unknown }
            let caller = (dict["caller"] as? String).map { String($0.prefix(40)) } ?? "Hood"
            let reason = (dict["reason"] as? String).map { String($0.prefix(140)) } ?? ""
            return .incomingCall(IncomingCall(callId: callId, caller: caller.isEmpty ? "Hood" : caller, reason: reason,
                                              join: (dict["join"] as? [String: Any]).flatMap(parseGrant)))
        case "hood.notification":
            let call = dict["call_id"] as? String
            return .notification(NotificationPush(eventId: dict["event_id"] as? String,
                                                  callId: call.flatMap { isCallId($0) ? $0 : nil },
                                                  action: dict["action"] as? String))
        default:
            return .unknown
        }
    }

    static func isCallId(_ s: String) -> Bool {
        s.count == 25 && s.hasPrefix("call_") && s.dropFirst(5).allSatisfy { $0.isHexDigit && ($0.isNumber || $0.isLowercase) }
    }

    private static func parseGrant(_ j: [String: Any]) -> JoinGrant? {
        guard let url = j["url"] as? String, let token = j["token"] as? String, let room = j["room"] as? String,
              let identity = j["identity"] as? String,
              let url2 = URL(string: url), ["ws", "wss"].contains(url2.scheme ?? "") else { return nil }
        let exp = (j["expires_at"] as? Double) ?? (j["expires_at"] as? Int).map(Double.init) ?? 0
        return JoinGrant(url: url, token: token, room: room, identity: identity, expiresAt: exp)
    }
}
