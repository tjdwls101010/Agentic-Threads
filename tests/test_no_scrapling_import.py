"""Base-install imports must not eagerly load the optional browser dependency."""

from __future__ import annotations

import subprocess
import sys

import pytest


def _fresh_import(module: str) -> subprocess.CompletedProcess[str]:
    code = f"""
import builtins
import sys

real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "scrapling" or name.startswith("scrapling."):
        raise AssertionError("base import attempted to load optional scrapling")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
import {module}
assert not any(name == "scrapling" or name.startswith("scrapling.") for name in sys.modules)
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "module",
    [
        "agentic_threads",
        "agentic_threads.cli",
        "agentic_threads.auth",
        "agentic_threads.session",
    ],
)
def test_base_surface_imports_without_loading_scrapling(module):
    result = _fresh_import(module)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
