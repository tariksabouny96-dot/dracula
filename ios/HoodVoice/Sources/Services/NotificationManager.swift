import Foundation
import UIKit
import UserNotifications
import HoodVoiceCore

/// Ordinary notifications (missed-call fallback, mission updates). These are NOT calls: Hood only makes the phone
/// ring with a VoIP push; a missed call arrives here as a banner with a "Call back" action.
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let callbackCategory = "HOOD_MISSED_CALL"
    static let callbackAction = "HOOD_CALL_BACK"
    var onCallBack: ((String) -> Void)?

    func start() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let callBack = UNNotificationAction(identifier: Self.callbackAction, title: "Call Hood back", options: [.foreground])
        center.setNotificationCategories([UNNotificationCategory(identifier: Self.callbackCategory, actions: [callBack],
                                                                 intentIdentifiers: [], options: [])])
        center.requestAuthorization(options: [.alert, .sound, .badge, .timeSensitive]) { granted, _ in
            if granted { DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() } }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        guard case .notification(let n) = PushPayload.parse(info), n.action == "callback", let callId = n.callId else { return }
        onCallBack?(callId)
    }
}
