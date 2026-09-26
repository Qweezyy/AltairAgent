"""Разбор структуры не-Python файлов через tree-sitter."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.codemap.treesitter_outline import outline_treesitter, supported_suffixes


def _outline(tmp_path: Path, name: str, code: str):
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return outline_treesitter(path, name, code)


def test_supported_suffixes():
    assert {".ts", ".go", ".rs", ".java", ".cs", ".jsx"} <= supported_suffixes()
    assert ".py" not in supported_suffixes()  # Python идёт через ast


def test_unsupported_returns_none(tmp_path):
    assert _outline(tmp_path, "x.txt", "hello") is None


def test_typescript_outline(tmp_path):
    pytest.importorskip("tree_sitter_typescript")
    code = (
        "export abstract class Widget {\n"
        "  render(): string { return ''; }\n"
        "  private update(x: number): void {}\n"
        "}\n"
        "export interface Clickable { onClick(): void; }\n"
        "export function make(name: string): Widget { return null as any; }\n"
        "enum Color { Red, Green }\n"
    )
    outline = _outline(tmp_path, "app.ts", code)
    assert outline is not None
    kinds = {(s.name, s.kind, s.parent) for s in outline.symbols}
    assert ("Widget", "class", "") in kinds
    assert ("render", "method", "Widget") in kinds
    assert ("update", "method", "Widget") in kinds
    assert ("Clickable", "interface", "") in kinds
    assert ("make", "function", "") in kinds
    assert ("Color", "enum", "") in kinds


def test_go_outline(tmp_path):
    pytest.importorskip("tree_sitter_go")
    code = (
        "package main\n"
        "type Server struct { Port int }\n"
        "func (s *Server) Start() error { return nil }\n"
        "func main() {}\n"
    )
    outline = _outline(tmp_path, "s.go", code)
    names = {s.name for s in outline.symbols}
    assert {"Server", "Start", "main"} <= names


def test_rust_outline(tmp_path):
    pytest.importorskip("tree_sitter_rust")
    code = (
        "pub struct Config { pub name: String }\n"
        "pub trait Handler { fn handle(&self); }\n"
        "impl Config {\n"
        "    pub fn new(n: String) -> Self { Config { name: n } }\n"
        "}\n"
    )
    outline = _outline(tmp_path, "l.rs", code)
    triples = {(s.name, s.kind, s.parent) for s in outline.symbols}
    assert ("Config", "struct", "") in triples
    assert ("Handler", "interface", "") in triples
    # Метод new собран из impl-блока с родителем Config.
    assert ("new", "method", "Config") in triples


def test_java_outline(tmp_path):
    pytest.importorskip("tree_sitter_java")
    code = (
        "public class Main {\n"
        "  public void run() {}\n"
        "  public static void main(String[] args) {}\n"
        "}\n"
    )
    outline = _outline(tmp_path, "Main.java", code)
    methods = {s.name for s in outline.symbols if s.kind == "method"}
    assert {"run", "main"} <= methods


def test_broken_source_returns_outline(tmp_path):
    """tree-sitter устойчив к синтаксическим ошибкам — не падает, что-то да найдёт."""
    pytest.importorskip("tree_sitter_typescript")
    outline = _outline(tmp_path, "broken.ts", "class Foo { bar( { \nfunction ok() {}\n")
    assert outline is not None  # не бросает исключение


def test_builder_uses_treesitter(tmp_path):
    """outline_file выбирает tree-sitter для .ts (проверяем язык результата)."""
    pytest.importorskip("tree_sitter_typescript")
    from core.codemap.builder import outline_file

    path = tmp_path / "a.ts"
    path.write_text("export function f(): void {}\n", encoding="utf-8")
    outline = outline_file(path, "a.ts")
    assert outline.language == "typescript"
    assert any(s.name == "f" for s in outline.symbols)
