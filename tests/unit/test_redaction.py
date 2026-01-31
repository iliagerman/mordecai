from app.observability.redaction import redact_text, sanitize


def test_redact_text_redacts_known_patterns():
    s = "email=a@b.com token=sk-abcdefghijklmnopqrstuvwxyz1234567890 AKIAABCDEFGHIJKLMNO12"
    out = redact_text(s, max_chars=10_000)
    assert "a@b.com" not in out
    assert "sk-" not in out
    assert "AKIA" not in out
    assert "[REDACTED]" in out


def test_sanitize_redacts_secret_keys_in_dict():
    payload = {
        "password": "supersecret",
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "nested": {"Authorization": "Bearer abc.def.ghi"},
        "ok": "hello",
    }
    out = sanitize(payload, max_depth=10, max_chars=10_000)
    assert out["password"] == "[REDACTED]"
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"]["Authorization"] == "[REDACTED]"
    assert out["ok"] == "hello"


def test_sanitize_truncates_long_strings():
    s = "x" * 5000
    out = sanitize(s, max_depth=3, max_chars=100)
    assert isinstance(out, str)
    assert len(out) <= 120
