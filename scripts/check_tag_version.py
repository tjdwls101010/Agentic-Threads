#!/usr/bin/env python3
"""Fail if a release tag and the package's static versions disagree.

Usage: check_tag_version.py <tag-name, e.g. "v0.1.0">
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path


def _source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            continue
        if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            return statement.value.value
        break
    raise ValueError(f"{path} must define __version__ as a static string")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_tag_version.py <tag-name>", file=sys.stderr)
        return 1

    tag_name = sys.argv[1]
    tag_version = tag_name.removeprefix("v")
    root = Path(__file__).resolve().parent.parent

    with (root / "pyproject.toml").open("rb") as file:
        pyproject_version = tomllib.load(file)["project"]["version"]
    source_version = _source_version(root / "src" / "agentic_threads" / "__init__.py")

    if tag_version != pyproject_version or pyproject_version != source_version:
        print(
            "::error::release versions do not match: "
            f"tag {tag_name!r}, pyproject.toml {pyproject_version!r}, "
            f"agentic_threads.__version__ {source_version!r}",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: tag {tag_name!r} matches pyproject.toml and "
        f"agentic_threads.__version__ ({pyproject_version!r})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
