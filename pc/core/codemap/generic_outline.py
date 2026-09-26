"""Извлечение определений для не-Python языков.

Честно о подходе: это разбор по шаблонам, а не полноценный AST. Он находит
объявления верхнего уровня (классы, функции, типы) и этого достаточно для
навигации: агент получает имя, строку и сигнатуру, а дальше читает файл.

Такой вариант выбран сознательно вместо tree-sitter: последний тянет бинарные
грамматики под каждый язык, а надёжность и лёгкость установки для локального
агента важнее идеального разбора. Если понадобится точность, интерфейс
`outline_file()` позволяет добавить tree-sitter как ещё один бэкенд, не трогая
инструменты.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.codemap.model import FileOutline, Symbol

#: Расширение -> (язык, список правил (regex, вид символа))
LANGUAGE_RULES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    ".js": (
        "javascript",
        [
            (r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", "function"),
            (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", "function"),
            (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?function", "function"),
            (r"^\s*(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=", "const"),
        ],
    ),
    ".ts": (
        "typescript",
        [
            (r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:export\s+)?interface\s+(\w+)", "interface"),
            (r"^\s*(?:export\s+)?type\s+(\w+)", "type"),
            (r"^\s*(?:export\s+)?enum\s+(\w+)", "enum"),
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", "function"),
            (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?[(<]", "function"),
            (r"^\s*(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*[:=]", "const"),
        ],
    ),
    ".go": (
        "go",
        [
            (r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", "function"),
            (r"^\s*type\s+(\w+)\s+struct", "struct"),
            (r"^\s*type\s+(\w+)\s+interface", "interface"),
        ],
    ),
    ".rs": (
        "rust",
        [
            (r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", "function"),
            (r"^\s*(?:pub\s+)?struct\s+(\w+)", "struct"),
            (r"^\s*(?:pub\s+)?enum\s+(\w+)", "enum"),
            (r"^\s*(?:pub\s+)?trait\s+(\w+)", "interface"),
            (r"^\s*impl(?:<[^>]*>)?\s+(\w+)", "class"),
        ],
    ),
    ".java": (
        "java",
        [
            (r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:public\s+|private\s+)?interface\s+(\w+)", "interface"),
            (r"^\s*(?:public|private|protected)\s+[\w<>\[\], ]+\s+(\w+)\s*\(", "method"),
        ],
    ),
    ".cs": (
        "csharp",
        [
            (r"^\s*(?:public\s+|private\s+|internal\s+)?(?:sealed\s+|abstract\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:public\s+|private\s+|internal\s+)?interface\s+(\w+)", "interface"),
            (r"^\s*(?:public|private|protected|internal)\s+[\w<>\[\], ]+\s+(\w+)\s*\(", "method"),
        ],
    ),
    ".php": (
        "php",
        [
            (r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", "class"),
            (r"^\s*interface\s+(\w+)", "interface"),
            (r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)", "function"),
        ],
    ),
    ".rb": (
        "ruby",
        [
            (r"^\s*class\s+(\w+)", "class"),
            (r"^\s*module\s+(\w+)", "class"),
            (r"^\s*def\s+(?:self\.)?(\w+)", "function"),
        ],
    ),
    ".kt": (
        "kotlin",
        [
            (r"^\s*(?:open\s+|data\s+|sealed\s+)?class\s+(\w+)", "class"),
            (r"^\s*interface\s+(\w+)", "interface"),
            (r"^\s*(?:suspend\s+)?fun\s+(\w+)", "function"),
        ],
    ),
    ".swift": (
        "swift",
        [
            (r"^\s*(?:public\s+|private\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:public\s+|private\s+)?struct\s+(\w+)", "struct"),
            (r"^\s*(?:public\s+|private\s+)?protocol\s+(\w+)", "interface"),
            (r"^\s*(?:public\s+|private\s+)?func\s+(\w+)", "function"),
        ],
    ),
    ".sh": ("shell", [(r"^\s*(?:function\s+)?(\w+)\s*\(\s*\)\s*\{", "function")]),
    ".sql": (
        "sql",
        [
            (r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", "struct"),
            (r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+[`\"]?(\w+)", "function"),
            (r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[`\"]?(\w+)", "type"),
        ],
    ),
}

# Родственные расширения используют те же правила.
LANGUAGE_RULES[".jsx"] = ("javascript", LANGUAGE_RULES[".js"][1])
LANGUAGE_RULES[".mjs"] = ("javascript", LANGUAGE_RULES[".js"][1])
LANGUAGE_RULES[".cjs"] = ("javascript", LANGUAGE_RULES[".js"][1])
LANGUAGE_RULES[".tsx"] = ("typescript", LANGUAGE_RULES[".ts"][1])
LANGUAGE_RULES[".mts"] = ("typescript", LANGUAGE_RULES[".ts"][1])
LANGUAGE_RULES[".bash"] = ("shell", LANGUAGE_RULES[".sh"][1])
LANGUAGE_RULES[".vue"] = ("vue", LANGUAGE_RULES[".ts"][1])
LANGUAGE_RULES[".svelte"] = ("svelte", LANGUAGE_RULES[".ts"][1])

# Без re.VERBOSE: в этом шаблоне есть '#include', а в VERBOSE '#' начинает комментарий.
_IMPORT_RE = re.compile(
    r"""^\s*(?:import\s+.*?from\s+['"](?P<from>[^'"]+)['"]"""
    r"""|import\s+['"](?P<bare>[^'"]+)['"]"""
    r"""|(?:const|let|var)\s+.*?=\s*require\(['"](?P<req>[^'"]+)['"]\)"""
    r"""|use\s+(?P<rust>[\w:]+)"""
    r"""|\#include\s+[<"](?P<c>[^>"]+)[>"])"""
)


def supported_suffixes() -> set[str]:
    return set(LANGUAGE_RULES) | {".py", ".pyi"}


def outline_generic(path: Path, rel_path: str, source: str) -> FileOutline:
    language, rules = LANGUAGE_RULES.get(path.suffix.lower(), ("text", []))
    lines = source.splitlines()
    outline = FileOutline(path=path, rel_path=rel_path, language=language, lines=len(lines))

    compiled = [(re.compile(pattern), kind) for pattern, kind in rules]
    seen: set[tuple[str, int]] = set()

    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("//", "#", "*", "/*")):
            continue

        import_match = _IMPORT_RE.match(raw)
        if import_match:
            module = next((v for v in import_match.groupdict().values() if v), None)
            if module and module not in outline.imports:
                outline.imports.append(module)
            continue

        for pattern, kind in compiled:
            match = pattern.match(raw)
            if not match:
                continue
            name = match.group(1)
            if (name, number) in seen:
                continue
            seen.add((name, number))
            outline.symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    line=number,
                    signature=stripped.rstrip("{").strip()[:160],
                )
            )
            break

    return outline
