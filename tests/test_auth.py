import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

import pytest

from agentic_threads import auth, config
from agentic_threads.errors import InvalidCookieError, InvalidIdentifierError, LoginRequiredError

REQUIRED_COOKIES = {
    "sessionid": "synthetic-session",
    "ds_user_id": "101",
    "csrftoken": "synthetic-csrf",
}


def _credential(label: str) -> auth.SessionCredential:
    return auth.SessionCredential(
        sessionid=f"synthetic-session-{label}",
        ds_user_id=f"101{label}",
        csrftoken=f"synthetic-csrf-{label}",
        user_agent="Synthetic Test Browser/1.0",
        doc_ids={"feed": f"synthetic-doc-{label}"},
        features={"synthetic_feature": True},
        extracted_at="2026-07-23T00:00:00Z",
    )


def _raw_cookie_header() -> str:
    return "; ".join(f"{name}={value}" for name, value in REQUIRED_COOKIES.items())


def _cookie_export_with_duplicates(
    format_name: str,
    cookie_name: str,
    duplicate_values: tuple[str, str],
) -> str:
    pairs = [
        (cookie_name, duplicate_values[0]),
        (cookie_name, duplicate_values[1]),
        *[(name, value) for name, value in REQUIRED_COOKIES.items() if name != cookie_name],
    ]
    if format_name == "netscape":
        lines = ["# Netscape HTTP Cookie File"]
        lines.extend(f".threads.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}" for name, value in pairs)
        return "\n".join(lines) + "\n"

    payload = "; ".join(f"{name}={value}" for name, value in pairs)
    if format_name == "cookie-header":
        return f"Cookie: {payload}"
    if format_name == "curl":
        return f"curl 'https://www.threads.com/' -H 'Cookie: {payload}'"
    raise AssertionError(f"unsupported synthetic cookie format: {format_name}")


def test_session_credential_rendering_is_fixed_and_secret_free():
    secrets = {
        "sessionid": "credential-repr-session-unique",
        "ds_user_id": "credential-repr-user-unique",
        "csrftoken": "credential-repr-csrf-unique",
        "user_agent": "credential-repr-agent-unique",
        "doc_id": "credential-repr-doc-unique",
        "feature": "credential-repr-feature-unique",
        "extracted_at": "credential-repr-time-unique",
    }
    credential = auth.SessionCredential(
        sessionid=secrets["sessionid"],
        ds_user_id=secrets["ds_user_id"],
        csrftoken=secrets["csrftoken"],
        user_agent=secrets["user_agent"],
        doc_ids={"feed": secrets["doc_id"]},
        features={"probe": secrets["feature"]},
        extracted_at=secrets["extracted_at"],
    )

    assert repr(credential) == "SessionCredential(<redacted>)"
    assert str(credential) == "SessionCredential(<redacted>)"
    assert all(secret not in repr(credential) for secret in secrets.values())


@pytest.mark.parametrize(
    "profile",
    ["default", "Work-2", "team_alpha", "release.2026", "a" * 64],
)
def test_profile_names_resolve_as_single_basenames(tmp_path: Path, profile: str):
    assert config.profile_dir(profile, profile_dir_override=tmp_path) == tmp_path / profile
    assert (
        config.browser_profile_dir(profile, profile_dir_override=tmp_path)
        == tmp_path / profile / "browser"
    )


@pytest.mark.parametrize(
    "profile",
    [
        "",
        ".",
        "..",
        "../other",
        "safe/../../other",
        "/tmp/other",
        r"safe\other",
        r"C:\profiles\other",
        r"\\server\share",
        "C:relative",
        "bad\x00name",
        "bad\nname",
        "bad\tname",
        "a" * 65,
        "nonascii-\N{LATIN SMALL LETTER E WITH ACUTE}",
    ],
)
def test_profile_names_reject_path_like_and_control_values(tmp_path: Path, profile: str):
    with pytest.raises(InvalidIdentifierError) as exc_info:
        config.profile_dir(profile, profile_dir_override=tmp_path)

    assert str(exc_info.value) == "invalid profile name"
    assert exc_info.value.exit_code == 1


def test_save_session_is_private_and_round_trips(tmp_path: Path):
    profiles_root = tmp_path / "profiles"
    credential = _credential("one")

    session_path = auth.save_session("work", credential, profile_dir_override=profiles_root)

    assert session_path == profiles_root / "work" / auth.SESSION_FILENAME
    assert stat.S_IMODE(session_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert json.loads(session_path.read_text(encoding="utf-8")) == asdict(credential)
    assert auth.load_session("work", profile_dir_override=profiles_root) == credential
    assert not list(session_path.parent.glob(f".{auth.SESSION_FILENAME}.*.tmp"))


def test_explicit_symlinked_root_override_is_preserved(tmp_path: Path):
    actual_root = tmp_path / "actual profiles"
    actual_root.mkdir()
    root_override = tmp_path / "profiles override"
    root_override.symlink_to(actual_root, target_is_directory=True)
    credential = _credential("override")

    session_path = auth.save_session("team.profile", credential, profile_dir_override=root_override)

    assert session_path == root_override / "team.profile" / auth.SESSION_FILENAME
    assert auth.load_session("team.profile", profile_dir_override=root_override) == credential
    assert stat.S_IMODE(session_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600


def test_save_session_rejects_profile_directory_symlink(tmp_path: Path):
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    marker = outside / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    (profiles_root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LoginRequiredError) as exc_info:
        auth.save_session("linked", _credential("blocked"), profile_dir_override=profiles_root)

    assert str(exc_info.value) == "profile storage directory is unavailable or unsafe"
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
    assert not (outside / auth.SESSION_FILENAME).exists()


def test_load_session_rejects_cross_profile_directory_symlink(tmp_path: Path):
    profiles_root = tmp_path / "profiles"
    credential = _credential("victim")
    victim_session = auth.save_session("victim", credential, profile_dir_override=profiles_root)
    victim_bytes = victim_session.read_bytes()
    victim_session.parent.chmod(0o750)
    (profiles_root / "alias").symlink_to(victim_session.parent, target_is_directory=True)

    with pytest.raises(LoginRequiredError) as exc_info:
        auth.load_session("alias", profile_dir_override=profiles_root)

    assert str(exc_info.value) == "profile storage directory is unavailable or unsafe"
    assert victim_session.read_bytes() == victim_bytes
    assert stat.S_IMODE(victim_session.parent.stat().st_mode) == 0o750


@pytest.mark.parametrize("operation", ["save", "load"])
def test_regular_file_is_rejected_as_profile_directory(tmp_path: Path, operation: str):
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    blocked_path = profiles_root / "blocked"
    blocked_path.write_text("unchanged", encoding="utf-8")

    with pytest.raises(LoginRequiredError) as exc_info:
        if operation == "save":
            auth.save_session("blocked", _credential("blocked"), profile_dir_override=profiles_root)
        else:
            auth.load_session("blocked", profile_dir_override=profiles_root)

    assert str(exc_info.value) == "profile storage directory is unavailable or unsafe"
    assert blocked_path.read_text(encoding="utf-8") == "unchanged"


def test_ensure_profile_dir_rejects_browser_directory_symlink(tmp_path: Path):
    profiles_root = tmp_path / "profiles"
    profile_path = profiles_root / "work"
    profile_path.mkdir(parents=True)
    outside_browser = tmp_path / "outside-browser"
    outside_browser.mkdir()
    outside_browser.chmod(0o755)
    browser_path = config.browser_profile_dir("work", profile_dir_override=profiles_root)
    browser_path.symlink_to(outside_browser, target_is_directory=True)

    with pytest.raises(LoginRequiredError) as exc_info:
        auth.ensure_profile_dir(browser_path)

    assert str(exc_info.value) == "profile storage directory is unavailable or unsafe"
    assert stat.S_IMODE(outside_browser.stat().st_mode) == 0o755


def test_failed_atomic_replace_preserves_previous_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    profiles_root = tmp_path / "profiles"
    session_path = auth.save_session(
        "atomic", _credential("old"), profile_dir_override=profiles_root
    )
    previous = session_path.read_bytes()
    observed: dict[str, object] = {}

    def fail_replace(source, destination, *, src_dir_fd, dst_dir_fd):
        source_stat = os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False)
        observed["source_is_temporary"] = (
            isinstance(source, str)
            and source.startswith(f".{auth.SESSION_FILENAME}.")
            and source.endswith(".tmp")
        )
        observed["source_mode"] = stat.S_IMODE(source_stat.st_mode)
        observed["destination"] = destination
        observed["same_directory_fd"] = src_dir_fd == dst_dir_fd
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(auth.os, "replace", fail_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        auth.save_session("atomic", _credential("new"), profile_dir_override=profiles_root)

    assert session_path.read_bytes() == previous
    assert observed == {
        "source_is_temporary": True,
        "source_mode": 0o600,
        "destination": auth.SESSION_FILENAME,
        "same_directory_fd": True,
    }
    assert not list(session_path.parent.glob(f".{auth.SESSION_FILENAME}.*.tmp"))


def test_load_session_rejects_missing_file(tmp_path: Path):
    with pytest.raises(LoginRequiredError, match="no session"):
        auth.load_session("missing", profile_dir_override=tmp_path)


@pytest.mark.parametrize(
    "contents",
    [
        "{not valid json",
        "[]",
        json.dumps({"sessionid": "synthetic-session-only"}),
    ],
)
def test_load_session_rejects_corrupt_or_incomplete_file(tmp_path: Path, contents: str):
    profile_dir = tmp_path / "broken"
    profile_dir.mkdir()
    (profile_dir / auth.SESSION_FILENAME).write_text(contents, encoding="utf-8")

    with pytest.raises(LoginRequiredError, match="saved session"):
        auth.load_session("broken", profile_dir_override=tmp_path)


def test_load_session_rejects_session_file_symlink(tmp_path: Path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(asdict(_credential("outside"))), encoding="utf-8")
    profile_dir = tmp_path / "linked"
    profile_dir.mkdir()
    (profile_dir / auth.SESSION_FILENAME).symlink_to(outside)

    with pytest.raises(LoginRequiredError, match="opened safely"):
        auth.load_session("linked", profile_dir_override=tmp_path)


@pytest.mark.parametrize("wrapped", [False, True])
def test_json_cookie_map_formats_remain_supported(tmp_path: Path, wrapped: bool):
    payload: object = {"cookies": REQUIRED_COOKIES} if wrapped else REQUIRED_COOKIES
    path = tmp_path / "cookie-map.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("reverse_duplicates", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("cookie_name", REQUIRED_COOKIES)
def test_json_cookie_maps_reject_conflicting_duplicate_required_keys(
    tmp_path: Path,
    cookie_name: str,
    wrapped: bool,
    reverse_duplicates: bool,
):
    secrets = [
        f"json-key-first-{cookie_name}-{reverse_duplicates}-unique",
        f"json-key-second-{cookie_name}-{reverse_duplicates}-unique",
    ]
    if reverse_duplicates:
        secrets.reverse()
    entries = [
        (cookie_name, secrets[0]),
        (cookie_name, secrets[1]),
        *[(name, value) for name, value in REQUIRED_COOKIES.items() if name != cookie_name],
    ]
    serialized_entries = ",".join(
        f"{json.dumps(name)}:{json.dumps(value)}" for name, value in entries
    )
    cookie_map = f"{{{serialized_entries}}}"
    contents = f'{{"cookies":{cookie_map}}}' if wrapped else cookie_map
    path = tmp_path / "duplicate-json-key.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert message == "conflicting JSON cookie object key"
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("cookie_name", REQUIRED_COOKIES)
def test_json_cookie_maps_coalesce_identical_duplicate_required_keys(
    tmp_path: Path,
    cookie_name: str,
    wrapped: bool,
):
    entries = [
        (cookie_name, REQUIRED_COOKIES[cookie_name]),
        (cookie_name, REQUIRED_COOKIES[cookie_name]),
        *[(name, value) for name, value in REQUIRED_COOKIES.items() if name != cookie_name],
    ]
    serialized_entries = ",".join(
        f"{json.dumps(name)}:{json.dumps(value)}" for name, value in entries
    )
    cookie_map = f"{{{serialized_entries}}}"
    contents = f'{{"cookies":{cookie_map}}}' if wrapped else cookie_map
    path = tmp_path / "identical-json-key.json"
    path.write_text(contents, encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("field_name", ["value", "domain"])
def test_json_cookie_records_reject_conflicting_duplicate_fields(
    tmp_path: Path,
    field_name: str,
):
    secrets = (
        f"json-record-{field_name}-first-secret-unique",
        f"json-record-{field_name}-second-secret-unique",
    )
    if field_name == "value":
        duplicate_values = secrets
        remaining_field = ("domain", "threads.com")
    else:
        duplicate_values = tuple(f"{secret}.threads.com" for secret in secrets)
        remaining_field = ("value", REQUIRED_COOKIES["sessionid"])

    fields = [
        ("name", "sessionid"),
        (field_name, duplicate_values[0]),
        (field_name, duplicate_values[1]),
        remaining_field,
    ]
    serialized_fields = ",".join(
        f"{json.dumps(name)}:{json.dumps(value)}" for name, value in fields
    )
    serialized_records = [
        f"{{{serialized_fields}}}",
        *[
            json.dumps({"name": name, "value": value, "domain": "threads.com"})
            for name, value in REQUIRED_COOKIES.items()
            if name != "sessionid"
        ],
    ]
    path = tmp_path / f"conflicting-duplicate-{field_name}.json"
    path.write_text(f"[{','.join(serialized_records)}]", encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert message == "conflicting JSON cookie object key"
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize("field_name", ["value", "domain"])
def test_json_cookie_records_coalesce_identical_duplicate_fields(
    tmp_path: Path,
    field_name: str,
):
    secret = f"json-record-identical-{field_name}-secret-unique"
    if field_name == "value":
        duplicate_value = secret
        remaining_field = ("domain", "threads.com")
        expected = {**REQUIRED_COOKIES, "sessionid": secret}
    else:
        duplicate_value = f"{secret}.threads.com"
        remaining_field = ("value", REQUIRED_COOKIES["sessionid"])
        expected = REQUIRED_COOKIES

    fields = [
        ("name", "sessionid"),
        (field_name, duplicate_value),
        (field_name, duplicate_value),
        remaining_field,
    ]
    serialized_fields = ",".join(
        f"{json.dumps(name)}:{json.dumps(value)}" for name, value in fields
    )
    serialized_records = [
        f"{{{serialized_fields}}}",
        *[
            json.dumps({"name": name, "value": value, "domain": "threads.com"})
            for name, value in REQUIRED_COOKIES.items()
            if name != "sessionid"
        ],
    ]
    path = tmp_path / f"identical-duplicate-{field_name}.json"
    path.write_text(f"[{','.join(serialized_records)}]", encoding="utf-8")

    assert auth.parse_cookie_file(path) == expected


@pytest.mark.parametrize(
    "domain",
    ["threads.com", ".threads.net", "www.threads.com", "edge.threads.net"],
)
def test_json_cookie_import_accepts_threads_domains_and_filters_decoys(tmp_path: Path, domain: str):
    records = [
        {"name": name, "value": value, "domain": domain} for name, value in REQUIRED_COOKIES.items()
    ]
    records.extend(
        [
            {
                "name": name,
                "value": f"untrusted-{name}",
                "domain": "threads.com.evil.invalid",
            }
            for name in REQUIRED_COOKIES
        ]
    )
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


def test_json_cookie_records_reject_domainless_required_cookies(tmp_path: Path):
    secrets = {
        name: f"domainless-only-{index}-unique"
        for index, name in enumerate(REQUIRED_COOKIES, start=1)
    }
    records = [{"name": name, "value": value} for name, value in secrets.items()]
    path = tmp_path / "domainless.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert "domain" in message
    assert all(secret not in message for secret in secrets.values())


@pytest.mark.parametrize(
    ("domain", "secret"),
    [
        (None, "malformed-domain-none-unique"),
        ("", "malformed-domain-empty-unique"),
        ("   ", "malformed-domain-whitespace-unique"),
        (17, "malformed-domain-type-unique"),
    ],
)
def test_json_cookie_records_reject_malformed_required_domains(
    tmp_path: Path,
    domain: object,
    secret: str,
):
    records = [{"name": "sessionid", "value": secret, "domain": domain}]
    path = tmp_path / "malformed-domain.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    assert "domain" in str(exc_info.value)
    assert secret not in str(exc_info.value)


@pytest.mark.parametrize("unscoped_first", [True, False])
def test_json_cookie_records_reject_unscoped_required_cookie_in_either_order(
    tmp_path: Path,
    unscoped_first: bool,
):
    unscoped_secret = f"trusted-unscoped-order-{unscoped_first}-unique"
    trusted = [
        {"name": name, "value": value, "domain": ".threads.com"}
        for name, value in REQUIRED_COOKIES.items()
    ]
    unscoped = {"name": "sessionid", "value": unscoped_secret}
    records = [unscoped, *trusted] if unscoped_first else [*trusted, unscoped]
    path = tmp_path / "trusted-and-unscoped.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    assert "domain" in str(exc_info.value)
    assert unscoped_secret not in str(exc_info.value)


@pytest.mark.parametrize("reverse_duplicates", [False, True])
def test_json_cookie_records_reject_conflicting_trusted_duplicates_in_either_order(
    tmp_path: Path,
    reverse_duplicates: bool,
):
    first_secret = f"trusted-duplicate-first-{reverse_duplicates}-unique"
    second_secret = f"trusted-duplicate-second-{reverse_duplicates}-unique"
    duplicates = [
        {"name": "sessionid", "value": first_secret, "domain": "threads.com"},
        {"name": "sessionid", "value": second_secret, "domain": ".threads.com"},
    ]
    if reverse_duplicates:
        duplicates.reverse()
    records = [
        *duplicates,
        {
            "name": "ds_user_id",
            "value": REQUIRED_COOKIES["ds_user_id"],
            "domain": "threads.com",
        },
        {
            "name": "csrftoken",
            "value": REQUIRED_COOKIES["csrftoken"],
            "domain": "threads.com",
        },
    ]
    path = tmp_path / "conflicting-trusted.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert "conflicting JSON cookie" in message
    assert first_secret not in message
    assert second_secret not in message


def test_json_cookie_records_coalesce_identical_trusted_duplicates(tmp_path: Path):
    records = [
        {
            "name": "sessionid",
            "value": REQUIRED_COOKIES["sessionid"],
            "domain": "threads.com",
        },
        {
            "name": "sessionid",
            "value": REQUIRED_COOKIES["sessionid"],
            "domain": ".threads.com",
        },
        *[
            {"name": name, "value": value, "domain": "www.threads.com"}
            for name, value in REQUIRED_COOKIES.items()
            if name != "sessionid"
        ],
    ]
    path = tmp_path / "identical-trusted.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


def test_netscape_cookie_import_filters_non_threads_domains(tmp_path: Path):
    lines = ["# Netscape HTTP Cookie File"]
    lines.extend(
        f".threads.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}"
        for name, value in REQUIRED_COOKIES.items()
    )
    lines.append(".evil.invalid\tTRUE\t/\tTRUE\t0\tsessionid\tuntrusted-session")
    path = tmp_path / "cookies.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("reverse_duplicates", [False, True])
@pytest.mark.parametrize("format_name", ["netscape", "cookie-header", "curl"])
@pytest.mark.parametrize("cookie_name", REQUIRED_COOKIES)
def test_cookie_text_formats_reject_conflicting_required_duplicates(
    tmp_path: Path,
    cookie_name: str,
    format_name: str,
    reverse_duplicates: bool,
):
    secrets = [
        f"{format_name}-first-{cookie_name}-{reverse_duplicates}-unique",
        f"{format_name}-second-{cookie_name}-{reverse_duplicates}-unique",
    ]
    if reverse_duplicates:
        secrets.reverse()
    path = tmp_path / f"conflicting-{format_name}.txt"
    path.write_text(
        _cookie_export_with_duplicates(format_name, cookie_name, (secrets[0], secrets[1])),
        encoding="utf-8",
    )

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    expected = (
        "conflicting Netscape cookie at line 3"
        if format_name == "netscape"
        else "conflicting cookie header at segment 2"
    )
    assert message == expected
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize("format_name", ["netscape", "cookie-header", "curl"])
@pytest.mark.parametrize("cookie_name", REQUIRED_COOKIES)
def test_cookie_text_formats_coalesce_identical_required_duplicates(
    tmp_path: Path,
    cookie_name: str,
    format_name: str,
):
    value = REQUIRED_COOKIES[cookie_name]
    path = tmp_path / f"identical-{format_name}.txt"
    path.write_text(
        _cookie_export_with_duplicates(format_name, cookie_name, (value, value)),
        encoding="utf-8",
    )

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("reverse_headers", [False, True])
@pytest.mark.parametrize("input_kind", ["curl", "raw-lines"])
def test_cookie_header_imports_reject_conflicts_across_separate_headers_in_either_order(
    tmp_path: Path,
    input_kind: str,
    reverse_headers: bool,
):
    secrets = [
        f"{input_kind}-separate-header-first-{reverse_headers}-unique",
        f"{input_kind}-separate-header-second-{reverse_headers}-unique",
    ]
    payloads = [
        f"sessionid={secrets[0]}; ds_user_id={REQUIRED_COOKIES['ds_user_id']}",
        f"sessionid={secrets[1]}; csrftoken={REQUIRED_COOKIES['csrftoken']}",
    ]
    if reverse_headers:
        payloads.reverse()

    if input_kind == "curl":
        contents = (
            "curl 'https://www.threads.com/' "
            f"-H 'Cookie: {payloads[0]}' "
            f'--header="Cookie: {payloads[1]}"'
        )
    else:
        contents = "\n".join(f"Cookie: {payload}" for payload in payloads)

    path = tmp_path / f"conflicting-separate-{input_kind}.txt"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert message == "conflicting cookie header at segment 1"
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize("reverse_headers", [False, True])
@pytest.mark.parametrize("input_kind", ["curl", "raw-lines"])
def test_cookie_header_imports_merge_identical_values_across_split_headers_in_either_order(
    tmp_path: Path,
    input_kind: str,
    reverse_headers: bool,
):
    payloads = [
        (f"sessionid={REQUIRED_COOKIES['sessionid']}; ds_user_id={REQUIRED_COOKIES['ds_user_id']}"),
        (f"sessionid={REQUIRED_COOKIES['sessionid']}; csrftoken={REQUIRED_COOKIES['csrftoken']}"),
    ]
    if reverse_headers:
        payloads.reverse()

    if input_kind == "curl":
        contents = (
            "curl 'https://www.threads.com/' "
            f"-H 'Cookie: {payloads[0]}' "
            f'--header="Cookie: {payloads[1]}"'
        )
    else:
        contents = "\n".join(f"Cookie: {payload}" for payload in payloads)

    path = tmp_path / f"identical-split-{input_kind}.txt"
    path.write_text(contents, encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("reverse_sources", [False, True])
@pytest.mark.parametrize(
    "curl_header_template",
    [
        "-H 'Cookie: {payload}'",
        "--header='Cookie: {payload}'",
    ],
)
def test_mixed_curl_and_raw_cookie_headers_merge_identical_values_in_either_order(
    tmp_path: Path,
    curl_header_template: str,
    reverse_sources: bool,
):
    curl_payload = (
        f"sessionid={REQUIRED_COOKIES['sessionid']}; ds_user_id={REQUIRED_COOKIES['ds_user_id']}"
    )
    raw_payload = (
        f"sessionid={REQUIRED_COOKIES['sessionid']}; csrftoken={REQUIRED_COOKIES['csrftoken']}"
    )
    sources = [
        (f"curl 'https://www.threads.com/' {curl_header_template.format(payload=curl_payload)}"),
        f"Cookie: {raw_payload}",
    ]
    if reverse_sources:
        sources.reverse()

    path = tmp_path / "identical-mixed-cookie-sources.txt"
    path.write_text("\n".join(sources), encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


@pytest.mark.parametrize("reverse_sources", [False, True])
@pytest.mark.parametrize(
    "curl_header_template",
    [
        "-H 'Cookie: {payload}'",
        "--header='Cookie: {payload}'",
    ],
)
def test_mixed_curl_and_raw_cookie_headers_reject_conflicts_in_either_order(
    tmp_path: Path,
    curl_header_template: str,
    reverse_sources: bool,
):
    secrets = (
        "mixed-curl-session-secret-unique",
        "mixed-raw-session-secret-unique",
    )
    curl_payload = f"sessionid={secrets[0]}; ds_user_id={REQUIRED_COOKIES['ds_user_id']}"
    raw_payload = f"sessionid={secrets[1]}; csrftoken={REQUIRED_COOKIES['csrftoken']}"
    sources = [
        (f"curl 'https://www.threads.com/' {curl_header_template.format(payload=curl_payload)}"),
        f"Cookie: {raw_payload}",
    ]
    if reverse_sources:
        sources.reverse()

    path = tmp_path / "conflicting-mixed-cookie-sources.txt"
    path.write_text("\n".join(sources), encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert message == "conflicting cookie header at segment 1"
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize(
    "contents",
    [
        "curl 'https://www.threads.com/' -H 'Accept: application/json'",
        ("curl 'https://www.threads.com/' -H Cookie: sessionid=malformed-curl-session-unique"),
    ],
)
def test_curl_without_a_recognized_cookie_header_remains_rejected(
    tmp_path: Path,
    contents: str,
):
    path = tmp_path / "curl-without-cookie.txt"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(
        InvalidCookieError,
        match="^cURL input does not contain a Cookie header$",
    ) as exc_info:
        auth.parse_cookie_file(path)

    assert "malformed-curl-session-unique" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("malformed_line", "field_count", "secrets"),
    [
        (
            ".threads.com\tTRUE\t/\tTRUE\t0\tsessionid\t"
            "netscape-extra-cookie-unique\tnetscape-extra-tail-unique",
            8,
            ("netscape-extra-cookie-unique", "netscape-extra-tail-unique"),
        ),
        (
            ".threads.com\tTRUE\t/\tTRUE\t0\tsessionid=netscape-missing-unique",
            6,
            ("netscape-missing-unique",),
        ),
        (
            "\t.threads.com\tTRUE\t/\tTRUE\t0\tsessionid\tnetscape-leading-unique",
            8,
            ("netscape-leading-unique",),
        ),
        (
            ".threads.com\tTRUE\t/\tTRUE\t0\tsessionid\tnetscape-trailing-unique\t",
            8,
            ("netscape-trailing-unique",),
        ),
    ],
)
def test_malformed_netscape_tab_shapes_report_only_synthetic_context(
    tmp_path: Path,
    malformed_line: str,
    field_count: int,
    secrets: tuple[str, ...],
):
    path = tmp_path / "malformed-netscape.txt"
    path.write_text(f"# Netscape HTTP Cookie File\n{malformed_line}\n", encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert message == (
        f"malformed Netscape cookie line 2 ({field_count} fields): <redacted cookie data>"
    )
    assert all(secret not in message for secret in secrets)


@pytest.mark.parametrize(
    "contents",
    [
        _raw_cookie_header(),
        f"Cookie: {_raw_cookie_header()}",
        f"curl 'https://www.threads.com/' -H 'Cookie: {_raw_cookie_header()}'",
    ],
)
def test_raw_cookie_and_curl_import(contents: str, tmp_path: Path):
    path = tmp_path / "cookies.txt"
    path.write_text(contents, encoding="utf-8")

    assert auth.parse_cookie_file(path) == REQUIRED_COOKIES


def test_from_cookie_file_persists_only_required_cookies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = tmp_path / "browser-cookies.txt"
    source.write_text(
        f"{_raw_cookie_header()}; mid=synthetic-extra-cookie",
        encoding="utf-8",
    )
    profiles_root = tmp_path / "profiles"

    imported = auth.from_cookie_file(
        source,
        "imported",
        profile_dir_override=profiles_root,
    )

    assert imported.cookies == REQUIRED_COOKIES
    assert imported.user_agent == auth.DEFAULT_USER_AGENT
    assert auth.load_session("imported", profile_dir_override=profiles_root) == imported
    persisted = json.loads(
        (profiles_root / "imported" / auth.SESSION_FILENAME).read_text(encoding="utf-8")
    )
    assert "mid" not in persisted
    warning = capsys.readouterr().err
    assert "delete or secure it" in warning
    assert "synthetic-extra-cookie" not in warning


def test_cookie_import_reports_missing_name_without_echoing_present_secret(
    tmp_path: Path,
):
    path = tmp_path / "cookies.txt"
    path.write_text("sessionid=synthetic-private-value; ds_user_id=101", encoding="utf-8")

    with pytest.raises(InvalidCookieError) as exc_info:
        auth.parse_cookie_file(path)

    message = str(exc_info.value)
    assert "csrftoken" in message
    assert "synthetic-private-value" not in message


@pytest.mark.parametrize(
    ("raw", "by", "expected"),
    [
        ("synthetic_alice", None, ("username", "synthetic_alice")),
        ("@synthetic.alice", None, ("username", "synthetic.alice")),
        ("123456", None, ("user_id", "123456")),
        ("@123456", None, ("username", "123456")),
        ("123456", "username", ("username", "123456")),
        ("123456", "id", ("user_id", "123456")),
        ("123456", "user_id", ("user_id", "123456")),
    ],
)
def test_user_identifier_normalization(raw: str, by: str | None, expected: tuple[str, str]):
    assert auth.normalize_user_identifier(raw, by=by) == expected
    assert auth.normalize_identifier(raw, by=by) == expected


def test_explicit_id_mode_rejects_a_username():
    with pytest.raises(InvalidIdentifierError, match="requires a numeric"):
        auth.normalize_user_identifier("synthetic_alice", by="id")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.threads.com/@synthetic_alice?hl=en",
            ("username", "synthetic_alice"),
        ),
        ("threads.net/synthetic.alice/", ("username", "synthetic.alice")),
    ],
)
def test_threads_profile_urls(raw: str, expected: tuple[str, str]):
    assert auth.normalize_user_identifier(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://threads.com/@synthetic_alice/post/AQ?share=synthetic",
        "www.threads.net/synthetic_alice/post/AQ/",
    ],
)
def test_threads_post_urls(raw: str):
    assert auth.normalize_post_identifier(raw) == ("shortcode", "AQ")


def test_profile_and_post_url_spaces_are_not_interchangeable():
    with pytest.raises(InvalidIdentifierError, match="profile URL"):
        auth.normalize_post_identifier("https://threads.com/@synthetic_alice")
    with pytest.raises(InvalidIdentifierError, match="post URL"):
        auth.normalize_user_identifier("https://threads.com/@synthetic_alice/post/AQ")


@pytest.mark.parametrize(
    "raw",
    [
        "https://threads.com.evil.invalid/@synthetic_alice",
        "https://evil.invalid/threads.com/@synthetic_alice",
        "ftp://threads.com/@synthetic_alice",
    ],
)
def test_threads_url_parser_rejects_untrusted_hosts_and_schemes(raw: str):
    with pytest.raises(InvalidIdentifierError):
        auth.normalize_user_identifier(raw)


def test_numeric_post_id_and_exact_shortcode_candidates():
    assert auth.normalize_post_identifier("4001") == ("post_id", "4001")
    assert auth.normalize_post_identifier("AQ") == ("shortcode", "AQ")
    assert auth.shortcode_to_post_id_candidates("AQ") == ("4", "5", "6", "7")


def test_invalid_shortcode_is_rejected():
    with pytest.raises(InvalidIdentifierError, match="shortcode"):
        auth.shortcode_to_post_id_candidates("not*a*shortcode")
