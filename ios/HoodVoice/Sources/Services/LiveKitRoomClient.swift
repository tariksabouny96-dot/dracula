import Foundation
import LiveKit
import HoodVoiceCore

/// What the call controller needs from the media layer (a seam: the vendor can change, and tests use a fake).
protocol RoomControlling: AnyObject {
    var onConnected: (() -> Void)? { get set }
    var onUnexpectedDisconnect: (() -> Void)? { get set }
    var onHoodEvent: (([String: Any]) -> Void)? { get set }
    var onTranscript: ((String, TranscriptLine.Speaker, String, Bool) -> Void)? { get set }
    func connect(_ grant: JoinGrant) async throws
    func disconnect() async
    func setMuted(_ muted: Bool) async
}

/// The only file that talks to LiveKit's Room. The rest of the app sees plain callbacks, so the media vendor can
/// be swapped without touching call logic.
final class LiveKitRoomClient: NSObject, RoomDelegate, RoomControlling, @unchecked Sendable {
    var onConnected: (() -> Void)?
    var onUnexpectedDisconnect: (() -> Void)?
    var onHoodEvent: (([String: Any]) -> Void)?
    var onTranscript: ((String, TranscriptLine.Speaker, String, Bool) -> Void)?

    private var room: Room?
    private var intentionalDisconnect = false

    func connect(_ grant: JoinGrant) async throws {
        if let old = room {                       // a rejoin replaces the previous (dead) room
            intentionalDisconnect = true
            await old.disconnect()
        }
        intentionalDisconnect = false
        let room = Room(delegate: self)
        self.room = room
        try await room.connect(url: grant.url, token: grant.token)
        try await room.localParticipant.setMicrophone(enabled: true)
        try? await room.registerTextStreamHandler(for: "lk.transcription") { [weak self] reader, identity in
            guard let self else { return }
            Task { await self.consume(reader: reader, from: identity) }
        }
    }

    func disconnect() async {
        intentionalDisconnect = true
        await room?.disconnect()
        room = nil
    }

    func setMuted(_ muted: Bool) async {
        _ = try? await room?.localParticipant.setMicrophone(enabled: !muted)
    }

    // MARK: transcription (agent text arrives in chunks, owner text arrives as a growing full line)
    private func consume(reader: TextStreamReader, from identity: Participant.Identity) async {
        let attrs = reader.info.attributes
        let segment = attrs["lk.segment_id"] ?? reader.info.id
        let isOwner = identity == room?.localParticipant.identity
        var text = ""
        do {
            for try await chunk in reader {
                text = isOwner ? chunk : text + chunk
                onTranscript?(segment, isOwner ? .owner : .hood, text, false)
            }
        } catch { /* partial text stays as it was */ }
        onTranscript?(segment, isOwner ? .owner : .hood, text, true)
    }

    // MARK: RoomDelegate
    func room(_ room: Room, didUpdateConnectionState connectionState: ConnectionState, from oldConnectionState: ConnectionState) {
        if connectionState == .connected { onConnected?() }
    }

    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        if !intentionalDisconnect { onUnexpectedDisconnect?() }
    }

    func room(_ room: Room, participant: RemoteParticipant?, didReceiveData data: Data, forTopic topic: String, encryptionType: EncryptionType) {
        guard topic == "hood.event", let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        onHoodEvent?(obj)
    }
}
