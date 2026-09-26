"""Языковая диагностика через pyright (ошибки типов до запуска тестов)."""

from core.lsp.diagnostics import Diagnostic, TypeCheckResult, type_check

__all__ = ["Diagnostic", "TypeCheckResult", "type_check"]
