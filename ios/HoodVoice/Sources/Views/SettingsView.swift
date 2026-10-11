import HoodVoiceCore
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var confirmUnpair = false

    private let languages: [(code: String, name: String)] = [
        ("en", "English"), ("fr", "Français"), ("ar", "العربية — untested"), ("ar-MA", "Darija — untested"),
    ]

    var body: some View {
        Form {
            if let s = model.settings {
                Section("Language") {
                    Picker("Voice language", selection: binding(s.language, key: "language")) {
                        ForEach(languages, id: \.code) { Text($0.name).tag($0.code) }
                    }
                }
                Section {
                    Toggle("Let Hood call me", isOn: binding(s.callsEnabled, key: "calls_enabled"))
                    Toggle("Notify me about routine updates", isOn: binding(s.notifyOnInfo, key: "notify_on_info"))
                } header: { Text("Calling permissions") } footer: {
                    Text("Hood only calls this phone, and only for the events you leave on below.")
                }
                Section("Events that may ring this phone") {
                    ForEach(s.eventToggles.keys.sorted(), id: \.self) { key in
                        Toggle(label(for: key), isOn: toggleBinding(s, key))
                    }
                }
                Section {
                    TimeRow(title: "Quiet from", value: s.quietStart) { post(["quiet_start": $0]) }
                    TimeRow(title: "Quiet until", value: s.quietEnd) { post(["quiet_end": $0]) }
                    Toggle("Critical alerts may still call", isOn: binding(s.urgentOverride, key: "urgent_override"))
                    LabeledContent("Time zone", value: s.timezone)
                } header: { Text("Quiet hours") } footer: {
                    Text("During quiet hours Hood sends a silent notification instead of calling, unless the event is critical and you allow it.")
                }
                Section {
                    LimitRow(title: "Per call", value: s.perCallUsd, unit: "$", step: 0.25) { post(["per_call_usd": $0]) }
                    LimitRow(title: "Per day", value: s.dailyUsd, unit: "$", step: 0.5) { post(["daily_usd": $0]) }
                    LimitRow(title: "Per month", value: s.monthlyUsd, unit: "$", step: 1) { post(["monthly_usd": $0]) }
                    LimitRow(title: "Minutes per month", value: s.monthlyMinutes, unit: "min", step: 30) { post(["monthly_minutes": $0]) }
                    LimitRow(title: "Longest call", value: s.maxCallSeconds / 60, unit: "min", step: 1) { post(["max_call_seconds": $0 * 60]) }
                } header: { Text("Call spending") } footer: {
                    Text("These can only be set lower than the limits configured on the Hood server.")
                }
            } else {
                Section { ProgressView() }
            }

            Section("This device") {
                LabeledContent("Hood", value: model.serverHost)
                LabeledContent("Decision key", value: model.signerDescription)
                Button("Unpair this phone", role: .destructive) { confirmUnpair = true }
            }
            if let error = model.errorMessage { Section { Banner(text: error, tint: .red).listRowInsets(EdgeInsets()) } }
        }
        .navigationTitle("Settings")
        .refreshable { await model.refresh() }
        .confirmationDialog("Unpair this phone?", isPresented: $confirmUnpair, titleVisibility: .visible) {
            Button("Unpair", role: .destructive) { model.unpair() }
        } message: {
            Text("Hood will stop calling it. Also revoke the device in the Hood console to invalidate its credential.")
        }
    }

    private func post(_ patch: [String: Any]) { Task { await model.updateSettings(patch) } }

    private func binding<T>(_ value: T, key: String) -> Binding<T> {
        Binding(get: { value }, set: { post([key: $0]) })
    }

    private func toggleBinding(_ s: HoodSettings, _ key: String) -> Binding<Bool> {
        Binding(get: { s.eventToggles[key] ?? true }, set: { post(["event_toggles": [key: $0]]) })
    }

    private func label(for key: String) -> String {
        switch key {
        case "security_critical": return "Critical security incident"
        case "operational_failure": return "Serious operational failure"
        case "approval_required": return "Mission needs your approval"
        case "decision_blocking": return "Urgent blocking decision"
        default: return key.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

private struct TimeRow: View {
    let title: String
    let value: String
    let onChange: (String) -> Void
    var body: some View {
        DatePicker(title, selection: Binding(get: { parse(value) }, set: { onChange(format($0)) }), displayedComponents: .hourAndMinute)
    }
    private static let formatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; f.locale = Locale(identifier: "en_US_POSIX"); return f
    }()
    private func parse(_ s: String) -> Date { Self.formatter.date(from: s) ?? Date() }
    private func format(_ d: Date) -> String { Self.formatter.string(from: d) }
}

private struct LimitRow: View {
    let title: String
    let value: Double
    let unit: String
    let step: Double
    let onChange: (Double) -> Void
    var body: some View {
        Stepper(value: Binding(get: { value }, set: { onChange($0) }), in: 0...1000, step: step) {
            HStack { Text(title); Spacer(); Text(unit == "$" ? formatUSD(value) : "\(Int(value)) \(unit)").foregroundStyle(.secondary) }
        }
    }
}
