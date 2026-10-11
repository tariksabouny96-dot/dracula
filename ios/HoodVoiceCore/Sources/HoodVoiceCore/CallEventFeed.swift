import Foundation

/// Call events reach the phone twice (instantly over the room's data channel, and by polling Hood).
/// This merges both without showing anything twice, and remembers the cursor to poll from.
public struct CallEventFeed: Sendable {
    public private(set) var events: [CallEvent] = []
    public private(set) var cursor: Int = 0
    private var seen: Set<Int> = []
    public init() {}

    /// Returns only events not seen before, in order.
    @discardableResult
    public mutating func ingest(_ incoming: [CallEvent]) -> [CallEvent] {
        var fresh: [CallEvent] = []
        for e in incoming.sorted(by: { $0.id < $1.id }) where !seen.contains(e.id) {
            seen.insert(e.id); events.append(e); fresh.append(e); cursor = max(cursor, e.id)
        }
        return fresh
    }

    /// The server's reason if the call has ended, read from the audit trail.
    public var endedReason: String? {
        events.last(where: { $0.kind == "ended" })?.detail["reason"]?.stringValue
    }

    /// Items Hood wants confirmed on the phone ("confirm_request" events).
    public var confirmationRequests: [CallEvent] { events.filter { $0.kind == "confirm_request" } }
}
