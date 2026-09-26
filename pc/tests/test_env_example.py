""".env.example is copied as the user's settings on first launch of the packaged app.

So it must not carry the developer's machine (a D:\\ workspace crashes startup on a PC
without that drive), stale keys of removed settings, or values that contradict the
product defaults (MAX_STEPS=25 silently capped every new user's runs).
"""

from __future__ import annotations

import re
from pathlib import Path

from core.settings import Settings

EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def _active_pairs() -> dict[str, str]:
    pairs = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$", line)
        if match:
            pairs[match.group(1)] = match.group(2).strip()
    return pairs


def test_every_active_key_is_a_real_setting():
    fields = {name.upper() for name in Settings.model_fields}
    unknown = sorted(set(_active_pairs()) - fields)
    assert not unknown, f"stale keys in .env.example: {unknown}"


def test_no_machine_specific_paths():
    for key, value in _active_pairs().items():
        assert not re.match(r"^[A-Za-z]:[\\/]", value), f"{key}={value} is a path from one machine"


def test_limits_match_product_defaults():
    pairs = _active_pairs()
    defaults = Settings.model_fields
    for key in ("MAX_STEPS", "MAX_RUN_TOKENS", "TOOL_SEARCH", "TOOL_RESULT_CLEARING"):
        default = defaults[key.lower()].default
        assert pairs[key].lower() == str(default).lower(), f"{key}={pairs[key]} but the default is {default}"
