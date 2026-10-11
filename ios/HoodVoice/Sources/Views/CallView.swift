import AVKit
import HoodVoiceCore
import SwiftUI

struct CallView: View {
    @EnvironmentObject private var call: CallController
    @State private var showCaptions = false

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.05, green: 0.06, blue: 0.11), Color(red: 0.12, green: 0.15, blue: 0.28)],
                           startPoint: .top, endPoint: .bottom).ignoresSafeArea()
            VStack(spacing: 20) {
                Spacer(minLength: 12)
                HoodAvatar(size: showCaptions ? 72 : 132)
                Text(call.callerName).font(.largeTitle.weight(.semibold)).foregroundStyle(.white)
                statusLine
                if !call.reason.isEmpty, call.phase == .ringing {
                    Text(call.reason).font(.callout).foregroundStyle(.white.opacity(0.8))
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                if let banner = call.banner { Banner(text: banner, tint: .yellow).padding(.horizontal, 20) }
                if showCaptions { captions } else { Spacer() }
                controls.padding(.bottom, 28)
            }
        }
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var statusLine: some View {
        VStack(spacing: 6) {
            Text(statusText).font(.title3).foregroundStyle(.white.opacity(0.85))
                .accessibilityIdentifier("call-status")
            if call.phase == .active { Text(formatDuration(call.elapsed)).font(.body.monospacedDigit()).foregroundStyle(.white.opacity(0.7)) }
            if case .reconnecting = call.phase { ProgressView().tint(.white) }
        }
    }

    private var statusText: String {
        switch call.phase {
        case .idle: return ""
        case .requesting: return "Calling Hood…"
        case .ringing: return "Hood is calling"
        case .connecting: return "Connecting…"
        case .active: return call.isMuted ? "Connected · muted" : "Connected"
        case .reconnecting(let attempt): return "Connection lost — reconnecting (\(attempt))"
        case .ending: return "Ending…"
        case .ended: return "Call ended"
        case .failed(let message): return message
        }
    }

    private var captions: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(call.transcript) { line in
                        HStack {
                            if line.speaker == .owner { Spacer(minLength: 40) }
                            Text(line.text)
                                .padding(10)
                                .background(line.speaker == .owner ? Color.blue.opacity(0.55) : Color.white.opacity(0.14),
                                            in: RoundedRectangle(cornerRadius: 12))
                                .opacity(line.isFinal ? 1 : 0.7)
                                .foregroundStyle(.white)
                            if line.speaker == .hood { Spacer(minLength: 40) }
                        }
                        .id(line.id)
                    }
                }
                .padding(.horizontal, 16)
            }
            .onChange(of: call.transcript.last?.text) { _ in
                if let last = call.transcript.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }
    }

    @ViewBuilder private var controls: some View {
        if call.phase == .ringing {
            HStack(spacing: 64) {
                RoundCallButton(systemImage: "phone.down.fill", label: "Decline", tint: .red, filled: true) { call.hangUp() }
                RoundCallButton(systemImage: "phone.fill", label: "Accept", tint: .green, filled: true) { call.accept() }
            }
        } else {
            VStack(spacing: 24) {
                HStack(spacing: 28) {
                    RoundCallButton(systemImage: call.isMuted ? "mic.slash.fill" : "mic.fill", label: call.isMuted ? "Unmute" : "Mute",
                                    tint: .white, filled: call.isMuted) { call.toggleMute() }
                    RoundCallButton(systemImage: call.speakerOn ? "speaker.wave.3.fill" : "speaker.fill", label: "Speaker",
                                    tint: .white, filled: call.speakerOn) { call.toggleSpeaker() }
                    RoundCallButton(systemImage: "captions.bubble.fill", label: "Captions", tint: .white, filled: showCaptions) {
                        withAnimation { showCaptions.toggle() }
                    }
                }
                HStack(spacing: 28) {
                    RoutePicker().frame(width: 44, height: 44).accessibilityLabel("Audio output")
                    RoundCallButton(systemImage: "phone.down.fill", label: "End", tint: .red, filled: true) { call.hangUp() }
                    Color.clear.frame(width: 44, height: 44)
                }
            }
        }
    }
}

/// The system audio-route picker (earpiece, speaker, Bluetooth, AirPods).
struct RoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let v = AVRoutePickerView()
        v.tintColor = .white; v.activeTintColor = .systemGreen
        return v
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
