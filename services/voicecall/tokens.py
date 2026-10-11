"""Small, dependency-light JWT and signature helpers.

HS256 (LiveKit access tokens, per-call agent tokens), ES256 (APNs provider tokens) and ECDSA P-256
verification (device confirmations). Uses only the stdlib and ``cryptography`` (already a Hood dependency).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _json(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def jwt_hs256(claims: Dict[str, Any], secret: str, headers: Optional[Dict[str, Any]] = None) -> str:
    head = {"alg": "HS256", "typ": "JWT", **(headers or {})}
    signing_input = b64u(_json(head)) + "." + b64u(_json(claims))
    sig = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + b64u(sig)


def verify_hs256(token: str, secret: str, *, now: Optional[float] = None, leeway: int = 0) -> Dict[str, Any]:
    """Return the claims or raise ValueError. Rejects other algorithms (no alg-confusion)."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head = json.loads(b64u_decode(head_b64))
        claims = json.loads(b64u_decode(body_b64))
        sig = b64u_decode(sig_b64)
    except Exception:
        raise ValueError("malformed token") from None
    if head.get("alg") != "HS256":
        raise ValueError("unsupported algorithm")
    expected = hmac.new(secret.encode("utf-8"), (head_b64 + "." + body_b64).encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    t = time.time() if now is None else now
    if "exp" in claims and t > float(claims["exp"]) + leeway:
        raise ValueError("token expired")
    if "nbf" in claims and t + leeway < float(claims["nbf"]):
        raise ValueError("token not yet valid")
    return claims


def load_p8_private_key(pem: bytes):
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("APNs key must be an EC (P-256) key")
    return key


def jwt_es256(claims: Dict[str, Any], private_key: ec.EllipticCurvePrivateKey, headers: Dict[str, Any]) -> str:
    """JWS ES256: signature is the raw 64-byte r||s (not DER)."""
    head = {"alg": "ES256", **headers}
    signing_input = b64u(_json(head)) + "." + b64u(_json(claims))
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def load_p256_public_key(raw: bytes):
    """Accept SPKI DER (what CryptoKit's derRepresentation / SecKey export gives) or 65-byte X9.63."""
    if len(raw) == 65 and raw[0] == 4:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    key = serialization.load_der_public_key(raw)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("device key must be a P-256 public key")
    return key


def verify_p256(public_key: ec.EllipticCurvePublicKey, message: bytes, signature: bytes) -> bool:
    """ECDSA-SHA256. The signature may be DER or raw r||s (CryptoKit rawRepresentation)."""
    try:
        if len(signature) == 64:
            signature = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
