"""Command-line interface for read-only Agentic Threads primitives."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from . import __version__, auth, client, config, errors, model, redact, retrieve, session

_PROFILE_DIR_HELP = (
    "Override the profile-store root (default: platform data dir, or $AGENTIC_THREADS_PROFILE_DIR)."
)
_COMPLETE_POST_REASONS = frozenset(
    {"feed_exhausted", "no_next_page", "no_matches", "since_crossed"}
)
_OUTPUT_DIRECTORY_MODE = 0o700
_OUTPUT_FILE_MODE = 0o600
_OUTPUT_TEMP_PREFIX = ".at-"


class _ArgumentParser(argparse.ArgumentParser):
    """Reserve exit 2 for authentication failures rather than usage errors."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _parse_iso_date(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _since_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _until_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, 23, 59, 59, 999999, tzinfo=UTC)


def _add_profile_args(parser: argparse.ArgumentParser, *, action: str = "use") -> None:
    parser.add_argument(
        "--profile",
        default=config.DEFAULT_PROFILE_NAME,
        help=f"Named login session to {action} (default: 'default').",
    )
    parser.add_argument("--profile-dir", default=None, help=_PROFILE_DIR_HELP)


def _add_common_read_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=["json", "ndjson"],
        default="json",
        help="Write one JSON array or one object per NDJSON line (default: json).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path (default: a timestamped file under the platform data dir).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Strictly cap User and non-pinned Post objects at N; pinned profile posts are "
            "preserved and may make total Post rows exceed N (default: unbounded)."
        ),
    )
    parser.add_argument(
        "--wait-on-limit",
        action="store_true",
        help="Wait for a usable rate-limit reset instead of stopping immediately.",
    )
    parser.add_argument(
        "--max-wait",
        type=float,
        default=None,
        help="Maximum rate-limit wait in seconds (requires --wait-on-limit).",
    )
    _add_profile_args(parser)
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Attach source GraphQL nodes for diagnostics (redacted by default).",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Leave --raw nodes unredacted and print a warning.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Include redacted error details.",
    )


def _add_date_args(
    parser: argparse.ArgumentParser,
    *,
    search_posts_only: bool = False,
) -> None:
    search_scope = " For search, valid only with --type posts." if search_posts_only else ""
    parser.add_argument(
        "--since",
        type=_parse_iso_date,
        default=None,
        help=(f"Keep posts on/after YYYY-MM-DD; an unconfirmed boundary exits 7.{search_scope}"),
    )
    parser.add_argument(
        "--until",
        type=_parse_iso_date,
        default=None,
        help=f"Keep posts on/before YYYY-MM-DD.{search_scope}",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="agentic-threads",
        description=(
            "Read-only Threads retrieval over authenticated HTTP. Commands are "
            "single-target primitives and write JSON files."
        ),
        epilog="Use a throwaway Instagram account and read DISCLAIMER.md before use.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agentic-threads {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        help="Open a headed login browser or import a cookie export.",
    )
    _add_profile_args(login_parser, action="save")
    login_parser.add_argument(
        "--cookies",
        default=None,
        help="Import a Netscape/JSON/cURL cookie export instead of opening a browser.",
    )
    login_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum browser-login wait (default: 300).",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Classify a saved session with one authenticated HTTP read.",
    )
    _add_profile_args(status_parser, action="check")
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable status object to stdout.",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="Provision the optional login browser into an isolated cache.",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall even if the browser is already provisioned.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check authentication and optionally refresh rotated doc IDs.",
    )
    _add_profile_args(doctor_parser, action="check")
    doctor_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-anchor doc IDs from Threads JavaScript over HTTP (no browser).",
    )

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Emit the parser-derived CLI catalog as JSON (offline).",
    )
    catalog_parser.add_argument(
        "--json",
        action="store_true",
        help="Accepted for symmetry; catalog output is always JSON.",
    )

    schema_parser = subparsers.add_parser(
        "schema",
        help="Print the Post/User/Media output schema (offline).",
    )
    schema_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON Schema draft 2020-12 instead of a field listing.",
    )

    feed_parser = subparsers.add_parser(
        "feed",
        help="Read the logged-in account's home/For You feed.",
    )
    _add_common_read_args(feed_parser)
    _add_date_args(feed_parser)

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Read a profile's Threads-tab posts and optional replies tab.",
    )
    fetch_parser.add_argument(
        "identifier",
        help="@handle, username, numeric user id, or Threads profile URL.",
    )
    fetch_parser.add_argument(
        "--replies",
        action="store_true",
        help="Also include the profile's replies tab.",
    )
    fetch_parser.add_argument(
        "--by",
        choices=["username", "id"],
        default=None,
        help="Disambiguate an all-digit identifier as username or numeric id.",
    )
    _add_common_read_args(fetch_parser)
    _add_date_args(fetch_parser)

    post_parser = subparsers.add_parser(
        "post",
        help="Read one post plus its reply thread.",
    )
    post_parser.add_argument(
        "identifier",
        help="Threads post URL, shortcode, or numeric post id.",
    )
    post_parser.add_argument(
        "--no-replies",
        action="store_true",
        help="Return only the requested root post.",
    )
    _add_common_read_args(post_parser)

    search_parser = subparsers.add_parser(
        "search",
        help="Search Threads posts or people.",
    )
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--type",
        choices=["posts", "people"],
        default="posts",
        help="Result type (default: posts).",
    )
    _add_common_read_args(search_parser)
    _add_date_args(search_parser, search_posts_only=True)

    for command, help_text in (
        ("followers", "Read accounts following one user."),
        ("following", "Read accounts followed by one user."),
    ):
        graph_parser = subparsers.add_parser(command, help=help_text)
        graph_parser.add_argument(
            "identifier",
            help="@handle, username, numeric user id, or Threads profile URL.",
        )
        _add_common_read_args(graph_parser)

    return parser


def _cmd_login(args: argparse.Namespace) -> int:
    try:
        if args.cookies is not None:
            auth.from_cookie_file(
                Path(args.cookies),
                args.profile,
                profile_dir_override=args.profile_dir,
            )
            print(f"Cookie import succeeded. Profile saved: {args.profile!r}", file=sys.stderr)
            return 0
        logged_in = session.run_login(
            args.profile,
            profile_dir_override=args.profile_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        return _report_exception("login failed", exc, args)

    if logged_in:
        print(f"Logged in. Profile saved: {args.profile!r}", file=sys.stderr)
        return 0
    print(
        "Could not verify a complete Threads login. Resolve the browser login and try again.",
        file=sys.stderr,
    )
    return 2


_STATUS_EXIT_CODES = {
    session.Status.LOGGED_IN: 0,
    session.Status.EXPIRED: 2,
    session.Status.RATE_LIMITED: 3,
}


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = session.run_status(
            args.profile,
            profile_dir_override=args.profile_dir,
        )
    except errors.LoginRequiredError as exc:
        safe_error = redact.redact_raw_text(str(exc))
        if args.json:
            print(json.dumps({"status": "not_logged_in", "error": safe_error}))
        else:
            print(
                f"{safe_error} Run: agentic-threads login --profile {args.profile}",
                file=sys.stderr,
            )
        return exc.exit_code
    except Exception as exc:
        return _report_exception("status check failed", exc, args)

    if args.json:
        print(json.dumps({"status": status.value}))
    else:
        print(f"status: {status.value}", file=sys.stderr)
    return _STATUS_EXIT_CODES[status]


def _cmd_setup(args: argparse.Namespace) -> int:
    try:
        session.run_setup(force=args.force)
    except Exception as exc:
        _report_exception("setup failed", exc, args)
        return 1
    print("Browser provisioned.", file=sys.stderr)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        ok, message = session.run_doctor(
            args.profile,
            profile_dir_override=args.profile_dir,
            refresh=args.refresh,
        )
    except Exception as exc:
        return _report_exception("doctor check failed", exc, args)
    print(redact.redact_raw_text(message), file=sys.stderr)
    return 0 if ok else 1


def _print_schema_object(title: str, where: str, fields: list[dict[str, Any]]) -> None:
    print(f"{title} — {where}:\n")
    for field in fields:
        note = "" if field["always_present"] else " (only present with --raw)"
        print(f"  {field['name']} : {field['type']}{note}")
        print(f"      {field['description']}")
    print()


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(model.json_schema(), indent=2, ensure_ascii=False))
        return 0
    print(
        "agentic-threads output schema — read commands write Post or User objects "
        "as JSON arrays (or one object per NDJSON line).\n"
    )
    _print_schema_object("Post", "one post result", model.post_schema_fields())
    _print_schema_object(
        "User",
        "a direct account result or Post.author",
        model.user_schema_fields(),
    )
    _print_schema_object("Media", "an element of Post.media", model.media_schema_fields())
    return 0


CATALOG_VERSION = 1
_COMMAND_OUTPUT: dict[str, str] = {
    "feed": "Post",
    "fetch": "Post",
    "post": "Post",
    "search": "Post | User",
    "followers": "User",
    "following": "User",
}
_EXIT_CODES = {
    0: "success",
    1: "usage error, invalid identifier, setup failure, or unexpected failure",
    2: "not logged in, session expired, soft-locked, or checkpointed",
    3: "rate-limited (see --wait-on-limit)",
    4: "Threads response drift or rotated doc ID (run doctor --refresh)",
    5: "target user or post does not exist or is unavailable",
    7: "--since was requested but the lower boundary was not confirmed",
}


def _catalog_type(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        return "boolean"
    if action.type is _parse_iso_date:
        return "date"
    if action.type is not None:
        return getattr(action.type, "__name__", str(action.type))
    return "string"


def _argument_catalog(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    arguments: list[dict[str, Any]] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        arguments.append(
            {
                "name": action.dest,
                "flags": list(action.option_strings),
                "type": _catalog_type(action),
                "positional": not action.option_strings,
                "required": action.required,
                "default": action.default,
                "choices": list(action.choices) if action.choices is not None else None,
                "is_flag": isinstance(
                    action,
                    (argparse._StoreTrueAction, argparse._StoreFalseAction),
                ),
                "help": action.help,
            }
        )
    return arguments


def build_catalog() -> dict[str, Any]:
    parser = build_parser()
    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    summaries = {choice.dest: choice.help or "" for choice in subparsers_action._choices_actions}
    commands = [
        {
            "name": name,
            "help": subparser.description or summaries.get(name, ""),
            "output": _COMMAND_OUTPUT.get(name),
            "arguments": _argument_catalog(subparser),
        }
        for name, subparser in subparsers_action.choices.items()
    ]
    return {
        "catalog_version": CATALOG_VERSION,
        "package": "agentic-threads",
        "command": "agentic-threads",
        "version": __version__,
        "commands": commands,
        "exit_codes": {str(code): text for code, text in _EXIT_CODES.items()},
        "output_schema": "agentic-threads schema --json",
    }


def _cmd_catalog(args: argparse.Namespace) -> int:
    print(json.dumps(build_catalog(), indent=2, ensure_ascii=False))
    return 0


def _redact_raw_recursive(
    post: model.Post,
    seen: set[int] | None = None,
) -> None:
    """Redact every raw node reachable through nested Post relationships."""

    if seen is None:
        seen = set()
    identity = id(post)
    if identity in seen:
        return
    seen.add(identity)
    if post.raw is not None:
        post.raw = redact.redact(post.raw)
    for nested in (post.quoted_post, post.reposted_post):
        if nested is not None:
            _redact_raw_recursive(nested, seen)


def _safe_identifier(identifier: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", identifier).strip("-")
    return safe[:96].rstrip("-") or "threads"


def _default_output_path(identifier: str, fmt: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    extension = "ndjson" if fmt == "ndjson" else "json"
    return config.default_output_dir() / f"{_safe_identifier(identifier)}-{timestamp}.{extension}"


def _write_rows(rows: list[object], path: Path, fmt: str) -> None:
    dictionaries = [row.to_dict() for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True, mode=_OUTPUT_DIRECTORY_MODE)
    fd, temporary_path = tempfile.mkstemp(prefix=_OUTPUT_TEMP_PREFIX, dir=path.parent)
    try:
        if os.name == "posix":
            os.fchmod(fd, _OUTPUT_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            if fmt == "ndjson":
                for dictionary in dictionaries:
                    stream.write(json.dumps(dictionary, ensure_ascii=False))
                    stream.write("\n")
            else:
                json.dump(dictionaries, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        try:
            if fd >= 0:
                os.close(fd)
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


def _write_output(posts: list[model.Post], path: Path, fmt: str) -> None:
    _write_rows(list(posts), path, fmt)


def _write_users(users: list[model.User], path: Path, fmt: str) -> None:
    _write_rows(list(users), path, fmt)


def _output_path(identifier: str, args: argparse.Namespace) -> Path:
    if args.output:
        return Path(args.output).expanduser()
    return _default_output_path(identifier, args.format)


def _render_output_path(path: Path) -> str:
    """Escape C0/DEL for the summary without changing the filesystem path."""

    rendered: list[str] = []
    for character in str(path):
        codepoint = ord(character)
        if codepoint <= 0x1F or codepoint == 0x7F:
            rendered.append(f"\\x{codepoint:02x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _prepare_raw(posts: list[model.Post], args: argparse.Namespace) -> None:
    if not args.raw:
        return
    if args.no_redact:
        print(
            "WARNING: --no-redact leaves --raw GraphQL nodes unscrubbed in the saved file.",
            file=sys.stderr,
        )
        return
    for post in posts:
        _redact_raw_recursive(post)


def _finish(
    result: retrieve.RetrieveResult,
    identifier: str,
    args: argparse.Namespace,
) -> int:
    _prepare_raw(result.posts, args)
    output_path = _output_path(identifier, args)
    _write_output(result.posts, output_path, args.format)

    if result.stop_reason == "rate_limited":
        exit_code = 3
    else:
        since_requested = getattr(args, "since", None) is not None
        since_inconclusive = (
            since_requested
            and not result.since_target_crossed
            and result.stop_reason not in _COMPLETE_POST_REASONS
        )
        exit_code = 7 if since_inconclusive else 0

    dated = [post.created_at for post in result.posts if post.created_at is not None]
    oldest = min(dated).date().isoformat() if dated else "unknown"
    newest = max(dated).date().isoformat() if dated else "unknown"
    print(
        f"{len(result.posts)} posts, range {oldest}..{newest}, stop reason: "
        f"{result.stop_reason}. Saved to {_render_output_path(output_path)}",
        file=sys.stderr,
    )
    return exit_code


def _finish_users(
    result: retrieve.UserResult,
    identifier: str,
    args: argparse.Namespace,
) -> int:
    if args.raw and args.no_redact:
        print(
            "WARNING: --no-redact disables raw diagnostic scrubbing.",
            file=sys.stderr,
        )
    elif args.raw:
        for user in result.users:
            if user.raw is not None:
                user.raw = redact.redact(user.raw)
    output_path = _output_path(identifier, args)
    _write_users(result.users, output_path, args.format)
    print(
        f"{len(result.users)} accounts, stop reason: {result.stop_reason}. "
        f"Saved to {_render_output_path(output_path)}",
        file=sys.stderr,
    )
    return 3 if result.stop_reason == "rate_limited" else 0


def _handle_common_errors(exc: Exception, args: argparse.Namespace) -> int:
    """Map planned errors from their canonical ``exit_code`` attributes."""

    if not isinstance(exc, errors.AgenticThreadsError):
        return -1
    safe = redact.redact_raw_text(str(exc))
    if isinstance(exc, (errors.LoginRequiredError, errors.SessionExpiredError)):
        print(
            f"{safe} Run: agentic-threads login --profile {getattr(args, 'profile', 'default')}",
            file=sys.stderr,
        )
    elif isinstance(exc, errors.ChallengeError):
        print(f"checkpoint: {safe}", file=sys.stderr)
    elif isinstance(exc, errors.RateLimitedError):
        print(f"rate-limited: {safe}", file=sys.stderr)
    elif isinstance(exc, errors.EnvelopeParseError):
        print(
            f"response envelope drift: {safe}. Try: agentic-threads doctor --refresh",
            file=sys.stderr,
        )
    elif isinstance(exc, errors.InvalidIdentifierError):
        print(f"invalid identifier: {safe}", file=sys.stderr)
    else:
        print(safe, file=sys.stderr)
    return int(exc.exit_code)


def _report_exception(
    prefix: str,
    exc: Exception,
    args: argparse.Namespace,
) -> int:
    mapped = _handle_common_errors(exc, args)
    if mapped != -1:
        return mapped
    if getattr(args, "verbose", False):
        detail = redact.redact_raw_text(str(exc))
        print(f"{prefix}: {detail}", file=sys.stderr)
    else:
        print(
            f"{prefix}: {type(exc).__name__} (rerun with -v for redacted details)",
            file=sys.stderr,
        )
    return 1


def _load_read_client(
    args: argparse.Namespace,
) -> tuple[client.ReadClient, dict[str, str], dict[str, object]] | int:
    try:
        credential = auth.load_session(
            args.profile,
            profile_dir_override=args.profile_dir,
        )
        doc_ids, features = session.query_data_for(credential)
        read_client = client.ReadClient(
            credential,
            max_requests=config.DEFAULT_MAX_REQUESTS,
        )
    except Exception as exc:
        return _report_exception("could not initialize read session", exc, args)
    return read_client, doc_ids, features


def _retrieve_with_client(
    args: argparse.Namespace,
    operation: Callable[[client.ReadClient, dict[str, str], dict[str, object]], object],
) -> object | int:
    loaded = _load_read_client(args)
    if isinstance(loaded, int):
        return loaded
    read_client, doc_ids, features = loaded
    try:
        return operation(read_client, doc_ids, features)
    except Exception as exc:
        return _report_exception("read failed", exc, args)
    finally:
        read_client.close()


def _finish_or_error(
    result: retrieve.RetrieveResult | retrieve.UserResult,
    identifier: str,
    args: argparse.Namespace,
) -> int:
    try:
        if isinstance(result, retrieve.UserResult):
            return _finish_users(result, identifier, args)
        return _finish(result, identifier, args)
    except Exception as exc:
        return _report_exception("could not save output", exc, args)


def _cmd_feed(args: argparse.Namespace) -> int:
    result = _retrieve_with_client(
        args,
        lambda read_client, doc_ids, features: retrieve.fetch_home(
            read_client,
            doc_ids,
            features,
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            max_requests=config.DEFAULT_MAX_REQUESTS,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        ),
    )
    if isinstance(result, int):
        return result
    return _finish_or_error(result, "home", args)


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        kind, value = auth.normalize_user_identifier(args.identifier, by=args.by)
    except Exception as exc:
        return _report_exception("invalid identifier", exc, args)
    result = _retrieve_with_client(
        args,
        lambda read_client, doc_ids, features: retrieve.fetch_profile(
            read_client,
            doc_ids,
            features,
            kind,
            value,
            replies=args.replies,
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            max_requests=config.DEFAULT_MAX_REQUESTS,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        ),
    )
    if isinstance(result, int):
        return result
    return _finish_or_error(result, args.identifier, args)


def _cmd_post(args: argparse.Namespace) -> int:
    try:
        kind, value = auth.normalize_post_identifier(args.identifier)
    except Exception as exc:
        return _report_exception("invalid identifier", exc, args)
    result = _retrieve_with_client(
        args,
        lambda read_client, doc_ids, features: retrieve.fetch_post(
            read_client,
            doc_ids,
            features,
            kind,
            value,
            replies=not args.no_replies,
            limit=args.limit,
            max_requests=config.DEFAULT_MAX_REQUESTS,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        ),
    )
    if isinstance(result, int):
        return result
    return _finish_or_error(result, args.identifier, args)


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.query.strip():
        return _report_exception(
            "invalid search query",
            errors.InvalidIdentifierError("search query must not be empty"),
            args,
        )
    result = _retrieve_with_client(
        args,
        lambda read_client, doc_ids, features: retrieve.search(
            read_client,
            doc_ids,
            features,
            args.query,
            search_type=args.type,
            limit=args.limit,
            since=_since_datetime(args.since),
            until=_until_datetime(args.until),
            max_requests=config.DEFAULT_MAX_REQUESTS,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        ),
    )
    if isinstance(result, int):
        return result
    return _finish_or_error(result, args.query, args)


def _cmd_social_graph(args: argparse.Namespace) -> int:
    try:
        kind, value = auth.normalize_user_identifier(args.identifier)
    except Exception as exc:
        return _report_exception("invalid identifier", exc, args)
    result = _retrieve_with_client(
        args,
        lambda read_client, doc_ids, features: retrieve.fetch_social_graph(
            read_client,
            doc_ids,
            features,
            args.command,
            kind,
            value,
            limit=args.limit,
            max_requests=config.DEFAULT_MAX_REQUESTS,
            wait_on_limit=args.wait_on_limit,
            max_wait=args.max_wait,
            raw=args.raw,
        ),
    )
    if isinstance(result, int):
        return result
    return _finish_or_error(result, f"{args.command}-{args.identifier}", args)


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "login": _cmd_login,
    "status": _cmd_status,
    "setup": _cmd_setup,
    "doctor": _cmd_doctor,
    "catalog": _cmd_catalog,
    "schema": _cmd_schema,
    "feed": _cmd_feed,
    "fetch": _cmd_fetch,
    "post": _cmd_post,
    "search": _cmd_search,
    "followers": _cmd_social_graph,
    "following": _cmd_social_graph,
}


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        parser.error("--limit must be non-negative")

    max_wait = getattr(args, "max_wait", None)
    if max_wait is not None:
        if not math.isfinite(max_wait):
            parser.error("--max-wait must be finite")
        if max_wait < 0:
            parser.error("--max-wait must be non-negative")
        if not getattr(args, "wait_on_limit", False):
            parser.error("--max-wait requires --wait-on-limit")

    if getattr(args, "no_redact", False) and not getattr(args, "raw", False):
        parser.error("--no-redact requires --raw")

    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if (
        args.command == "search"
        and args.type == "people"
        and (since is not None or until is not None)
    ):
        parser.error("--since/--until cannot be used with search --type people")
    if since is not None and until is not None and since > until:
        parser.error("--since must not be later than --until")

    timeout_seconds = getattr(args, "timeout_seconds", None)
    if timeout_seconds is not None:
        if not math.isfinite(timeout_seconds):
            parser.error("--timeout-seconds must be finite")
        if timeout_seconds < 0:
            parser.error("--timeout-seconds must be non-negative")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    try:
        return _HANDLERS[args.command](args)
    except Exception as exc:
        return _report_exception("command failed", exc, args)


if __name__ == "__main__":
    raise SystemExit(main())
