import json
import logging
from packages.logging.redactor import redact_string, sanitize_object
from packages.logging.logger import HoodStructuredFormatter

def test_redact_sensitive_strings():
    raw_key = "api_key: sk-1234567890abcdef1234567890abcdef"
    redacted = redact_string(raw_key)
    assert "sk-" not in redacted
    assert "[REDACTED_SECRET]" in redacted

def test_preserve_safe_secret_references():
    safe_ref = "Using pointer SECRET://gemini/api_key for auth"
    result = redact_string(safe_ref)
    assert "SECRET://gemini/api_key" in result

def test_sanitize_dict():
    data = {
        "user": "Zak",
        "api_key": "raw_super_secret_value_12345678",
        "nested": {
            "token": "bearer 12345678901234567890",
            "safe": "SECRET://provider/token"
        }
    }
    sanitized = sanitize_object(data)
    assert sanitized["user"] == "Zak"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["token"] == "[REDACTED_SECRET]"
    assert sanitized["nested"]["safe"] == "SECRET://provider/token"

def test_structured_formatter():
    formatter = HoodStructuredFormatter()
    record = logging.LogRecord(
        name="hood_test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Connecting with secret sk-abcdef12345678901234567890",
        args=(),
        exc_info=None
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert "sk-" not in parsed["message"]
    assert "[REDACTED_SECRET]" in parsed["message"]
