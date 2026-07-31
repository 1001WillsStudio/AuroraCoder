"""Unit tests for :mod:`memory.redact`.

QA focus: PII / secret hygiene is a security-critical contract. We assert
that every known secret shape is scrubbed, that benign text is untouched,
and that the recurrence count is reported correctly. No network/filesystem.
"""
import pytest

from memory.redact import REDACTED, redact


def test_redact_empty_input_is_noop():
    text, count = redact("")
    assert text == "" and count == 0
    text, count = redact(None)  # type: ignore[arg-type]
    assert text is None and count == 0


def test_redact_clean_text_untouched():
    src = "The quick brown fox jumps over the lazy dog."
    out, count = redact(src)
    assert out == src and count == 0


@pytest.mark.parametrize(
    "label, payload",
    [
        ("api_key assignment", "config api_key: abcdefgh12345678 value"),
        ("sk- prefix", "my secret is sk-abcdefghijklmnopqrstuvwxyz"),
        ("github PAT", "token = ghp_0123456789abcdefghij nope"),
        ("aws access id", "creds AKIAIOSFODNN7EXAMPLE go"),
        ("slack token", "webhook xoxb-1234567890abcdef post"),
        ("bearer token", "Authorization: Bearer aBcDeFgHiJkLmNoPqRsT"),
        ("url credentials", "open https://alice:supersecret@host.example/path"),
    ],
)
def test_redact_known_secret_shapes(label, payload):
    out, count = redact(payload)
    assert REDACTED in out, f"secret not redacted for {label!r}: {out!r}"
    assert count >= 1, f"count not reported for {label!r}"
    # Original secret must no longer appear verbatim in the output.
    leaked = ["sk-abcdefghijklmnopqrstuvwxyz", "ghp_0123456789abcdefghij",
              "AKIAIOSFODNN7EXAMPLE", "xoxb-1234567890abcdef",
              "aBcDeFgHiJkLmNoPqRsT", "alice:supersecret@", "abcdefgh12345678"]
    assert all(tok not in out for tok in leaked), out


def test_redact_pem_key_block():
    src = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA0123456789abcdef==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, count = redact(src)
    assert REDACTED in out and count == 1
    assert "MIIEpAIBAA" not in out


def test_redact_counts_multiple_disjoint_findings():
    src = (
        "one api_key: abcdefgh12345678\n"
        "two: ghp_0123456789abcdefghij\n"
        "three bearer XYZaBCdEFgH1234567890"
    )
    out, count = redact(src)
    assert count >= 3, (out, count)
    assert out.count(REDACTED) >= 3


def test_redact_preserves_surrounding_text():
    src = "prefix api_key: abcdefgh12345678 suffix"
    out, _ = redact(src)
    assert out.startswith("prefix ") and out.endswith(" suffix")