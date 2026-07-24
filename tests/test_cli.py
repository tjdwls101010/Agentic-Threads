from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_threads import cli, errors, model, retrieve, session

META_COMMANDS = {"login", "status", "setup", "doctor", "catalog", "schema"}
READ_COMMANDS = {"feed", "fetch", "post", "search", "followers", "following"}
ALL_COMMANDS = META_COMMANDS | READ_COMMANDS
COMMON_READ_FLAGS = {
    "--format",
    "--output",
    "--limit",
    "--wait-on-limit",
    "--max-wait",
    "--profile",
    "--profile-dir",
    "--raw",
    "--no-redact",
    "--verbose",
}
EXPECTED_FLAGS = {
    "login": {"--profile", "--profile-dir", "--cookies", "--timeout-seconds"},
    "status": {"--profile", "--profile-dir", "--json"},
    "setup": {"--force"},
    "doctor": {"--profile", "--profile-dir", "--refresh"},
    "catalog": {"--json"},
    "schema": {"--json"},
    "feed": COMMON_READ_FLAGS | {"--since", "--until"},
    "fetch": COMMON_READ_FLAGS | {"--replies", "--by", "--since", "--until"},
    "post": COMMON_READ_FLAGS | {"--no-replies"},
    "search": COMMON_READ_FLAGS | {"--type", "--since", "--until"},
    "followers": COMMON_READ_FLAGS,
    "following": COMMON_READ_FLAGS,
}


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    return action.choices


def _post(post_id: str = "100") -> model.Post:
    built = model.build_post(
        {
            "pk": post_id,
            "code": f"S{post_id}",
            "taken_at": int(datetime(2026, 7, 1, 12, tzinfo=UTC).timestamp()),
            "caption": {"text": f"synthetic post {post_id}"},
            "user": {"pk": "10", "username": "synthetic_author"},
            "text_post_app_info": {},
        },
        captured_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    assert built is not None
    return built


def _user(user_id: str, username: str) -> model.User:
    built = model.build_user(
        {
            "pk": user_id,
            "username": username,
            "full_name": f"Synthetic {username}",
            "is_verified": False,
        }
    )
    assert built is not None
    return built


def test_parser_handler_and_command_sets_are_exactly_in_sync():
    assert set(_subparsers()) == ALL_COMMANDS
    assert set(cli._HANDLERS) == ALL_COMMANDS


@pytest.mark.parametrize("command", sorted(ALL_COMMANDS))
def test_every_command_help_is_reachable_and_lists_its_flags(command, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.build_parser().parse_args([command, "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert f"usage: agentic-threads {command}" in help_text
    for flag in EXPECTED_FLAGS[command]:
        assert flag in help_text


def test_top_level_help_and_version_are_offline_surfaces(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.build_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "agentic-threads" in help_text
    for command in ALL_COMMANDS:
        assert command in help_text

    with pytest.raises(SystemExit) as version_exit:
        cli.main(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.startswith("agentic-threads 0.1.1")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown-command"],
        ["feed", "--unknown-flag"],
        ["feed", "--since", "07/01/2026"],
        ["search"],
        ["search", "synthetic", "--type", "people", "--since", "2026-07-01"],
        ["search", "synthetic", "--type", "people", "--until", "2026-07-01"],
        ["feed", "--limit", "-1"],
        ["feed", "--max-wait", "1"],
        ["feed", "--max-wait", "-1", "--wait-on-limit"],
        ["feed", "--no-redact"],
        ["fetch", "@threads", "--since", "2026-07-02", "--until", "2026-07-01"],
        ["login", "--timeout-seconds", "-1"],
    ],
)
def test_usage_errors_exit_one_not_the_auth_code(argv, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 1
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
@pytest.mark.parametrize(
    ("argv", "option", "command", "message"),
    [
        (
            ["feed", "--wait-on-limit"],
            "--max-wait",
            "feed",
            "--max-wait must be finite",
        ),
        (
            ["login"],
            "--timeout-seconds",
            "login",
            "--timeout-seconds must be finite",
        ),
    ],
)
def test_non_finite_bounds_exit_one_before_handler_dispatch(
    monkeypatch,
    capsys,
    value,
    argv,
    option,
    command,
    message,
):
    handler_calls: list[argparse.Namespace] = []
    monkeypatch.setitem(
        cli._HANDLERS,
        command,
        lambda args: handler_calls.append(args) or 0,
    )

    with pytest.raises(SystemExit) as caught:
        cli.main([*argv, f"{option}={value}"])

    assert caught.value.code == 1
    assert handler_calls == []
    captured = capsys.readouterr()
    assert "usage:" in captured.err
    assert message in captured.err


def test_parser_defaults_and_choices_pin_the_documented_flag_contract():
    subparsers = _subparsers()

    login = {action.dest: action for action in subparsers["login"]._actions}
    assert login["timeout_seconds"].default == 300.0
    assert login["profile"].default == "default"

    fetch = {action.dest: action for action in subparsers["fetch"]._actions}
    assert list(fetch["by"].choices) == ["username", "id"]
    assert fetch["replies"].default is False

    post = {action.dest: action for action in subparsers["post"]._actions}
    assert post["no_replies"].default is False

    search = {action.dest: action for action in subparsers["search"]._actions}
    assert list(search["type"].choices) == ["posts", "people"]
    assert search["type"].default == "posts"
    assert search["since"].help == (
        "Keep posts on/after YYYY-MM-DD; an unconfirmed boundary exits 7. "
        "For search, valid only with --type posts."
    )
    assert search["until"].help == (
        "Keep posts on/before YYYY-MM-DD. For search, valid only with --type posts."
    )

    for command in READ_COMMANDS:
        actions = {action.dest: action for action in subparsers[command]._actions}
        assert list(actions["format"].choices) == ["json", "ndjson"]
        assert actions["format"].default == "json"
        assert actions["limit"].default is None
        assert actions["limit"].help == (
            "Strictly cap User and non-pinned Post objects at N; pinned profile posts are "
            "preserved and may make total Post rows exceed N (default: unbounded)."
        )
        assert actions["max_wait"].default is None


def test_catalog_is_json_and_accepts_the_symmetric_json_flag(capsys):
    assert cli.main(["catalog"]) == 0
    without_flag = json.loads(capsys.readouterr().out)
    assert cli.main(["catalog", "--json"]) == 0
    with_flag = json.loads(capsys.readouterr().out)

    assert with_flag == without_flag
    assert with_flag["catalog_version"] == cli.CATALOG_VERSION
    assert with_flag["package"] == "agentic-threads"
    assert with_flag["command"] == "agentic-threads"
    assert with_flag["output_schema"] == "agentic-threads schema --json"


def test_catalog_commands_handlers_and_outputs_cannot_drift():
    catalog = cli.build_catalog()
    commands = {entry["name"]: entry for entry in catalog["commands"]}

    assert set(commands) == set(cli._HANDLERS) == ALL_COMMANDS
    expected_output = {
        "feed": "Post",
        "fetch": "Post",
        "post": "Post",
        "search": "Post | User",
        "followers": "User",
        "following": "User",
    }
    for command in META_COMMANDS:
        assert commands[command]["output"] is None
    for command, output in expected_output.items():
        assert commands[command]["output"] == output

    search_arguments = {argument["name"]: argument for argument in commands["search"]["arguments"]}
    assert search_arguments["query"]["positional"] is True
    assert search_arguments["type"]["choices"] == ["posts", "people"]
    assert search_arguments["type"]["default"] == "posts"
    assert search_arguments["raw"]["is_flag"] is True
    assert search_arguments["since"]["type"] == "date"
    assert search_arguments["since"]["help"].endswith("For search, valid only with --type posts.")
    assert search_arguments["until"]["help"].endswith("For search, valid only with --type posts.")


def test_catalog_exit_codes_include_every_documented_semantic():
    catalog = cli.build_catalog()
    assert set(catalog["exit_codes"]) == {"0", "1", "2", "3", "4", "5", "7"}
    assert "boundary" in catalog["exit_codes"]["7"]
    assert "doctor --refresh" in catalog["exit_codes"]["4"]


def test_plain_schema_is_offline_and_lists_post_user_and_media(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must stay offline")),
    )

    assert cli.main(["schema"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Post — " in captured.out
    assert "User — " in captured.out
    assert "Media — " in captured.out
    assert "id : string" in captured.out
    assert "raw : object (only present with --raw)" in captured.out


def test_json_schema_is_offline_and_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.auth,
        "load_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must stay offline")),
    )

    assert cli.main(["schema", "--json"]) == 0

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["title"] == "Post"
    assert set(document["$defs"]) == {"Post", "User", "Media"}
    assert "id" in document["required"]
    assert "raw" not in document["required"]


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (errors.InvalidIdentifierError("invalid"), 1),
        (errors.LoginRequiredError("missing"), 2),
        (errors.SessionExpiredError("expired"), 2),
        (errors.ChallengeError("checkpoint"), 2),
        (errors.RateLimitedError("limited"), 3),
        (errors.EnvelopeParseError("drift"), 4),
        (errors.ProfileUnavailableError("private"), 5),
        (errors.NotFoundError("missing"), 5),
    ],
)
def test_error_types_map_to_their_documented_exit_codes(failure, code, capsys):
    args = argparse.Namespace(profile="synthetic", verbose=False)
    assert cli._handle_common_errors(failure, args) == code
    assert capsys.readouterr().err


def test_unexpected_error_is_redacted_and_verbose_only_on_request(capsys):
    terse = argparse.Namespace(profile="synthetic", verbose=False)
    assert cli._report_exception("read failed", RuntimeError("secret detail"), terse) == 1
    terse_error = capsys.readouterr().err
    assert "RuntimeError" in terse_error
    assert "secret detail" not in terse_error

    verbose = argparse.Namespace(profile="synthetic", verbose=True)
    assert cli._report_exception("read failed", RuntimeError("safe detail"), verbose) == 1
    assert "safe detail" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["feed"],
        ["fetch", "synthetic_user"],
        ["post", "12345"],
        ["search", "synthetic query"],
        ["followers", "12345"],
        ["following", "12345"],
    ],
)
def test_every_read_handler_maps_a_missing_session_to_exit_two(monkeypatch, argv):
    monkeypatch.setattr(
        cli.auth,
        "load_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(errors.LoginRequiredError("missing")),
    )
    monkeypatch.setattr(
        cli.client,
        "ReadClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no client expected")),
    )
    assert cli.main(argv) == 2


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (session.Status.LOGGED_IN, 0),
        (session.Status.EXPIRED, 2),
        (session.Status.RATE_LIMITED, 3),
    ],
)
def test_status_json_output_and_exit_code_follow_classification(
    monkeypatch,
    status,
    expected_code,
    capsys,
):
    monkeypatch.setattr(cli.session, "run_status", lambda *args, **kwargs: status)

    assert cli.main(["status", "--json"]) == expected_code

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": status.value}
    assert captured.err == ""


def test_status_human_summary_goes_to_stderr_only(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.session,
        "run_status",
        lambda *args, **kwargs: session.Status.LOGGED_IN,
    )
    assert cli.main(["status"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "status: logged_in"


def test_cookie_login_passes_profile_and_path_and_prints_only_stderr(
    monkeypatch,
    capsys,
):
    calls: list[tuple[Path, str, object]] = []

    def import_cookie(path, profile, *, profile_dir_override=None):
        calls.append((path, profile, profile_dir_override))

    monkeypatch.setattr(cli.auth, "from_cookie_file", import_cookie)

    assert (
        cli.main(
            [
                "login",
                "--cookies",
                "synthetic-cookies.json",
                "--profile",
                "secondary",
                "--profile-dir",
                "profiles",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert calls == [(Path("synthetic-cookies.json"), "secondary", "profiles")]
    assert captured.out == ""
    assert "Cookie import succeeded" in captured.err


def test_browser_login_forwards_timeout_and_setup_and_doctor_flags(monkeypatch, capsys):
    login_calls: list[tuple[str, object, float]] = []
    setup_calls: list[bool] = []
    doctor_calls: list[tuple[str, object, bool]] = []

    def run_login(profile, *, profile_dir_override=None, timeout_seconds=300.0):
        login_calls.append((profile, profile_dir_override, timeout_seconds))
        return True

    def run_doctor(profile, *, profile_dir_override=None, refresh=False):
        doctor_calls.append((profile, profile_dir_override, refresh))
        return True, "OK - synthetic round-trip"

    monkeypatch.setattr(cli.session, "run_login", run_login)
    monkeypatch.setattr(cli.session, "run_setup", lambda *, force=False: setup_calls.append(force))
    monkeypatch.setattr(cli.session, "run_doctor", run_doctor)

    assert cli.main(["login", "--timeout-seconds", "12.5"]) == 0
    assert cli.main(["setup", "--force"]) == 0
    assert cli.main(["doctor", "--refresh"]) == 0

    assert login_calls == [("default", None, 12.5)]
    assert setup_calls == [True]
    assert doctor_calls == [("default", None, True)]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Logged in" in captured.err
    assert "Browser provisioned" in captured.err
    assert "synthetic round-trip" in captured.err


def test_login_handler_preserves_challenge_exit_and_only_calls_browser_login(
    monkeypatch,
    capsys,
):
    login_calls: list[tuple[str, object, float]] = []
    cookie_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def run_login(profile, *, profile_dir_override=None, timeout_seconds=300.0):
        login_calls.append((profile, profile_dir_override, timeout_seconds))
        raise errors.ChallengeError("browser checkpoint remains; sessionid=synthetic-login-secret")

    def import_cookie(*args, **kwargs):
        cookie_calls.append((args, kwargs))

    monkeypatch.setattr(cli.session, "run_login", run_login)
    monkeypatch.setattr(cli.auth, "from_cookie_file", import_cookie)

    assert (
        cli.main(
            [
                "login",
                "--profile",
                "secondary",
                "--profile-dir",
                "profiles",
                "--timeout-seconds",
                "12.5",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert login_calls == [("secondary", "profiles", 12.5)]
    assert cookie_calls == []
    assert captured.out == ""
    assert "checkpoint:" in captured.err
    assert "[REDACTED]" in captured.err
    assert "synthetic-login-secret" not in captured.err


def test_status_handler_preserves_rate_limit_exit_without_json_stdout(monkeypatch, capsys):
    status_calls: list[tuple[str, object]] = []

    def run_status(profile, *, profile_dir_override=None):
        status_calls.append((profile, profile_dir_override))
        raise errors.RateLimitedError(
            "status unavailable; Authorization: Bearer synthetic-status-secret"
        )

    monkeypatch.setattr(cli.session, "run_status", run_status)

    assert (
        cli.main(
            [
                "status",
                "--profile",
                "secondary",
                "--profile-dir",
                "profiles",
                "--json",
            ]
        )
        == 3
    )

    captured = capsys.readouterr()
    assert status_calls == [("secondary", "profiles")]
    assert captured.out == ""
    assert "rate-limited:" in captured.err
    assert "[REDACTED]" in captured.err
    assert "synthetic-status-secret" not in captured.err


def test_doctor_handler_preserves_persisted_operation_drift_exit(monkeypatch, capsys):
    doctor_calls: list[tuple[str, object, bool]] = []

    def run_doctor(profile, *, profile_dir_override=None, refresh=False):
        doctor_calls.append((profile, profile_dir_override, refresh))
        raise errors.PersistedOperationDriftError(
            "HTTP 400 persisted operation drift; csrftoken=synthetic-doctor-secret"
        )

    monkeypatch.setattr(cli.session, "run_doctor", run_doctor)

    assert (
        cli.main(
            [
                "doctor",
                "--profile",
                "secondary",
                "--profile-dir",
                "profiles",
                "--refresh",
            ]
        )
        == 4
    )

    captured = capsys.readouterr()
    assert doctor_calls == [("secondary", "profiles", True)]
    assert captured.out == ""
    assert "response envelope drift:" in captured.err
    assert "[REDACTED]" in captured.err
    assert "synthetic-doctor-secret" not in captured.err


class ClosableClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_fetch_binds_normalization_and_every_retrieval_option(
    monkeypatch,
    tmp_path,
    capsys,
):
    output = tmp_path / "fetch.ndjson"
    fake_client = ClosableClient()
    doc_ids = {"profile": "doc-profile", "profile_replies": "doc-replies"}
    features = {"synthetic": True}
    normalization_calls: list[tuple[str, str | None]] = []
    observed: dict[str, object] = {}
    result = retrieve.RetrieveResult(
        posts=[_post()],
        stop_reason="no_next_page",
        requests_made=3,
        since_target_crossed=True,
    )

    def normalize_user_identifier(identifier, *, by=None):
        normalization_calls.append((identifier, by))
        return "username", "123456"

    def fetch_profile(read_client, bound_doc_ids, bound_features, kind, value, **kwargs):
        observed["positionals"] = (
            read_client,
            bound_doc_ids,
            bound_features,
            kind,
            value,
        )
        observed["kwargs"] = kwargs
        return result

    monkeypatch.setattr(cli.auth, "normalize_user_identifier", normalize_user_identifier)
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, doc_ids, features),
    )
    monkeypatch.setattr(cli.retrieve, "fetch_profile", fetch_profile)

    code = cli.main(
        [
            "fetch",
            "123456",
            "--by",
            "username",
            "--replies",
            "--since",
            "2026-06-01",
            "--until",
            "2026-07-01",
            "--limit",
            "2",
            "--wait-on-limit",
            "--max-wait",
            "4.5",
            "--raw",
            "--format",
            "ndjson",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert normalization_calls == [("123456", "username")]
    assert observed["positionals"] == (
        fake_client,
        doc_ids,
        features,
        "username",
        "123456",
    )
    assert observed["kwargs"] == {
        "replies": True,
        "limit": 2,
        "since": datetime(2026, 6, 1, tzinfo=UTC),
        "until": datetime(2026, 7, 1, 23, 59, 59, 999999, tzinfo=UTC),
        "max_requests": cli.config.DEFAULT_MAX_REQUESTS,
        "wait_on_limit": True,
        "max_wait": 4.5,
        "raw": True,
    }
    assert fake_client.closed is True
    assert json.loads(output.read_text(encoding="utf-8"))["id"] == "100"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"1 posts, range 2026-07-01..2026-07-01, stop reason: no_next_page. Saved to {output}\n"
    )


@pytest.mark.parametrize(
    ("argv", "expected_filename"),
    [
        (["feed"], "home-20260723T010203456789Z.json"),
        (
            ["fetch", "../../Synthetic Profile", "--format", "ndjson"],
            "Synthetic-Profile-20260723T010203456789Z.ndjson",
        ),
    ],
)
def test_successful_reads_without_output_use_exact_default_path(
    monkeypatch,
    tmp_path,
    capsys,
    argv,
    expected_filename,
):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return cls(2026, 7, 23, 1, 2, 3, 456789, tzinfo=tz)

    platform_data_root = tmp_path / "platform-data" / "agentic-threads"
    platform_calls: list[str] = []
    fake_client = ClosableClient()
    result = retrieve.RetrieveResult([_post()], "no_next_page", 1)

    def user_data_dir(app_name):
        platform_calls.append(app_name)
        return str(platform_data_root)

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    monkeypatch.setattr(cli.config.platformdirs, "user_data_dir", user_data_dir)
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {"synthetic": "doc-id"}, {}),
    )
    monkeypatch.setattr(
        cli.auth,
        "normalize_user_identifier",
        lambda identifier, *, by=None: ("username", "synthetic"),
    )
    monkeypatch.setattr(cli.retrieve, "fetch_home", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli.retrieve, "fetch_profile", lambda *args, **kwargs: result)

    assert "--output" not in argv
    assert cli.main(argv) == 0

    expected_output = platform_data_root / "output" / expected_filename
    assert platform_calls == ["agentic-threads"]
    assert fake_client.closed is True
    assert expected_output.is_file()
    assert [path.name for path in expected_output.parent.iterdir()] == [expected_filename]
    serialized = expected_output.read_text(encoding="utf-8")
    document = (
        json.loads(serialized)[0]
        if expected_output.suffix == ".json"
        else json.loads(serialized.splitlines()[0])
    )
    assert document["id"] == "100"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "1 posts, range 2026-07-01..2026-07-01, stop reason: no_next_page. "
        f"Saved to {expected_output}\n"
    )


@pytest.mark.parametrize("result_kind", ["post", "user"])
def test_rate_limited_post_and_user_results_are_saved_with_exit_three(
    monkeypatch,
    tmp_path,
    capsys,
    result_kind,
):
    output = tmp_path / f"rate-limited-{result_kind}.json"
    fake_client = ClosableClient()
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {"synthetic": "doc-id"}, {}),
    )

    if result_kind == "post":
        result = retrieve.RetrieveResult([_post()], "rate_limited", 1)
        monkeypatch.setattr(cli.retrieve, "fetch_home", lambda *args, **kwargs: result)
        argv = ["feed"]
        expected_id = "100"
        expected_summary = (
            f"1 posts, range 2026-07-01..2026-07-01, stop reason: rate_limited. Saved to {output}\n"
        )
    else:
        result = retrieve.UserResult([_user("201", "synthetic_user")], "rate_limited", 1)
        monkeypatch.setattr(
            cli.auth,
            "normalize_user_identifier",
            lambda identifier, *, by=None: ("username", "synthetic_user"),
        )
        monkeypatch.setattr(
            cli.retrieve,
            "fetch_social_graph",
            lambda *args, **kwargs: result,
        )
        argv = ["followers", "synthetic_user"]
        expected_id = "201"
        expected_summary = f"1 accounts, stop reason: rate_limited. Saved to {output}\n"

    assert cli.main([*argv, "--output", str(output)]) == 3

    assert fake_client.closed is True
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == expected_id
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected_summary
    assert captured.err.count("\n") == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX control-character path contract")
@pytest.mark.parametrize("result_kind", ["post", "user"])
def test_summary_escapes_path_controls_without_changing_the_real_path(
    tmp_path,
    capsys,
    result_kind,
):
    output = tmp_path / f"{result_kind}-line\n-tab\t-escape\x1b-unit\x1f-del\x7f.json"
    args = argparse.Namespace(
        output=str(output),
        format="json",
        raw=False,
        no_redact=False,
        since=None,
    )
    if result_kind == "post":
        result = retrieve.RetrieveResult([_post()], "no_next_page", 1)
        expected_id = "100"
        summary_prefix = (
            "1 posts, range 2026-07-01..2026-07-01, stop reason: no_next_page. Saved to "
        )
    else:
        result = retrieve.UserResult([_user("201", "synthetic_user")], "no_next_page", 1)
        expected_id = "201"
        summary_prefix = "1 accounts, stop reason: no_next_page. Saved to "

    assert cli._finish_or_error(result, "unused", args) == 0

    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == expected_id
    rendered_path = (
        str(output)
        .replace("\n", r"\x0a")
        .replace("\t", r"\x09")
        .replace("\x1b", r"\x1b")
        .replace("\x1f", r"\x1f")
        .replace("\x7f", r"\x7f")
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{summary_prefix}{rendered_path}\n"
    assert captured.err.count("\n") == 1
    assert all(ord(character) >= 0x20 and ord(character) != 0x7F for character in captured.err[:-1])


@pytest.mark.skipif(os.name != "posix", reason="POSIX output-permission contract")
@pytest.mark.parametrize("fmt", ["json", "ndjson"])
def test_write_rows_keeps_format_bytes_and_uses_owner_only_modes(tmp_path, fmt):
    output_directory = tmp_path / "new-output"
    output = output_directory / f"users.{fmt}"
    users = [_user("201", "synthetic_a"), _user("202", "synthetic_b")]
    dictionaries = [user.to_dict() for user in users]
    expected = (
        "".join(json.dumps(dictionary, ensure_ascii=False) + "\n" for dictionary in dictionaries)
        if fmt == "ndjson"
        else json.dumps(dictionaries, ensure_ascii=False, indent=2) + "\n"
    )

    cli._write_users(users, output, fmt)

    assert output.read_text(encoding="utf-8") == expected
    assert stat.S_IMODE(output_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(output_directory.glob(f"{cli._OUTPUT_TEMP_PREFIX}*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_write_rows_replaces_a_destination_symlink_without_following_it(tmp_path):
    target = tmp_path / "outside.json"
    original_target = b'{"outside":"unchanged"}\n'
    target.write_bytes(original_target)
    output_directory = tmp_path / "selected-output"
    output_directory.mkdir()
    output_directory.chmod(0o750)
    output = output_directory / "posts.json"
    output.symlink_to(target)

    cli._write_output([_post()], output, "json")

    assert target.read_bytes() == original_target
    assert not output.is_symlink()
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == "100"
    assert stat.S_IMODE(output_directory.stat().st_mode) == 0o750
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic-write contract")
def test_write_rows_preserves_destination_and_cleans_temp_on_json_failure(tmp_path):
    class UnserializableRow:
        def to_dict(self):
            return {"written_before_failure": True, "invalid": object()}

    output = tmp_path / "existing.json"
    original = b'{"existing":"unchanged"}\n'
    output.write_bytes(original)

    with pytest.raises(TypeError, match="not JSON serializable"):
        cli._write_rows([UnserializableRow()], output, "json")

    assert output.read_bytes() == original
    assert not list(tmp_path.glob(f"{cli._OUTPUT_TEMP_PREFIX}*"))


def test_json_file_summary_is_stderr_only_and_inconclusive_since_exits_seven(
    monkeypatch,
    tmp_path,
    capsys,
):
    output = tmp_path / "posts.json"
    fake_client = ClosableClient()
    observed: dict[str, object] = {}
    result = retrieve.RetrieveResult(
        posts=[_post()],
        stop_reason="limit_reached",
        requests_made=1,
        since_target_crossed=False,
        raw_post_count=2,
    )
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {"feed": "doc-feed"}, {"synthetic": True}),
    )

    def fetch_home(read_client, doc_ids, features, **kwargs):
        observed.update(
            read_client=read_client,
            doc_ids=doc_ids,
            features=features,
            kwargs=kwargs,
        )
        return result

    monkeypatch.setattr(cli.retrieve, "fetch_home", fetch_home)

    code = cli.main(
        [
            "feed",
            "--limit",
            "1",
            "--since",
            "2026-06-01",
            "--until",
            "2026-07-01",
            "--output",
            str(output),
        ]
    )

    assert code == 7
    assert fake_client.closed is True
    assert observed["read_client"] is fake_client
    kwargs = observed["kwargs"]
    assert kwargs["limit"] == 1
    assert kwargs["since"] == datetime(2026, 6, 1, tzinfo=UTC)
    assert kwargs["until"] == datetime(2026, 7, 1, 23, 59, 59, 999999, tzinfo=UTC)
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == "100"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert "1 posts, range 2026-07-01..2026-07-01" in captured.err
    assert "stop reason: limit_reached" in captured.err
    assert f"Saved to {output}" in captured.err


def test_people_search_writes_ndjson_and_an_accounts_summary(
    monkeypatch,
    tmp_path,
    capsys,
):
    output = tmp_path / "people.ndjson"
    fake_client = ClosableClient()
    users = [_user("201", "synthetic_a"), _user("202", "synthetic_b")]
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {"people_search": "doc-search"}, {}),
    )

    def search(read_client, doc_ids, features, query, **kwargs):
        observed.update(query=query, kwargs=kwargs)
        return retrieve.UserResult(users, "no_next_page", 1)

    monkeypatch.setattr(cli.retrieve, "search", search)

    code = cli.main(
        [
            "search",
            "synthetic people",
            "--type",
            "people",
            "--format",
            "ndjson",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert fake_client.closed is True
    assert observed["query"] == "synthetic people"
    assert observed["kwargs"]["search_type"] == "people"
    assert observed["kwargs"]["raw"] is False
    lines = output.read_text(encoding="utf-8").splitlines()
    documents = [json.loads(line) for line in lines]
    assert [document["id"] for document in documents] == ["201", "202"]
    assert all("raw" not in document for document in documents)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (f"2 accounts, stop reason: no_next_page. Saved to {output}")


@pytest.mark.parametrize(
    ("redaction_args", "expected_credential", "warning_count"),
    [
        ([], "[REDACTED]", 0),
        (["--no-redact"], "synthetic-user-credential", 1),
    ],
)
def test_people_raw_is_forwarded_and_scrubbed_unless_explicitly_disabled(
    monkeypatch,
    tmp_path,
    capsys,
    redaction_args,
    expected_credential,
    warning_count,
):
    output = tmp_path / f"people-raw-{warning_count}.json"
    credential = "synthetic-user-credential"
    raw_user = {
        "pk": "203",
        "username": "synthetic_raw",
        "diagnostics": [{"access_token": credential}],
    }
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (ClosableClient(), {"people_search": "doc-search"}, {}),
    )

    def search(read_client, doc_ids, features, query, **kwargs):
        observed["raw"] = kwargs["raw"]
        user = model.build_user(raw_user, include_raw=kwargs["raw"])
        assert user is not None
        return retrieve.UserResult([user], "no_next_page", 1)

    monkeypatch.setattr(cli.retrieve, "search", search)

    assert (
        cli.main(
            [
                "search",
                "synthetic raw",
                "--type",
                "people",
                "--raw",
                *redaction_args,
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert observed["raw"] is True
    document = json.loads(output.read_text(encoding="utf-8"))[0]
    assert document["raw"]["diagnostics"][0]["access_token"] == expected_credential
    assert raw_user["diagnostics"][0]["access_token"] == credential
    warning = "WARNING: --no-redact disables raw diagnostic scrubbing."
    assert capsys.readouterr().err.count(warning) == warning_count


@pytest.mark.parametrize("fmt", ["json", "ndjson"])
@pytest.mark.parametrize(
    ("redaction_args", "secrets_are_redacted", "warning_count"),
    [
        ([], True, 0),
        (["--no-redact"], False, 1),
    ],
)
def test_post_raw_recursively_scrubs_nested_posts_in_json_and_ndjson(
    monkeypatch,
    tmp_path,
    capsys,
    fmt,
    redaction_args,
    secrets_are_redacted,
    warning_count,
):
    output = tmp_path / f"post-raw-{warning_count}.{fmt}"
    secrets = [
        "synthetic-root-session",
        "synthetic-quoted-token",
        "synthetic-reposted-secret",
    ]
    reposted_raw = {
        "pk": "302",
        "code": "S302",
        "client_secret": secrets[2],
        "text_post_app_info": {},
    }
    quoted_raw = {
        "pk": "301",
        "code": "S301",
        "access_token": secrets[1],
        "text_post_app_info": {
            "share_info": {
                "reposted_post": reposted_raw,
            }
        },
    }
    raw_post = {
        "pk": "300",
        "code": "S300",
        "sessionid": secrets[0],
        "text_post_app_info": {
            "share_info": {
                "quoted_post": quoted_raw,
            }
        },
    }
    original_raw_post = json.loads(json.dumps(raw_post))
    fake_client = ClosableClient()
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {"post": "doc-post"}, {}),
    )

    def fetch_post(read_client, doc_ids, features, kind, value, **kwargs):
        observed["raw"] = kwargs["raw"]
        post = model.build_post(raw_post, include_raw=kwargs["raw"])
        assert post is not None
        return retrieve.RetrieveResult([post], "no_next_page", 1)

    monkeypatch.setattr(cli.retrieve, "fetch_post", fetch_post)

    assert (
        cli.main(
            [
                "post",
                "12345",
                "--raw",
                *redaction_args,
                "--format",
                fmt,
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert observed["raw"] is True
    assert fake_client.closed is True
    assert raw_post == original_raw_post
    serialized = output.read_text(encoding="utf-8")
    documents = (
        json.loads(serialized)
        if fmt == "json"
        else [json.loads(line) for line in serialized.splitlines()]
    )
    assert len(documents) == 1
    document = documents[0]
    saved_secrets = [
        document["raw"]["sessionid"],
        document["quoted_post"]["raw"]["access_token"],
        document["quoted_post"]["reposted_post"]["raw"]["client_secret"],
    ]
    assert saved_secrets == (["[REDACTED]"] * 3 if secrets_are_redacted else secrets)
    if secrets_are_redacted:
        assert all(secret not in serialized for secret in secrets)
    else:
        assert all(secret in serialized for secret in secrets)

    warning = "WARNING: --no-redact leaves --raw GraphQL nodes unscrubbed in the saved file."
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count(warning) == warning_count
    assert captured.err.count("WARNING:") == warning_count


def _post_replies_calls(monkeypatch, argv: list[str]) -> list[dict[str, object]]:
    fake_client = ClosableClient()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {}, {}),
    )
    monkeypatch.setattr(
        cli,
        "_finish_or_error",
        lambda result, identifier, args: 0,
    )

    def fetch_post(*args, **kwargs):
        calls.append(kwargs)
        return retrieve.RetrieveResult([_post()], "no_next_page", 1)

    monkeypatch.setattr(cli.retrieve, "fetch_post", fetch_post)

    assert cli.main(argv) == 0
    assert fake_client.closed is True
    return calls


def test_post_defaults_to_forwarding_replies_true(monkeypatch):
    calls = _post_replies_calls(monkeypatch, ["post", "12345"])

    assert [call["replies"] for call in calls] == [True]


def test_post_no_replies_forwards_replies_false(monkeypatch):
    calls = _post_replies_calls(monkeypatch, ["post", "12345", "--no-replies"])

    assert [call["replies"] for call in calls] == [False]


def test_graph_operations_are_forwarded(monkeypatch):
    fake_client = ClosableClient()
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        cli,
        "_load_read_client",
        lambda args: (fake_client, {}, {}),
    )
    monkeypatch.setattr(
        cli,
        "_finish_or_error",
        lambda result, identifier, args: 0,
    )

    def fetch_graph(*args, **kwargs):
        calls.append((args[3], kwargs))
        return retrieve.UserResult([_user("1", "synthetic")], "no_next_page", 1)

    monkeypatch.setattr(cli.retrieve, "fetch_social_graph", fetch_graph)

    assert cli.main(["followers", "12345", "--limit", "2", "--raw"]) == 0
    assert cli.main(["following", "12345", "--limit", "3"]) == 0

    assert calls[0][0] == "followers"
    assert calls[0][1]["limit"] == 2
    assert calls[0][1]["raw"] is True
    assert calls[1][0] == "following"
    assert calls[1][1]["limit"] == 3
    assert calls[1][1]["raw"] is False
