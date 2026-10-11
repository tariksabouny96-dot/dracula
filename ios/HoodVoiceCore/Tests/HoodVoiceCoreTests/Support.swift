import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import HoodVoiceCore

func fixtureData(_ name: String) throws -> Data {
    guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Resources") else {
        throw NSError(domain: "fixture", code: 1, userInfo: [NSLocalizedDescriptionKey: "missing fixture \(name)"])
    }
    return try Data(contentsOf: url)
}

func decodeFixture<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
    let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
    return try d.decode(T.self, from: try fixtureData(name))
}

/// Records requests and replays canned responses: no network is involved.
final class MockTransport: HTTPTransport, @unchecked Sendable {
    struct Canned { let status: Int; let body: Data }
    private let lock = NSLock()
    private var queue: [Result<Canned, Error>] = []
    private(set) var requests: [URLRequest] = []

    func enqueue(status: Int = 200, fixture: String) throws { lock.lock(); queue.append(.success(Canned(status: status, body: try fixtureData(fixture)))); lock.unlock() }
    func enqueue(status: Int, json: String) { lock.lock(); queue.append(.success(Canned(status: status, body: Data(json.utf8)))); lock.unlock() }
    func enqueue(error: Error) { lock.lock(); queue.append(.failure(error)); lock.unlock() }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lock.lock(); requests.append(request); let next = queue.isEmpty ? nil : queue.removeFirst(); lock.unlock()
        switch next {
        case .success(let c)?:
            return (c.body, HTTPURLResponse(url: request.url!, statusCode: c.status, httpVersion: nil, headerFields: nil)!)
        case .failure(let e)?: throw e
        case nil: throw HoodAPIError.network("no canned response queued")
        }
    }
}

let testBase = URL(string: "http://hood.test:8990")!
let testCreds = DeviceCredentials(deviceId: "dev_0123456789abcdef01234567", secret: "s3cret-value")
