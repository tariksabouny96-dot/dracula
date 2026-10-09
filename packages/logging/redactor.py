"""
HOOD Redaction Engine
Detects and redacts potential API keys, passwords, tokens, and raw credentials.
"""

import re
from typing import Any

# Regex patterns for common API keys and sensitive tokens
SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|secret|password|token|bearer|auth[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?",
    r"(?i)bearer\s+([a-zA-Z0-9_\-\.]{16,})",
    r"(?i)sk-[a-zA-Z0-9_\-]{15,}",
    r"AIzaSy[a-zA-Z0-9_\-]{33}",
    r"(?i)-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----",
]

COMPILED_PATTERNS = [re.compile(p) for p in SECRET_PATTERNS]


def redact_string(text: str) -> str:
    """Sanitizes text, replacing raw credentials with [REDACTED_SECRET]."""
    if not isinstance(text, str):
        return text

    # Do not redact safe SECRET:// pointers
    placeholders = []
    def save_secret_uri(m):
        placeholders.append(m.group(0))
        return f"__SAFE_SECRET_REF_{len(placeholders)-1}__"

    text = re.sub(r"SECRET://[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+", save_secret_uri, text)

    for pattern in COMPILED_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)

    for idx, ref in enumerate(placeholders):
        text = text.replace(f"__SAFE_SECRET_REF_{idx}__", ref)

    return text


def sanitize_object(obj: Any) -> Any:
    """Recursively redacts strings in dicts, lists, or primitives."""
    if isinstance(obj, str):
        return redact_string(obj)
    elif isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in ["password", "secret", "api_key", "token", "auth_token"]):
                if isinstance(v, str) and v.startswith("SECRET://"):
                    sanitized[k] = v  # Safe pointer
                else:
                    sanitized[k] = "[REDACTED_SECRET]"
            else:
                sanitized[k] = sanitize_object(v)
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_object(item) for item in obj]
    return obj
