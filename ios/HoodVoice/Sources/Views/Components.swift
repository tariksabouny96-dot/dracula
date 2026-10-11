import SwiftUI

struct StatusPill: View {
    let text: String
    let color: Color
    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
            .accessibilityLabel(text)
    }
}

struct RoundCallButton: View {
    let systemImage: String
    let label: String
    var tint: Color = .gray
    var filled = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: systemImage)
                    .font(.title2.weight(.semibold))
                    .frame(width: 68, height: 68)
                    .background(filled ? tint : tint.opacity(0.18), in: Circle())
                    .foregroundStyle(filled ? Color.white : tint)
                Text(label).font(.footnote).foregroundStyle(.secondary)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

struct HoodAvatar: View {
    var size: CGFloat = 120
    var body: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: [Color(red: 0.10, green: 0.12, blue: 0.20), Color(red: 0.20, green: 0.25, blue: 0.45)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
            Image(systemName: "waveform").font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(.white)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

struct Banner: View {
    let text: String
    var tint: Color = .orange
    var onDismiss: (() -> Void)?
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(tint)
            Text(text).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
            if let onDismiss {
                Button(action: onDismiss) { Image(systemName: "xmark").font(.footnote.weight(.bold)) }
                    .buttonStyle(.plain).accessibilityLabel("Dismiss")
            }
        }
        .padding(12)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}

func formatDuration(_ seconds: TimeInterval) -> String {
    let s = max(0, Int(seconds))
    return String(format: "%d:%02d", s / 60, s % 60)
}

func formatUSD(_ value: Double) -> String { String(format: "$%.2f", value) }
