import SwiftUI

@main
struct HoodVoiceApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(AppModel.shared)
                .environmentObject(AppModel.shared.call)
                .onChange(of: scenePhase) { phase in
                    if phase == .active { Task { await AppModel.shared.refresh() } }
                }
        }
    }
}
