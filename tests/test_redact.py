import copy
from dataclasses import dataclass

import pytest

from agentic_threads import auth, redact


def test_redact_recurses_without_mutating_caller_owned_containers():
    payload = {
        "sessionid": "synthetic-session-secret",
        "nested": [
            {"Authorization": "Bearer synthetic-bearer-secret"},
            (
                "safe-value",
                {"owner-id": "synthetic-owner-secret"},
            ),
        ],
    }
    before = copy.deepcopy(payload)

    scrubbed = redact.redact(payload)

    assert payload == before
    assert scrubbed is not payload
    assert scrubbed["nested"] is not payload["nested"]
    assert scrubbed["nested"][1] is not payload["nested"][1]
    assert scrubbed == {
        "sessionid": "[REDACTED]",
        "nested": [
            {"Authorization": "[REDACTED]"},
            ("safe-value", {"owner-id": "[REDACTED]"}),
        ],
    }


def test_redact_projects_dataclass_fields_and_scrubs_credential_names():
    secrets = {
        "sessionid": "credential-redact-session-unique",
        "ds_user_id": "credential-redact-user-unique",
        "csrftoken": "credential-redact-csrf-unique",
        "feature_token": "credential-redact-feature-unique",
    }
    credential = auth.SessionCredential(
        sessionid=secrets["sessionid"],
        ds_user_id=secrets["ds_user_id"],
        csrftoken=secrets["csrftoken"],
        user_agent="Synthetic Safe Browser/1.0",
        doc_ids={"feed": "synthetic-safe-doc"},
        features={"extension_token": secrets["feature_token"], "enabled": True},
        extracted_at="2026-07-23T00:00:00Z",
    )

    scrubbed = redact.redact(credential)

    assert scrubbed == {
        "sessionid": "[REDACTED]",
        "ds_user_id": "[REDACTED]",
        "csrftoken": "[REDACTED]",
        "user_agent": "Synthetic Safe Browser/1.0",
        "doc_ids": {"feed": "synthetic-safe-doc"},
        "features": {"extension_token": "[REDACTED]", "enabled": True},
        "extracted_at": "2026-07-23T00:00:00Z",
    }
    assert all(secret not in repr(scrubbed) for secret in secrets.values())
    assert credential.sessionid == secrets["sessionid"]


def test_dataclass_projection_preserves_cycle_safety():
    @dataclass
    class CyclicDiagnostic:
        api_token: str
        child: object | None = None

    payload = CyclicDiagnostic(api_token="dataclass-cycle-token-unique")
    payload.child = payload

    assert redact.redact(payload) == {
        "api_token": "[REDACTED]",
        "child": "[REDACTED CYCLE]",
    }
    assert payload.child is payload


def test_sensitive_key_families_and_unstructured_secrets_are_removed():
    secrets = (
        "synthetic-direct-secret",
        "synthetic-token-secret",
        "synthetic-inline-session",
        "synthetic-inline-bearer",
    )
    payload = {
        "client-secret": secrets[0],
        "futureOAuthToken": secrets[1],
        "diagnostic": (f"sessionid={secrets[2]}; Authorization: Bearer {secrets[3]}"),
    }

    scrubbed = redact.redact(payload)
    rendered = repr(scrubbed)

    assert scrubbed["client-secret"] == "[REDACTED]"
    assert scrubbed["futureOAuthToken"] == "[REDACTED]"
    assert all(secret not in rendered for secret in secrets)
    assert rendered.count("[REDACTED]") == 4


def test_json_shaped_raw_text_secret_is_removed():
    raw = '{"csrftoken": "synthetic-json-secret", "status": "safe"}'

    scrubbed = redact.redact_raw_text(raw)

    assert "synthetic-json-secret" not in scrubbed
    assert '"csrftoken":"[REDACTED]"' in scrubbed
    assert '"status": "safe"' in scrubbed


@pytest.mark.parametrize(
    ("raw", "secret", "safe_fragment"),
    [
        (
            '{"extension_cookie": "raw-double-quoted-unique with spaces", "ok": true}',
            "raw-double-quoted-unique with spaces",
            '"ok": true',
        ),
        (
            "Probe(extension_token='raw-single-quoted-unique with spaces', ok=True)",
            "raw-single-quoted-unique with spaces",
            "ok=True",
        ),
        (
            "extension_token=raw unquoted value unique; status=safe",
            "raw unquoted value unique",
            "status=safe",
        ),
        (
            "plugin_cookie=raw-suffix-cookie-unique; next=safe",
            "raw-suffix-cookie-unique",
            "next=safe",
        ),
        (
            "Authorization: Bearer raw-bearer-assignment-unique; status=safe",
            "raw-bearer-assignment-unique",
            "status=safe",
        ),
        (
            "request failed with Bearer raw-bearer-standalone-unique; status=safe",
            "raw-bearer-standalone-unique",
            "status=safe",
        ),
    ],
)
def test_raw_text_scrubs_quoted_unquoted_suffix_and_bearer_values(
    raw: str,
    secret: str,
    safe_fragment: str,
):
    scrubbed = redact.redact_raw_text(raw)

    assert secret not in scrubbed
    assert "[REDACTED]" in scrubbed
    assert safe_fragment in scrubbed


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        (
            "token=[REDACTED]{suffix}; status=safe",
            "token=[REDACTED]; status=safe",
        ),
        (
            "Authorization: Bearer [REDACTED]{suffix}; status=safe",
            "Authorization=[REDACTED]; status=safe",
        ),
        (
            "request failed with Bearer [REDACTED]{suffix}; status=safe",
            "request failed with Bearer [REDACTED]; status=safe",
        ),
    ],
)
@pytest.mark.parametrize("separator", ["", " "], ids=["compact", "whitespace"])
def test_pre_redacted_suffixes_are_consumed_through_the_value_boundary(
    template: str,
    expected: str,
    separator: str,
):
    secret = "raw-pre-redacted-suffix-unique"
    raw = template.format(suffix=f"{separator}{secret}")

    scrubbed = redact.redact_raw_text(raw)

    assert scrubbed == expected
    assert secret not in scrubbed


@pytest.mark.parametrize(
    "redacted_value",
    [
        "token=[REDACTED]",
        "Authorization: Bearer [REDACTED]",
        "request failed with Bearer [REDACTED]",
        'token = "[REDACTED]"',
        'Authorization: Bearer "[REDACTED]"',
        'request failed with Bearer "[REDACTED]"',
    ],
)
@pytest.mark.parametrize(
    "boundary",
    ["", "; status=safe"],
    ids=["end-of-input", "structural-delimiter"],
)
def test_complete_redacted_values_are_exactly_idempotent_at_boundaries(
    redacted_value: str,
    boundary: str,
):
    raw = f"{redacted_value}{boundary}"

    assert redact.redact_raw_text(raw) == raw


def test_raw_cookie_header_value_is_redacted_as_one_sensitive_field():
    secrets = ("raw-cookie-first-unique", "raw-cookie-second-unique")
    raw = f"Cookie: unknown={secrets[0]}; extension={secrets[1]}"

    scrubbed = redact.redact_raw_text(raw)

    assert scrubbed == "Cookie=[REDACTED]"
    assert all(secret not in scrubbed for secret in secrets)


def test_malformed_sensitive_quote_fails_closed():
    secret = "raw-malformed-quote-unique"
    attacker_tail = "raw-malformed-attacker-tail-unique"
    raw = f'prefix extension_token="{secret} {attacker_tail}'

    scrubbed = redact.redact_raw_text(raw)

    assert scrubbed == 'prefix extension_token="[REDACTED]"'
    assert secret not in scrubbed
    assert attacker_tail not in scrubbed


def test_text_fields_are_bounded_but_non_text_strings_are_not_truncated():
    long_text = "x" * 100
    payload = {
        "text": long_text,
        "nested": {"biography": long_text, "short": "brief"},
        "opaque": long_text,
    }

    scrubbed = redact.redact(payload)

    expected = f"{'x' * 40}...[redacted 60 more chars]"
    assert scrubbed["text"] == expected
    assert scrubbed["nested"]["biography"] == expected
    assert scrubbed["nested"]["short"] == "brief"
    assert scrubbed["opaque"] == long_text
    assert payload["text"] == long_text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://scontent.test.cdninstagram.com/v/t51/photo.jpg"
            "?stp=synthetic&signature=synthetic#frame",
            "https://scontent.test.cdninstagram.com/v/t51/photo.jpg#frame",
        ),
        (
            "https://fbcdn.net/video.mp4?token=synthetic-signature",
            "https://fbcdn.net/video.mp4",
        ),
        (
            "https://video.test.fbcdn.net/path/clip.mp4?oh=synthetic&oe=1",
            "https://video.test.fbcdn.net/path/clip.mp4",
        ),
    ],
)
def test_signed_threads_cdn_queries_are_stripped(url: str, expected: str):
    assert redact.is_signed_media_url(url) is True
    assert redact.redact_url(url) == expected
    assert redact.redact({"url": url}) == {"url": expected}


@pytest.mark.parametrize(
    "url",
    [
        "https://cdninstagram.com.evil.invalid/photo.jpg?keep=synthetic",
        "https://notfbcdn.net/video.mp4?keep=synthetic",
        "https://example.invalid/cdninstagram.com/photo.jpg?keep=synthetic",
    ],
)
def test_cdn_lookalikes_and_ordinary_urls_keep_their_queries(url: str):
    assert redact.is_signed_media_url(url) is False
    assert redact.redact_url(url) == url
    assert redact.redact({"url": url}) == {"url": url}


@pytest.mark.parametrize(
    "raw",
    [
        ".threads.com\tTRUE\t/\tTRUE\t0\tunknown_cookie\tcookie-helper-tsv-unique",
        "unknown_cookie=cookie-helper-pair-unique",
        '{"value": "cookie-helper-json-unique"}',
        "unstructured cookie-helper-opaque-unique",
    ],
)
def test_cookie_parse_error_always_returns_a_fixed_placeholder(raw: str):
    assert redact.redact_cookie_parse_error(raw) == "<redacted cookie data>"


def test_recursive_cycles_are_bounded_without_mutating_the_cycle():
    payload: list[object] = []
    payload.append({"self": payload, "password": "synthetic-cycle-secret"})

    scrubbed = redact.redact(payload)

    assert payload[0]["self"] is payload
    assert payload[0]["password"] == "synthetic-cycle-secret"
    assert scrubbed == [{"self": "[REDACTED CYCLE]", "password": "[REDACTED]"}]
