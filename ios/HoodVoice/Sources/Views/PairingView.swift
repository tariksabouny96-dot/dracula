import SwiftUI

struct PairingView: View {
    @EnvironmentObject private var model: AppModel
    @State private var server = ""
    @State private var code = ""
    @State private var deviceName = UIDevice.current.name

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack { Spacer(); HoodAvatar(size: 88); Spacer() }
                        .listRowBackground(Color.clear)
                    Text("Pair this iPhone with your Hood. Hood will only ever call a phone you pair here.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Section("Hood address") {
                    TextField("https://hood.example.com", text: $server)
                        .textContentType(.URL).keyboardType(.URL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                Section {
                    TextField("Pairing code", text: $code)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                        .font(.system(.body, design: .monospaced))
                    TextField("Name of this phone", text: $deviceName)
                } header: { Text("Pairing code") } footer: {
                    Text("In the Hood console open Voice calling, choose “Pair a phone”, and type the code it shows. The code works once and expires after five minutes.")
                }
                if let error = model.errorMessage {
                    Section { Banner(text: error, tint: .red).listRowInsets(EdgeInsets()) }
                }
                Section {
                    Button {
                        Task { await model.pair(server: server, code: code, deviceName: deviceName) }
                    } label: {
                        HStack { Spacer(); if model.isBusy { ProgressView() } else { Text("Pair this phone").bold() }; Spacer() }
                    }
                    .disabled(model.isBusy || server.isEmpty || code.isEmpty)
                }
            }
            .navigationTitle("Hood")
        }
    }
}
