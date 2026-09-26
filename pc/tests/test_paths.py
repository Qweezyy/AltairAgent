from __future__ import annotations

import pytest

from core.errors import PathNotAllowed
from core.security.paths import resolve_path


def test_relative_path_resolves_inside_workspace(settings):
    result = resolve_path("data/file.txt", settings=settings)
    assert str(result).startswith(str(settings.workspace))


def test_escape_via_dotdot_is_blocked(settings):
    with pytest.raises(PathNotAllowed):
        resolve_path("../../secrets.txt", settings=settings)


def test_absolute_outside_workspace_is_blocked(settings):
    outside = "C:\\Windows\\System32" if __import__("os").name == "nt" else "/etc"
    with pytest.raises(PathNotAllowed):
        resolve_path(outside, settings=settings)


def test_allow_outside_flag_disables_sandbox(settings):
    settings.allow_outside_workspace = True
    assert resolve_path("../anything.txt", settings=settings)


def test_extra_roots_are_allowed(settings, tmp_path):
    extra = tmp_path.parent / "extra_root"
    extra.mkdir(exist_ok=True)
    settings.extra_allowed_roots = str(extra)
    assert resolve_path(str(extra / "x.txt"), settings=settings)


def test_must_exist_check(settings):
    with pytest.raises(PathNotAllowed):
        resolve_path("no_such_file.txt", settings=settings, must_exist=True)
