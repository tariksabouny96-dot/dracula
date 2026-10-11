import Foundation

public struct TranscriptLine: Equatable, Sendable, Identifiable {
    public enum Speaker: String, Sendable { case owner, hood }
    public let id: String
    public var speaker: Speaker
    public var text: String
    public var isFinal: Bool
}

/// Live captions: partial results replace themselves by segment id until they are final. Memory only;
/// Hood's rules for what is stored of a conversation apply server-side, the phone keeps nothing on disk.
public struct TranscriptBuffer: Sendable {
    public private(set) var lines: [TranscriptLine] = []
    private let limit: Int
    public init(limit: Int = 200) { self.limit = max(limit, 1) }

    public mutating func apply(id: String, speaker: TranscriptLine.Speaker, text: String, isFinal: Bool) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        if let i = lines.firstIndex(where: { $0.id == id }) {
            guard !lines[i].isFinal else { return }                      // a final line never changes
            lines[i].text = clean; lines[i].isFinal = isFinal
        } else {
            lines.append(TranscriptLine(id: id, speaker: speaker, text: clean, isFinal: isFinal))
            if lines.count > limit { lines.removeFirst(lines.count - limit) }
        }
    }

    public mutating func clear() { lines.removeAll() }
}
