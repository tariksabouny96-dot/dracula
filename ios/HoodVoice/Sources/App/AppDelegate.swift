import UIKit

/// Receives the APNs *alert* token (used only for missed-call and status notifications; calls ring through PushKit).
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // LiveKit's documented CallKit setup: must run before any Room exists.
        CallKitManager.prepareAudioEngine()
        MainActor.assumeIsolated { AppModel.shared.start() }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in AppModel.shared.setAlertToken(token) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Alert pushes are optional (calls use PushKit); the app keeps working without them.
    }
}
