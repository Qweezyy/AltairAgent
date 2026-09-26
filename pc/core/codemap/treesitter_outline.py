"""Точный разбор структуры не-Python файлов через tree-sitter.

Апгрейд над `generic_outline` (regex): настоящее синтаксическое дерево вместо
шаблонов. Ловит вложенные методы, различает класс/интерфейс/тип/enum, не
спотыкается о многострочные сигнатуры и комментарии.

Грамматики берутся из отдельных пакетов `tree-sitter-<язык>` (самодостаточные
wheels — надёжнее общего language-pack, чей загрузчик падает на Windows). Если
пакета для языка нет или tree-sitter не установлен, функция возвращает None, и
вызывающий код откатывается на regex-разбор.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.codemap.model import FileOutline, Symbol
from core.logging_setup import get_logger

logger = get_logger("codemap.treesitter")

#: Суффикс -> имя языка в наших правилах.
_SUFFIX_LANG: dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
}

#: Язык -> (модуль грамматики, имя функции-фабрики языка).
_GRAMMARS: dict[str, tuple[str, str]] = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
}


class _Rules:
    """Какие узлы считать определениями и где искать вложенные методы."""

    def __init__(
        self,
        top: dict[str, str],
        containers: set[str],
        methods: dict[str, str],
        *,
        lexical: bool = False,
    ) -> None:
        self.top = top  # тип узла -> вид символа (верхний уровень)
        self.containers = containers  # типы, внутри которых ищем методы
        self.methods = methods  # тип узла-метода -> вид символа
        self.lexical = lexical  # разбирать ли const/let (JS/TS)


# Общие для JS/TS правила методов.
_JS_METHODS = {"method_definition": "method"}

_RULES: dict[str, _Rules] = {
    "javascript": _Rules(
        top={
            "class_declaration": "class",
            "function_declaration": "function",
            "generator_function_declaration": "function",
        },
        containers={"class_declaration"},
        methods=_JS_METHODS,
        lexical=True,
    ),
    "typescript": _Rules(
        top={
            "class_declaration": "class",
            "abstract_class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type",
            "enum_declaration": "enum",
            "function_declaration": "function",
        },
        containers={"class_declaration", "abstract_class_declaration"},
        methods=_JS_METHODS,
        lexical=True,
    ),
    "go": _Rules(
        top={
            "function_declaration": "function",
            "method_declaration": "method",
            "type_declaration": "type",
        },
        containers=set(),
        methods={},
    ),
    "rust": _Rules(
        top={
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "interface",
            "impl_item": "impl",
            "mod_item": "module",
        },
        containers={"impl_item", "trait_item"},
        methods={"function_item": "method"},
    ),
    "java": _Rules(
        top={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "struct",
        },
        containers={"class_declaration", "interface_declaration", "enum_declaration"},
        methods={"method_declaration": "method", "constructor_declaration": "method"},
    ),
    "csharp": _Rules(
        top={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "enum_declaration": "enum",
            "record_declaration": "struct",
        },
        containers={"class_declaration", "interface_declaration", "struct_declaration"},
        methods={"method_declaration": "method", "constructor_declaration": "method"},
    ),
}

# "tsx" переиспользует правила typescript.
_RULES["tsx"] = _RULES["typescript"]

#: Кэш собранных парсеров по языку (сборка грамматики не бесплатна).
_parser_cache: dict[str, Any] = {}


def supported_suffixes() -> set[str]:
    return set(_SUFFIX_LANG)


def _get_parser(language: str) -> Any | None:
    if language in _parser_cache:
        return _parser_cache[language]
    grammar = _GRAMMARS.get(language)
    if grammar is None:
        return None
    module_name, factory = grammar
    try:
        from tree_sitter import Language, Parser

        module = __import__(module_name)
        lang = Language(getattr(module, factory)())
        parser = Parser(lang)
    except Exception as exc:  # noqa: BLE001 - нет пакета грамматики или tree-sitter
        logger.info("tree-sitter для %s недоступен: %s", language, exc)
        _parser_cache[language] = None
        return None
    _parser_cache[language] = parser
    return parser


def _node_name(node: Any, source: bytes) -> str | None:
    """Имя определения. Обычно поле name; для Go type_declaration — из type_spec."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return source[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")
    # Go: type_declaration -> type_spec -> name.
    for child in node.named_children:
        if child.type in ("type_spec", "type_alias"):
            inner = child.child_by_field_name("name")
            if inner is not None:
                return source[inner.start_byte : inner.end_byte].decode("utf-8", "replace")
    # Rust impl: цель импликации вместо имени.
    typ = node.child_by_field_name("type") or node.child_by_field_name("trait")
    if typ is not None:
        return source[typ.start_byte : typ.end_byte].decode("utf-8", "replace")
    return None


def _signature(node: Any, source: bytes) -> str:
    """Первая значимая строка узла как сигнатура (до тела/переноса)."""
    text = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
    for stop in ("{", "\n", "=>", ";"):
        index = text.find(stop)
        if index != -1:
            text = text[:index]
    return " ".join(text.split()).strip()


def _body(node: Any) -> Any | None:
    return node.child_by_field_name("body")


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _lexical_symbol(node: Any, source: bytes) -> Symbol | None:
    """const/let в JS/TS: стрелочные функции -> function, ВЕРХНИЙ_РЕГИСТР -> const."""
    for declarator in node.named_children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        value = declarator.child_by_field_name("value")
        vtype = value.type if value is not None else ""
        if vtype in ("arrow_function", "function", "function_expression"):
            kind = "function"
        elif name.isupper() or (name[:1].isupper() and "_" in name):
            kind = "const"
        else:
            continue  # обычные переменные в карту не тащим — это шум
        return Symbol(
            name=name,
            kind=kind,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=_signature(node, source),
        )
    return None


def _collect(node: Any, source: bytes, rules: _Rules, symbols: list[Symbol]) -> None:
    """Рекурсивно обходит дерево, снимая top-level определения и методы контейнеров."""
    for child in node.named_children:
        # export/public-обёртки прозрачны: разбираем их содержимое как top-level.
        if child.type in ("export_statement", "export_default_declaration"):
            _collect(child, source, rules, symbols)
            continue
        if rules.lexical and child.type in ("lexical_declaration", "variable_declaration"):
            lexical = _lexical_symbol(child, source)
            if lexical is not None:
                symbols.append(lexical)
            continue
        kind = rules.top.get(child.type)
        if kind is None:
            continue
        name = _node_name(child, source)
        if not name:
            continue
        symbols.append(
            Symbol(
                name=name,
                kind=kind,
                line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                signature=_signature(child, source),
            )
        )
        if child.type in rules.containers and rules.methods:
            _collect_methods(child, source, rules, name, symbols)


def _collect_methods(
    container: Any, source: bytes, rules: _Rules, parent: str, symbols: list[Symbol]
) -> None:
    body = _body(container)
    if body is None:
        return
    for member in body.named_children:
        kind = rules.methods.get(member.type)
        if kind is None:
            continue
        name = _node_name(member, source)
        if not name:
            continue
        symbols.append(
            Symbol(
                name=name,
                kind=kind,
                line=member.start_point[0] + 1,
                end_line=member.end_point[0] + 1,
                signature=_signature(member, source),
                parent=parent,
            )
        )


#: Узлы-импорты по языкам (для списка зависимостей файла).
_IMPORT_NODES: dict[str, set[str]] = {
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "csharp": {"using_directive"},
}


def _collect_imports(root: Any, language: str, source: bytes) -> list[str]:
    """Собирает модули/пути из импортов верхнего уровня."""
    node_types = _IMPORT_NODES.get(language, set())
    if not node_types:
        return []
    imports: list[str] = []
    for child in root.named_children:
        if child.type not in node_types:
            continue
        # У JS/TS есть строка-источник; у остальных берём читаемый хвост объявления.
        string_node = child.child_by_field_name("source")
        if string_node is not None:
            imports.append(_text(string_node, source).strip("'\"`"))
        else:
            text = _text(child, source).strip().rstrip(";")
            for keyword in ("import ", "use ", "using "):
                if text.startswith(keyword):
                    text = text[len(keyword):]
            imports.append(text.strip().strip('"'))
    # Go оборачивает несколько импортов в один import_declaration с блоком.
    return [imp for imp in imports if imp][:40]


def outline_treesitter(path: Path, rel_path: str, source: str) -> FileOutline | None:
    """Структура файла через tree-sitter. None, если язык/грамматика недоступны."""
    language = _SUFFIX_LANG.get(path.suffix.lower())
    if language is None:
        return None
    parser = _get_parser(language)
    if parser is None:
        return None
    data = source.encode("utf-8")
    try:
        tree = parser.parse(data)
    except Exception as exc:  # noqa: BLE001 - битый файл не должен ронять карту
        logger.info("tree-sitter не разобрал %s: %s", rel_path, exc)
        return None

    symbols: list[Symbol] = []
    _collect(tree.root_node, data, _RULES[language], symbols)
    imports = _collect_imports(tree.root_node, language, data)
    return FileOutline(
        path=path,
        rel_path=rel_path,
        language=language,
        lines=source.count("\n") + 1,
        symbols=symbols,
        imports=imports,
    )
