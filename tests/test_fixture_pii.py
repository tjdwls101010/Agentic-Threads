from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_script(filename: str, module_name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


scanner = _load_script("check_fixtures_pii.py", "agentic_threads_fixture_pii_scanner")
recorder = _load_script("record_fixture.py", "agentic_threads_fixture_recorder")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("path", scanner.fixture_paths(), ids=lambda path: path.name)
def test_every_committed_fixture_passes_structural_scan(path):
    assert scanner.scan_file(path) == []


def test_safe_synthetic_conventions_graphql_fields_and_prose_are_allowed(tmp_path):
    path = tmp_path / "synthetic.json"
    _write_json(
        path,
        {
            "data": {
                "xdt_api__v1__users__search_connection": {
                    ("__relay_internal__pv__BarcelonaOptionalCookiesEnabledrelayprovider"): True,
                    "user": {
                        "pk": "101",
                        "username": "synthetic_alice",
                        "full_name": "Synthetic Alice",
                        "code": "root1",
                    },
                    "page_info": {
                        "end_cursor": "synthetic_cursor_page_2",
                        "has_next_page": True,
                    },
                    "profile_pic_url": "https://assets.example.invalid/profiles/101.png",
                    "contact": "qa@example.invalid",
                    "description": ("Ordinary prose about a hand-authored GraphQL response shape."),
                }
            }
        },
    )

    assert scanner.scan_file(path) == []


@pytest.mark.parametrize(
    ("payload", "category"),
    (
        pytest.param({"auth_token": "synthetic"}, "credential-key", id="token-key"),
        pytest.param({"cookie_jar": "synthetic"}, "credential-key", id="cookie-key"),
        pytest.param({"userPassword": "synthetic"}, "credential-key", id="password-key"),
        pytest.param(
            {"caption": {"text": "Contact person@example.com"}},
            "email",
            id="email",
        ),
        pytest.param(
            {"caption": {"text": "Call +1 415-555-2671"}},
            "phone",
            id="phone",
        ),
        pytest.param(
            {"media_url": "https://cdn.example.com/private/photo.jpg"},
            "hostname",
            id="hostname",
        ),
        pytest.param(
            {"opaque": "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"},
            "high-entropy-secret",
            id="secret",
        ),
        pytest.param(
            {"user": {"pk": "9876543210123456789"}},
            "identifier",
            id="numeric-id",
        ),
        pytest.param(
            {"user": {"username": "person_account"}},
            "identifier",
            id="username",
        ),
        pytest.param(
            {"user": {"full_name": "Person Name"}},
            "identifier",
            id="full-name",
        ),
        pytest.param(
            {"page_info": {"end_cursor": "opaqueCursorValue"}},
            "identifier",
            id="cursor",
        ),
        pytest.param(
            {"post": {"code": "DAbCdEfGh12"}},
            "identifier",
            id="shortcode",
        ),
    ),
)
def test_synthetic_rejects_cover_each_structural_category(
    tmp_path,
    payload,
    category,
):
    path = tmp_path / "rejected.json"
    _write_json(path, payload)

    findings = scanner.scan_file(path)

    assert f"rejected.json: {category}" in findings


def test_invalid_json_is_rejected_without_echoing_input(tmp_path):
    path = tmp_path / "broken.json"
    suspect = "person@example.com"
    path.write_text(f'{{"value": "{suspect}"', encoding="utf-8")

    findings = scanner.scan_file(path)

    assert findings == ["broken.json: invalid-json"]
    assert suspect not in "\n".join(findings)


def test_fixture_symlink_is_rejected_without_following_it(tmp_path):
    target = tmp_path / "outside.json"
    _write_json(target, {"email": "person@example.com"})
    link = tmp_path / "linked.json"
    link.symlink_to(target)

    findings = scanner.scan_file(link)

    assert findings == ["linked.json: unsafe-file"]
    assert "email" not in findings


def test_fixture_scope_is_direct_json_children_only(tmp_path):
    _write_json(tmp_path / "safe.json", {"username": "synthetic_account"})
    (tmp_path / "ignored.txt").write_text(
        "person@example.com",
        encoding="utf-8",
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    _write_json(nested / "ignored.json", {"email": "person@example.com"})

    assert [path.name for path in scanner.fixture_paths(tmp_path)] == ["safe.json"]
    assert scanner.scan_fixtures(tmp_path) == []


def test_script_diagnostics_never_reflect_suspect_values(
    tmp_path,
    monkeypatch,
    capsys,
):
    credential_key = "pass" + "word"
    credential_value = hashlib.sha256(b"fixture entropy probe").hexdigest()
    suspects = {
        credential_key: credential_value,
        "email": "person@example.com",
        "phone": "+1 415-555-2671",
        "media_url": "https://cdn.example.com/private/photo.jpg",
        "pk": "9876543210123456789",
    }
    _write_json(tmp_path / "suspects.json", suspects)
    monkeypatch.setattr(scanner, "FIXTURES_DIR", tmp_path)

    assert scanner.main() == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "suspects.json" in output.err
    for category in (
        "credential-key",
        "email",
        "phone",
        "hostname",
        "high-entropy-secret",
        "identifier",
    ):
        assert category in output.err
    for suspect in suspects.values():
        assert suspect not in output.err


def test_capture_writes_valid_json_only_to_private_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)
    payload = b'{"data":{"shape":"synthetic"}}\n'

    destination = recorder.write_capture("shape", payload)

    assert destination == scratch / "shape.raw.json"
    assert destination.read_bytes() == payload
    assert stat.S_IMODE(scratch.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "name",
    (
        "../escape",
        "/absolute",
        "nested/escape",
        "tests/fixtures/leak",
        "..",
        "bad\\path",
    ),
)
def test_capture_refuses_path_traversal(name, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)

    with pytest.raises(recorder.CaptureError, match="safe filename stem"):
        recorder.write_capture(name, b"{}")

    assert not scratch.exists()


def test_capture_refuses_fixture_directory_even_if_reconfigured(
    tmp_path,
    monkeypatch,
):
    fixtures = tmp_path / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    monkeypatch.setattr(recorder, "SCRATCH_DIR", fixtures)
    monkeypatch.setattr(recorder, "FIXTURES_DIR", fixtures)

    with pytest.raises(recorder.CaptureError, match="may not be tests/fixtures"):
        recorder.write_capture("leak", b"{}")

    assert list(fixtures.iterdir()) == []


def test_capture_refuses_invalid_json_before_creating_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)

    with pytest.raises(recorder.CaptureError, match="valid JSON"):
        recorder.write_capture("broken", b'{"broken":')

    assert not scratch.exists()


def test_capture_refuses_existing_destination_without_overwrite(
    tmp_path,
    monkeypatch,
):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)
    first = b'{"capture":"synthetic-first"}'
    destination = recorder.write_capture("once", first)

    with pytest.raises(recorder.CaptureError, match="refusing to overwrite"):
        recorder.write_capture("once", b'{"capture":"synthetic-second"}')

    assert destination.read_bytes() == first


def test_capture_refuses_scratch_directory_symlink(tmp_path, monkeypatch):
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    scratch = tmp_path / "scratch"
    scratch.symlink_to(real_directory, target_is_directory=True)
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)

    with pytest.raises(recorder.CaptureError, match="not a symlink"):
        recorder.write_capture("linked", b"{}")

    assert list(real_directory.iterdir()) == []


def test_capture_refuses_existing_destination_symlink(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"outside":"unchanged"}')
    (scratch / "linked.raw.json").symlink_to(outside)
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)

    with pytest.raises(recorder.CaptureError, match="refusing to overwrite"):
        recorder.write_capture("linked", b'{"capture":"synthetic"}')

    assert outside.read_bytes() == b'{"outside":"unchanged"}'


def test_capture_cli_emits_strong_pii_warning(tmp_path, monkeypatch, capsys):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(recorder, "SCRATCH_DIR", scratch)
    monkeypatch.setattr(
        recorder.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b'{"capture":"synthetic"}')),
    )

    assert recorder.main(["warning-check"]) == 0

    output = capsys.readouterr()
    assert "CREDENTIALS" in output.err
    assert "PII" in output.err
    assert "NEVER BE COMMITTED" in output.err
    assert "tests/fixtures" in output.err
    assert "warning-check.raw.json" in output.out
