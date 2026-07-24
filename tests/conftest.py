import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], dict[str, Any]]:
    """Return a fresh parsed copy of one hand-authored JSON fixture."""

    def _load(name: str) -> dict[str, Any]:
        payload = (FIXTURES_DIR / name).read_bytes()
        parsed = json.loads(payload)
        assert isinstance(parsed, dict), f"fixture {name!r} must contain a JSON object"
        return parsed

    return _load
