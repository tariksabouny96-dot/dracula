import AVFoundation
import Foundation
import HoodVoiceCore
import SwiftUI
import UIKit

/// What the screens read. Holds the paired-device credential (in the Keychain), the cached Home data, and the
/// glue that registers push tokens and signs confirmations. It never decides anything about missions or approvals.
@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    enum Pairing: Equatable { case unpaired, paired(deviceName: String) }

    @Published private(set) var pairing: Pairing = .unpaired
    @Published private(set) var home: HomeData?
    @Published private(set) var settings: HoodSettings?
    @Published private(set) var confirmations: [ConfirmationItem] = []
    @Published private(set) var serverHost = ""
    @Published private(set) var signerDescription = ""
    @Published var errorMessage: String?
    @Published private(set) var isBusy = false

    let callKit = CallKitManager()
    let voip = VoIPPushRegistry()
    let notifications = NotificationManager()
    let keys = DeviceKeyManager()
    let call: CallController

    private let keychain = KeychainStore()
    private var api: HoodAPIClient?
    private var deviceId: String?
    private var voipToken: String?
    private var alertToken: String?
    private var started = false

    init() {
        call = CallController(callKit: callKit)
        call.apiProvider = { [weak self] in self?.api }
        call.languageProvider = { [weak self] in self?.settings?.language }
        call.onConfirmationRequested = { [weak self] in Task { await self?.refreshConfirmations() } }
        call.onCallFinished = { [weak self] in Task { await self?.refresh() } }
    }

    /// Called once from the app delegate at launch. A VoIP push can start the process in the background, so
    /// everything that must react to it is set up here, not in a view.
    func start() {
        guard !started else { return }
        started = true
        loadStoredPairing()
        voip.onToken = { [weak self] token in Task { @MainActor in self?.voipToken = token; await self?.registerTokens() } }
        voip.onTokenInvalidated = { [weak self] in Task { @MainActor in self?.voipToken = nil } }
        voip.onIncoming = { [weak self] payload, done in
            // PushKit delivers on the main queue; the call MUST be reported to CallKit before this returns.
            MainActor.assumeIsolated {
                guard let self else { done(); return }
                self.call.handleIncomingPush(payload, done: done)
            }
        }
        voip.start()
        notifications.onCallBack = { [weak self] callId in Task { @MainActor in self?.call.callBack(missedCallId: callId) } }
        notifications.start()
        Task { await refresh() }
    }

    func setAlertToken(_ token: String) { alertToken = token; Task { await registerTokens() } }

    // MARK: pairing
    func pair(server: String, code: String, deviceName: String) async {
        errorMessage = nil
        guard let url = Self.normalizedServerURL(server) else {
            errorMessage = "Enter Hood's address, for example https://hood.example.com or http://192.168.1.20:8080."; return
        }
        let cleanCode = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !cleanCode.isEmpty else { errorMessage = "Enter the pairing code shown in the Hood console."; return }
        isBusy = true; defer { isBusy = false }
        do {
            let signer = try keys.signer()
            let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1"
            let result = try await HoodAPIClient.pair(baseURL: url, code: cleanCode, name: deviceName,
                                                      publicKeySPKI: try signer.publicKeySPKI(), appVersion: version)
            try keychain.set(url.absoluteString, for: .serverURL)
            try keychain.set(result.deviceId, for: .deviceId)
            try keychain.set(result.deviceSecret, for: .deviceSecret)
            loadStoredPairing()
            await requestMicrophoneAccess()
            await registerTokens()
            await refresh()
        } catch {
            if !hasStoredCredential { keys.reset() }      // do not keep a key Hood never learned about
            errorMessage = CallController.describe(error)
        }
    }

    /// Forget this phone locally. The owner should also revoke the device in the Hood console.
    func unpair() {
        if call.phase.isInCall { call.hangUp() }
        keychain.removeAll(); keys.reset()
        api = nil; deviceId = nil; home = nil; settings = nil; confirmations = []
        pairing = .unpaired; serverHost = ""
    }

    private var hasStoredCredential: Bool { keychain.string(for: .deviceId) != nil }

    private func loadStoredPairing() {
        guard let urlString = keychain.string(for: .serverURL), let url = URL(string: urlString),
              let id = keychain.string(for: .deviceId), let secret = keychain.string(for: .deviceSecret) else {
            pairing = .unpaired; api = nil; return
        }
        deviceId = id
        api = HoodAPIClient(baseURL: url, credentials: DeviceCredentials(deviceId: id, secret: secret))
        serverHost = url.host ?? urlString
        pairing = .paired(deviceName: UIDevice.current.name)
        signerDescription = keys.isSecureEnclaveAvailable ? "Secure Enclave key, confirmed with Face ID" : "Software key (no Secure Enclave on this device)"
    }

    static func normalizedServerURL(_ text: String) -> URL? {
        var t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return nil }
        if !t.contains("://") { t = "https://" + t }
        guard var comps = URLComponents(string: t), let scheme = comps.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = comps.host, !host.isEmpty else { return nil }
        comps.path = ""; comps.query = nil; comps.fragment = nil
        return comps.url
    }

    // MARK: data
    func refresh() async {
        guard let api else { return }
        do {
            async let h = api.home()
            async let s = api.settings()
            async let c = api.pendingConfirmations()
            let (newHome, newSettings, newConfirmations) = try await (h, s, c)
            home = newHome; settings = newSettings; confirmations = newConfirmations
            errorMessage = nil
        } catch let e as HoodAPIError where e == .unauthorized {
            errorMessage = e.errorDescription
            unpair()
        } catch {
            errorMessage = CallController.describe(error)
        }
    }

    func refreshConfirmations() async {
        guard let api, let items = try? await api.pendingConfirmations() else { return }
        confirmations = items
    }

    func updateSettings(_ patch: [String: Any]) async {
        guard let api else { return }
        do { settings = try await api.updateSettings(patch); errorMessage = nil }
        catch { errorMessage = CallController.describe(error); await refresh() }
    }

    // MARK: push tokens
    private func registerTokens() async {
        guard let api, voipToken != nil || alertToken != nil else { return }
        #if DEBUG
        let production = false          // Xcode-installed builds receive pushes from APNs' sandbox
        #else
        let production = true           // TestFlight / App Store builds use APNs production
        #endif
        do { try await api.registerPushTokens(voip: voipToken, alert: alertToken, production: production) }
        catch { errorMessage = "Could not register for calls: " + CallController.describe(error) }
    }

    private func requestMicrophoneAccess() async {
        if #available(iOS 17.0, *) { _ = await AVAudioApplication.requestRecordPermission() }
        else { _ = await withCheckedContinuation { c in AVAudioSession.sharedInstance().requestRecordPermission { c.resume(returning: $0) } } }
    }

    // MARK: confirmations signed on the phone (Face ID)
    /// `decision` is "approve" or "reject". Hood issues a one-time challenge bound to this device, this decision and
    /// the exact subject; the phone shows what it is signing and signs only after Face ID.
    func decide(_ item: ConfirmationItem, decision: String) async -> Bool {
        guard let api, let deviceId else { return false }
        isBusy = true; defer { isBusy = false }
        do {
            let challenge = try await api.challenge(kind: item.kind, subject: item.subject, decision: decision)
            try challenge.validate(forDevice: deviceId)
            let signer = try keys.signer(reason: "Confirm: \(item.title)")
            let signature = try await Task.detached(priority: .userInitiated) {
                try challenge.signature(using: signer, forDevice: deviceId)
            }.value
            let result = try await api.submit(challengeId: challenge.challengeId, signatureDER: signature)
            errorMessage = result.applied ? nil : "Hood did not apply the decision."
            await refresh()
            return result.applied
        } catch {
            errorMessage = CallController.describe(error)
            return false
        }
    }
}
