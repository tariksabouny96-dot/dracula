// swift-tools-version:5.9
import PackageDescription

// Pure Swift (Foundation only on Apple platforms): everything here compiles and is unit-tested on Linux CI as
// well as on macOS. The Apple-only parts (CallKit, PushKit, Keychain, Secure Enclave, LiveKit, SwiftUI) live in
// the app target and depend on this package.
let package = Package(
    name: "HoodVoiceCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "HoodVoiceCore", targets: ["HoodVoiceCore"])],
    dependencies: [
        // CryptoKit lives in the OS on Apple platforms; swift-crypto provides the same API on Linux.
        .package(url: "https://github.com/apple/swift-crypto.git", from: "3.0.0"),
    ],
    targets: [
        .target(
            name: "HoodVoiceCore",
            dependencies: [.product(name: "Crypto", package: "swift-crypto", condition: .when(platforms: [.linux]))]
        ),
        .testTarget(
            name: "HoodVoiceCoreTests",
            dependencies: ["HoodVoiceCore"],
            resources: [.copy("Resources")]
        ),
    ]
)
