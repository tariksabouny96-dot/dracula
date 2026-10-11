import HoodVoiceCore
import SwiftUI

struct HomeView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var call: CallController

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                statusCard
                if let banner = call.banner { Banner(text: banner, onDismiss: { call.dismissBanner() }) }
                if let error = model.errorMessage { Banner(text: error, tint: .red) }
                callButton
                if !model.confirmations.isEmpty { confirmationsCard }
                missionsCard
                notificationsCard
                spendCard
            }
            .padding(16)
        }
        .refreshable { await model.refresh() }
        .navigationTitle("Hood")
        .background(Color(.systemGroupedBackground))
    }

    private var voiceReady: Bool { (model.home?.hood.voiceEnabled ?? false) && (model.home?.hood.transportReady ?? false) }
    private var callsEnabled: Bool { model.settings?.callsEnabled ?? true }

    private var statusCard: some View {
        HStack(spacing: 14) {
            HoodAvatar(size: 56)
            VStack(alignment: .leading, spacing: 4) {
                Text("Hood").font(.headline)
                Text(model.serverHost).font(.footnote).foregroundStyle(.secondary)
            }
            Spacer()
            if model.home == nil { StatusPill(text: "Connecting", color: .gray) }
            else if !voiceReady { StatusPill(text: "Voice off", color: .orange) }
            else if !callsEnabled { StatusPill(text: "Hood calls off", color: .orange) }
            else { StatusPill(text: "Ready", color: .green) }
        }
        .card()
    }

    private var callButton: some View {
        Button { call.pressCall() } label: {
            Label("Call Hood", systemImage: "phone.fill")
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity).padding(.vertical, 16)
        }
        .buttonStyle(.borderedProminent).tint(.green)
        .disabled(!voiceReady || !call.phase.isOver)
        .accessibilityHint(voiceReady ? "Starts a voice call with Hood" : "Voice calling is not available right now")
    }

    private var confirmationsCard: some View {
        NavigationLink { ConfirmationsView() } label: {
            HStack {
                Image(systemName: "checkmark.shield.fill").foregroundStyle(.orange)
                Text("\(model.confirmations.count) decision\(model.confirmations.count == 1 ? "" : "s") waiting for you")
                    .font(.subheadline.weight(.medium))
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(.secondary)
            }.card()
        }.buttonStyle(.plain)
    }

    private var missionsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Active missions").font(.headline)
            let missions = model.home?.activeMissions ?? []
            if missions.isEmpty { Text("Nothing running.").font(.subheadline).foregroundStyle(.secondary) }
            ForEach(missions) { m in
                HStack(alignment: .firstTextBaseline) {
                    Text(m.objective).font(.subheadline).lineLimit(2)
                    Spacer()
                    StatusPill(text: m.state.replacingOccurrences(of: "_", with: " "), color: .blue)
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading).card()
    }

    private var notificationsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Notifications").font(.headline)
            let items = model.home?.notifications ?? []
            if items.isEmpty { Text("No recent alerts.").font(.subheadline).foregroundStyle(.secondary) }
            ForEach(items.prefix(8)) { n in
                HStack(alignment: .top, spacing: 10) {
                    Circle().fill(color(for: n.severity)).frame(width: 9, height: 9).padding(.top, 6)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(n.summary).font(.subheadline)
                        Text(Date(timeIntervalSince1970: n.created), style: .relative).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading).card()
    }

    private var spendCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("This month").font(.headline)
            if let s = model.home?.spend {
                let cap = s.limits["monthly_usd"] ?? 0
                Text("\(formatUSD(s.monthUsd)) of \(formatUSD(cap)) · \(Int(s.monthMinutes)) min · \(s.monthCalls) calls").font(.subheadline)
                ProgressView(value: min(s.monthUsd, max(cap, 0.01)), total: max(cap, 0.01)).tint(s.monthUsd > cap * 0.8 ? .orange : .accentColor)
            } else { Text("—").foregroundStyle(.secondary) }
        }.frame(maxWidth: .infinity, alignment: .leading).card()
    }

    private func color(for severity: String) -> Color {
        switch severity { case "critical": return .red; case "high": return .orange; case "medium": return .yellow; default: return .gray }
    }
}

extension View {
    func card() -> some View {
        padding(14).background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }
}
