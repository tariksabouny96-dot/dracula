import Foundation
import PushKit

/// Receives VoIP pushes (the only thing that makes the phone ring) and the VoIP token Hood needs.
/// Apple's rule: every VoIP push MUST be reported to CallKit immediately, so `onIncoming` must do so
/// synchronously and `completion` is only called afterwards.
final class VoIPPushRegistry: NSObject, PKPushRegistryDelegate {
    var onToken: ((String) -> Void)?
    var onTokenInvalidated: (() -> Void)?
    /// Parse + report to CallKit. Call `done` once reported.
    var onIncoming: (([AnyHashable: Any], @escaping () -> Void) -> Void)?

    private let registry = PKPushRegistry(queue: .main)

    func start() {
        registry.delegate = self
        registry.desiredPushTypes = [.voIP]
    }

    func pushRegistry(_ registry: PKPushRegistry, didUpdate pushCredentials: PKPushCredentials, for type: PKPushType) {
        guard type == .voIP else { return }
        onToken?(pushCredentials.token.map { String(format: "%02x", $0) }.joined())
    }

    func pushRegistry(_ registry: PKPushRegistry, didInvalidatePushTokenFor type: PKPushType) {
        if type == .voIP { onTokenInvalidated?() }
    }

    func pushRegistry(_ registry: PKPushRegistry, didReceiveIncomingPushWith payload: PKPushPayload,
                      for type: PKPushType, completion: @escaping () -> Void) {
        guard type == .voIP, let handler = onIncoming else { completion(); return }
        handler(payload.dictionaryPayload, completion)
    }
}
