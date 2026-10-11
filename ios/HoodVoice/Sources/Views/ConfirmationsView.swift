import HoodVoiceCore
import SwiftUI

/// Decisions Hood wants a human to make. Approve / reject is signed with Face ID on this phone; a spoken “yes”
/// never counts. Money and X activation are not offered here at all (console only).
struct ConfirmationsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var working: String?

    var body: some View {
        List {
            if model.confirmations.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "checkmark.shield").font(.largeTitle).foregroundStyle(.secondary)
                    Text("Nothing is waiting for you.").foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity).padding(.vertical, 40).listRowBackground(Color.clear)
            }
            ForEach(model.confirmations) { item in
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(item.title).font(.headline)
                        Spacer()
                        StatusPill(text: item.risk.uppercased(), color: item.risk == "L3" ? .red : .orange)
                    }
                    Text(item.detail).font(.subheadline).foregroundStyle(.secondary)
                    if item.phoneConfirmable {
                        HStack(spacing: 12) {
                            Button(role: .destructive) { decide(item, "reject") } label: { Label("Reject", systemImage: "xmark").frame(maxWidth: .infinity) }
                                .buttonStyle(.bordered)
                            Button { decide(item, "approve") } label: { Label("Approve", systemImage: "faceid").frame(maxWidth: .infinity) }
                                .buttonStyle(.borderedProminent)
                        }
                        .disabled(working != nil)
                    } else {
                        Text("This one can only be decided in the Hood console.").font(.footnote).foregroundStyle(.orange)
                    }
                }
                .padding(.vertical, 6)
            }
            if let error = model.errorMessage { Section { Banner(text: error, tint: .red).listRowInsets(EdgeInsets()) } }
        }
        .navigationTitle("Confirm")
        .refreshable { await model.refreshConfirmations() }
        .task { await model.refreshConfirmations() }
    }

    private func decide(_ item: ConfirmationItem, _ decision: String) {
        working = item.id
        Task { _ = await model.decide(item, decision: decision); working = nil }
    }
}
