"""Структурный AST-поиск по коду Python."""

from __future__ import annotations

import pytest

from core.codemap.structural import _name_matches, structural_search

_SAMPLE = '''
import os
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class User(BaseModel):
    name: str


class Admin(User):
    level: int


@router.get("/users")
async def list_users():
    return save()


@router.post("/users")
def create_user() -> dict:
    raise ValueError("bad")


def helper(x):
    return x
'''


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "app.py").write_text(_SAMPLE, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def (((", encoding="utf-8")  # не должен ломать обход
    return tmp_path


def test_name_matches():
    assert _name_matches("router.get", "get")
    assert _name_matches("app.router.get", "router.get")
    assert _name_matches("get", "get")
    assert not _name_matches("router.post", "get")
    assert not _name_matches("myget", "get")


def test_decorated_by(project):
    matches = structural_search(project, project, "decorated_by", "router.get")
    assert len(matches) == 1
    assert matches[0].label == "def list_users"
    assert "async" in matches[0].detail


def test_decorated_by_last_segment(project):
    # target «get» без модуля тоже находит @router.get.
    assert len(structural_search(project, project, "decorated_by", "get")) == 1


def test_subclass_of(project):
    direct = structural_search(project, project, "subclass_of", "BaseModel")
    assert {m.label for m in direct} == {"class User"}
    assert structural_search(project, project, "subclass_of", "User")[0].label == "class Admin"


def test_calls(project):
    calls = structural_search(project, project, "calls", "save")
    assert len(calls) == 1
    assert calls[0].line > 0


def test_raises(project):
    r = structural_search(project, project, "raises", "ValueError")
    assert len(r) == 1 and "ValueError" in r[0].label


def test_imports(project):
    assert structural_search(project, project, "imports", "os")
    assert structural_search(project, project, "imports", "fastapi")
    # from-import по имени тоже: BaseModel импортирован из pydantic.
    assert structural_search(project, project, "imports", "BaseModel")


def test_async_functions(project):
    a = structural_search(project, project, "async_functions", "")
    assert {m.label for m in a} == {"async def list_users"}


def test_missing_return_type(project):
    miss = structural_search(project, project, "missing_return_type", "")
    labels = {m.label for m in miss}
    # list_users (async, без ->), helper — без аннотации; create_user имеет -> dict.
    assert "def list_users" in labels
    assert "def helper" in labels
    assert "def create_user" not in labels


def test_broken_file_skipped(project):
    # broken.py с синтаксической ошибкой не роняет поиск.
    assert structural_search(project, project, "async_functions", "")
