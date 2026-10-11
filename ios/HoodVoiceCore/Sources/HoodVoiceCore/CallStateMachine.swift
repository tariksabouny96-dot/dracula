import Foundation

public enum EndCause: Equatable, Sendable {
    case userHangup, remoteHangup, declined, missed, networkLost, limitReached, error
}

public enum CallPhase: Equatable, Sendable {
    case idle
    case requesting                       // owner pressed Call; asking Hood for a session
    case ringing                          // Hood is calling (CallKit is showing the native incoming-call screen)
    case connecting                       // have a join grant; joining the room
    case active
    case reconnecting(attempt: Int)
    case ending
    case ended(EndCause)
    case failed(String)

    public var isOver: Bool { switch self { case .ended, .failed, .idle: return true; default: return false } }
    public var isInCall: Bool { switch self { case .connecting, .active, .reconnecting: return true; default: return false } }
}

public enum CallInput: Sendable {
    case pressCall
    case startSucceeded(JoinGrant, callId: String)
    case startFailed(String)
    case incomingCall(callId: String)
    case userAnswered
    case userDeclined
    case ringTimeout
    case answerSucceeded(JoinGrant)
    case answerFailed(String)
    case roomConnected
    case roomDisconnected                 // the media connection dropped (not requested by us)
    case rejoinSucceeded(JoinGrant)
    case rejoinFailed(String)
    case serverEnded(reason: String)      // Hood ended the call (watchdog, limits, hang-up phrase, revoked...)
    case userEnded
    case tick(Date)
}

public enum CallEffect: Equatable, Sendable {
    case apiStart
    case apiAnswer(callId: String)
    case apiDecline(callId: String)
    case apiEnd(callId: String)
    case apiRejoin(callId: String, after: TimeInterval)
    case apiMarkConnected(callId: String)
    case connectRoom(JoinGrant)
    case disconnectRoom
    case reportEnded(EndCause)            // tell CallKit the call is over (clears the system call UI)
    case notify(String)
}

/// Pure state machine for one call on the phone. It decides *what to do*; the app performs the effects.
/// Reconnection mirrors Hood's server rule: if the owner is gone for more than `reconnectWindow` seconds the
/// server ends the call, so the phone gives up at the same time instead of retrying forever.
public struct CallStateMachine: Sendable {
    public static let backoff: [TimeInterval] = [1, 2, 4, 8, 15]
    public static let reconnectWindow: TimeInterval = 60

    public private(set) var phase: CallPhase = .idle
    public private(set) var callId: String?
    private var reconnectStarted: Date?
    private var now: Date = Date()

    public init() {}

    public mutating func send(_ input: CallInput) -> [CallEffect] {
        if case .tick(let t) = input { now = t }
        switch (phase, input) {

        // -------- owner calls Hood
        case (.idle, .pressCall), (.ended, .pressCall), (.failed, .pressCall):
            phase = .requesting; callId = nil
            return [.apiStart]
        case (.requesting, .startSucceeded(let grant, let id)):
            callId = id; phase = .connecting
            return [.connectRoom(grant)]
        case (.requesting, .startFailed(let msg)):
            phase = .failed(msg)
            return [.notify(msg)]

        // -------- Hood calls the owner
        case (.idle, .incomingCall(let id)), (.ended, .incomingCall(let id)), (.failed, .incomingCall(let id)):
            callId = id; phase = .ringing
            return []
        case (.ringing, .userAnswered):
            guard let id = callId else { return [] }
            phase = .connecting
            return [.apiAnswer(callId: id)]
        case (.ringing, .answerSucceeded(let grant)), (.connecting, .answerSucceeded(let grant)):
            phase = .connecting
            return [.connectRoom(grant)]
        case (.connecting, .answerFailed(let msg)), (.ringing, .answerFailed(let msg)):
            phase = .failed(msg)
            return [.reportEnded(.error), .notify(msg)]
        case (.ringing, .userDeclined):
            guard let id = callId else { return [] }
            phase = .ended(.declined)
            return [.apiDecline(callId: id), .reportEnded(.declined)]
        case (.ringing, .ringTimeout):
            phase = .ended(.missed)
            return [.reportEnded(.missed)]

        // -------- media
        case (.connecting, .roomConnected):
            guard let id = callId else { return [] }
            phase = .active
            return [.apiMarkConnected(callId: id)]
        case (.reconnecting, .roomConnected):
            guard let id = callId else { return [] }
            phase = .active; reconnectStarted = nil
            return [.apiMarkConnected(callId: id)]
        case (.active, .roomDisconnected), (.connecting, .roomDisconnected):
            guard let id = callId else { return [] }
            reconnectStarted = now
            phase = .reconnecting(attempt: 1)
            return [.apiRejoin(callId: id, after: Self.backoff[0])]
        case (.reconnecting(let attempt), .rejoinSucceeded(let grant)):
            phase = .reconnecting(attempt: attempt)
            return [.connectRoom(grant)]
        case (.reconnecting(let attempt), .rejoinFailed(let msg)):
            return retryOrGiveUp(attempt: attempt, message: msg)
        case (.reconnecting(let attempt), .roomDisconnected):
            return retryOrGiveUp(attempt: attempt, message: "the connection dropped again")
        case (.reconnecting, .tick(let t)):
            guard let id = callId, let started = reconnectStarted else { return [] }
            if t.timeIntervalSince(started) > Self.reconnectWindow {
                phase = .ended(.networkLost)
                return [.disconnectRoom, .apiEnd(callId: id), .reportEnded(.networkLost)]
            }
            return []

        // -------- ending
        case (.requesting, .userEnded), (.ringing, .userEnded):
            phase = .ended(.userHangup)
            return [.reportEnded(.userHangup)]
        case (.connecting, .userEnded), (.active, .userEnded), (.reconnecting, .userEnded):
            guard let id = callId else { return [] }
            phase = .ended(.userHangup)
            return [.disconnectRoom, .apiEnd(callId: id), .reportEnded(.userHangup)]
        case (.ringing, .serverEnded(let reason)), (.requesting, .serverEnded(let reason)):
            phase = .ended(Self.cause(for: reason))
            return [.reportEnded(Self.cause(for: reason))]
        case (.connecting, .serverEnded(let reason)), (.active, .serverEnded(let reason)), (.reconnecting, .serverEnded(let reason)):
            let cause = Self.cause(for: reason)
            phase = .ended(cause)
            return [.disconnectRoom, .reportEnded(cause)] + (cause == .limitReached ? [.notify("The call reached its limit.")] : [])

        // everything else (late or duplicate events) is ignored on purpose
        default:
            return []
        }
    }

    private mutating func retryOrGiveUp(attempt: Int, message: String) -> [CallEffect] {
        guard let id = callId else { return [] }
        if Self.exhausted(attempt: attempt, started: reconnectStarted, now: now) {
            phase = .ended(.networkLost)
            return [.disconnectRoom, .apiEnd(callId: id), .reportEnded(.networkLost), .notify("The call dropped: \(message)")]
        }
        phase = .reconnecting(attempt: attempt + 1)
        return [.apiRejoin(callId: id, after: Self.backoff[min(attempt, Self.backoff.count - 1)])]
    }

    static func exhausted(attempt: Int, started: Date?, now: Date) -> Bool {
        if attempt >= backoff.count { return true }
        if let started, now.timeIntervalSince(started) > reconnectWindow { return true }
        return false
    }

    static func cause(for serverReason: String) -> EndCause {
        switch serverReason {
        case "max_duration", "budget": return .limitReached
        case "owner_hangup", "hood_hangup": return .remoteHangup
        case "no_answer": return .missed
        case "declined": return .declined
        case "network", "worker_lost": return .networkLost
        default: return .remoteHangup
        }
    }
}
