import HoodVoiceCore
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var call: CallController

    private var callIsVisible: Binding<Bool> {
        Binding(get: { !call.phase.isOver }, set: { _ in })
    }

    var body: some View {
        Group {
            switch model.pairing {
            case .unpaired: PairingView()
            case .paired: MainTabs()
            }
        }
        .fullScreenCover(isPresented: callIsVisible) { CallView() }
    }
}

struct MainTabs: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        TabView {
            NavigationStack { HomeView() }
                .tabItem { Label("Home", systemImage: "waveform.circle") }
            NavigationStack { ConfirmationsView() }
                .tabItem { Label("Confirm", systemImage: "checkmark.shield") }
                .badge(model.confirmations.count)
            NavigationStack { SettingsView() }
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
