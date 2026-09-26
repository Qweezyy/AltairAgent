"""Интерактивный терминал: настоящий PTY-процесс в окне приложения."""

from core.terminal.session import TerminalSession, TerminalUnavailable

__all__ = ["TerminalSession", "TerminalUnavailable"]
