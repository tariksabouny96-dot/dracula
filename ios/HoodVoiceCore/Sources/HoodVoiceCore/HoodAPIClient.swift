import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Credentials of a paired phone. The secret lives in the Keychain on the device; Hood stores only its digest.
public struct DeviceCredentials: Equatable, Sendable {
    public let deviceId: String
    public let secret: String
    public init(deviceId: String, secret: String) { self.deviceId = deviceId; self.secret = secret }
    var header: String { "HoodDevice \(deviceId).\(secret)" }
}

/// Talks to Hood's `/api/voicecall/*` device routes. No mission logic lives here: the phone only asks Hood.
public final class HoodAPIClient: Sendable {
    public let baseURL: URL
    private let credentials: DeviceCredentials?
    private let transport: HTTPTransport
    private let decoder: JSONDecoder = {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d
    }()

    public init(baseURL: URL, credentials: DeviceCredentials?, transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL; self.credentials = credentials; self.transport = transport
    }

    // MARK: pairing (no credential: the one-time code is the proof)
    public static func pair(baseURL: URL, code: String, name: String, publicKeySPKI: Data, appVersion: String,
                            transport: HTTPTransport = URLSessionTransport()) async throws -> PairResult {
        let client = HoodAPIClient(baseURL: baseURL, credentials: nil, transport: transport)
        let body: [String: Any] = ["code": code, "name": name, "public_key": publicKeySPKI.base64EncodedString(),
                                   "platform": "ios", "app_version": appVersion]
        return try await client.call("POST", "pair", body: body)
    }

    // MARK: home, settings
    public func home() async throws -> HomeData { try await call("GET", "device/home") }
    public func settings() async throws -> HoodSettings { try await call("GET", "device/settings") }
    public func updateSettings(_ patch: [String: Any]) async throws -> HoodSettings {
        try await call("POST", "device/settings", body: ["settings": patch])
    }
    public func registerPushTokens(voip: String?, alert: String?, production: Bool) async throws {
        var body: [String: Any] = ["apns_env": production ? "production" : "sandbox"]
        if let voip { body["voip_token"] = voip }
        if let alert { body["alert_token"] = alert }
        let _: Ack = try await call("POST", "device/push-token", body: body)
    }

    // MARK: calls
    public func startCall(language: String? = nil) async throws -> StartCallResponse {
        try await call("POST", "calls/start", body: language.map { ["language": $0] } ?? [:])
    }
    public func callBack(missedCallId: String) async throws -> StartCallResponse {
        try await call("POST", "calls/callback", body: ["call_id": missedCallId])
    }
    public func answer(callId: String) async throws -> StartCallResponse { try await call("POST", "calls/\(callId)/answer") }
    public func decline(callId: String) async throws { let _: CallEnvelope = try await call("POST", "calls/\(callId)/decline") }
    public func rejoin(callId: String) async throws -> StartCallResponse { try await call("POST", "calls/\(callId)/rejoin") }
    public func markConnected(callId: String) async throws { let _: StateAck = try await call("POST", "calls/\(callId)/connected") }
    public func end(callId: String) async throws { let _: CallEnvelope = try await call("POST", "calls/\(callId)/end") }
    public func callDetail(callId: String, afterEvent: Int = 0) async throws -> CallDetail {
        try await call("GET", "calls/\(callId)", query: [URLQueryItem(name: "after", value: String(afterEvent))])
    }

    // MARK: step-up confirmations (decisions are signed on the phone with Face ID)
    public func pendingConfirmations() async throws -> [ConfirmationItem] {
        let list: ConfirmationList = try await call("GET", "confirmations"); return list.items
    }
    public func challenge(kind: String, subject: String, decision: String) async throws -> Challenge {
        try await call("POST", "confirmations/challenge", body: ["kind": kind, "subject": subject, "decision": decision])
    }
    public func submit(challengeId: String, signatureDER: Data) async throws -> SubmitResult {
        try await call("POST", "confirmations/submit", body: ["challenge_id": challengeId, "signature": signatureDER.base64EncodedString()])
    }

    // MARK: plumbing
    private struct Ack: Codable { let ok: Bool? }
    private struct StateAck: Codable { let state: String? }
    private struct CallEnvelope: Codable { let call: CallInfo }

    func call<T: Decodable>(_ method: String, _ path: String, body: [String: Any]? = nil,
                            query: [URLQueryItem] = []) async throws -> T {
        var comps = URLComponents(url: baseURL.appendingPathComponent("api/voicecall/" + path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.timeoutInterval = 20
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let credentials { req.setValue(credentials.header, forHTTPHeaderField: "Authorization") }
        if method == "POST" { req.httpBody = try JSONSerialization.data(withJSONObject: body ?? [:]) }
        let data: Data, http: HTTPURLResponse
        do { (data, http) = try await transport.send(req) }
        catch let e as HoodAPIError { throw e }
        catch { throw HoodAPIError.network((error as NSError).localizedDescription) }
        guard (200..<300).contains(http.statusCode) else { throw Self.map(status: http.statusCode, data: data) }
        do { return try decoder.decode(T.self, from: data) }
        catch { throw HoodAPIError.decoding(String(describing: error)) }
    }

    static func map(status: Int, data: Data) -> HoodAPIError {
        let message = (try? JSONDecoder().decode(APIErrorBody.self, from: data))?.error ?? "HTTP \(status)"
        switch status {
        case 401: return .unauthorized
        case 402: return .budget(message)
        case 403: return .forbidden(message)
        case 404: return .notFound
        case 409: return .conflict(message)
        case 429: return .rateLimited(message)
        case 503: return .unavailable(message)
        default: return .server(status, message)
        }
    }
}
