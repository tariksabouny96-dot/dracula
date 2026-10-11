import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// The one seam between the API client and the network, so the client is testable without a server.
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession
    public init(session: URLSession = URLSession(configuration: .ephemeral)) { self.session = session }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw HoodAPIError.network("Not an HTTP response") }
        return (data, http)
    }
}

public enum HoodAPIError: Error, Equatable, LocalizedError {
    case unauthorized                       // credential missing/wrong/revoked (401)
    case forbidden(String)                  // refused (403)
    case notFound                           // (404)
    case conflict(String)                   // wrong state, e.g. challenge expired / call over (409)
    case rateLimited(String)                // (429)
    case budget(String)                     // voice budget reached (402)
    case unavailable(String)                // feature off / not configured (503)
    case server(Int, String)
    case network(String)
    case decoding(String)

    public var errorDescription: String? {
        switch self {
        case .unauthorized: return "This phone is no longer authorised. Pair it again from the Hood console."
        case .forbidden(let m), .conflict(let m), .rateLimited(let m), .budget(let m), .unavailable(let m): return m
        case .notFound: return "Not found."
        case .server(let c, let m): return "Hood error \(c): \(m)"
        case .network(let m): return "Can't reach Hood: \(m)"
        case .decoding(let m): return "Unexpected reply from Hood: \(m)"
        }
    }

    /// Worth retrying automatically (transient), as opposed to a decision the user must make.
    public var isTransient: Bool {
        switch self { case .network, .server: return true; default: return false }
    }
}
