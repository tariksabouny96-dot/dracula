import Foundation

/// A JSON value of any shape (call event details are free-form on the server).
public enum JSONValue: Codable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), array([JSONValue]), object([String: JSONValue]), null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }

    public var stringValue: String? { if case .string(let s) = self { return s }; return nil }
}

/// What the app needs to join a call's LiveKit room. The token is short-lived and scoped to one room.
public struct JoinGrant: Codable, Equatable, Sendable {
    public let url: String
    public let token: String
    public let room: String
    public let identity: String
    public let expiresAt: Double
    public init(url: String, token: String, room: String, identity: String, expiresAt: Double) {
        self.url = url; self.token = token; self.room = room; self.identity = identity; self.expiresAt = expiresAt
    }
}

public enum CallDirection: String, Codable, Sendable { case ownerToHood = "owner_to_hood", hoodToOwner = "hood_to_owner" }

public enum ServerCallState: String, Codable, Sendable {
    case created = "CREATED", ringing = "RINGING", connecting = "CONNECTING", active = "ACTIVE", ended = "ENDED",
         missed = "MISSED", declined = "DECLINED", failed = "FAILED", expired = "EXPIRED"
    public var isTerminal: Bool { [.ended, .missed, .declined, .failed, .expired].contains(self) }
}

public struct CallInfo: Codable, Equatable, Sendable {
    public let callId: String
    public let direction: CallDirection
    public let state: ServerCallState
    public let room: String
    public let language: String?
    public let eventType: String?
    public let briefing: String?
    public let endReason: String?
    public let turns: Int?
    public let costUsd: Double?
    public let durationS: Double?
}

public struct StartCallResponse: Codable, Equatable, Sendable {
    public let call: CallInfo
    public let join: JoinGrant
    public let limits: [String: Double]?
}

public struct MissionSummary: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let state: String
    public let objective: String
}

public struct NotificationItem: Codable, Equatable, Sendable, Identifiable {
    public let eventId: String
    public let type: String
    public let severity: String
    public let summary: String
    public let created: Double
    public let decision: String?
    public let notificationStatus: String?
    public var id: String { eventId }
}

public struct SpendSummary: Codable, Equatable, Sendable {
    public let monthUsd: Double
    public let monthMinutes: Double
    public let monthCalls: Int
    public let limits: [String: Double]
}

public struct HomeData: Codable, Equatable, Sendable {
    public struct HoodStatus: Codable, Equatable, Sendable { public let voiceEnabled: Bool; public let transportReady: Bool }
    public let hood: HoodStatus
    public let activeMissions: [MissionSummary]
    public let notifications: [NotificationItem]
    public let pendingConfirmations: Int
    public let spend: SpendSummary
    public let activeCall: CallInfo?
}

public struct ConfirmationItem: Codable, Equatable, Sendable, Identifiable {
    public let kind: String
    public let subject: String
    public let title: String
    public let detail: String
    public let risk: String
    public let phoneConfirmable: Bool
    public var id: String { kind + ":" + subject }
}

public struct ConfirmationList: Codable, Equatable, Sendable { public let items: [ConfirmationItem] }

public struct Challenge: Codable, Equatable, Sendable {
    public let challengeId: String
    public let deviceId: String
    public let kind: String
    public let subject: String
    public let decision: String
    public let subjectHash: String
    public let nonce: String
    public let expires: Double
    public let signingString: String
    public let title: String
    public let detail: String
    public let algorithm: String
}

public struct SubmitResult: Codable, Equatable, Sendable {
    public let applied: Bool
    public let kind: String
    public let decision: String
    public let status: String?
    public let state: String?
}

public struct HoodSettings: Codable, Equatable, Sendable {
    public var language: String
    public var callsEnabled: Bool
    public var quietStart: String
    public var quietEnd: String
    public var timezone: String
    public var urgentOverride: Bool
    public var eventToggles: [String: Bool]
    public var notifyOnInfo: Bool
    public var perCallUsd: Double
    public var dailyUsd: Double
    public var monthlyUsd: Double
    public var dailyMinutes: Double
    public var monthlyMinutes: Double
    public var maxCallSeconds: Double
}

public struct PairResult: Codable, Equatable, Sendable {
    public let deviceId: String
    public let deviceSecret: String
    public let name: String
}

public struct CallEvent: Codable, Equatable, Sendable, Identifiable {
    public let id: Int
    public let ts: Double
    public let kind: String
    public let detail: [String: JSONValue]
}

public struct CallDetail: Codable, Equatable, Sendable {
    public let call: CallInfo
    public let events: [CallEvent]
}

struct APIErrorBody: Codable { let error: String? }
