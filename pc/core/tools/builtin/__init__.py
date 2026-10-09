"""Встроенные инструменты агента.

ЧТОБЫ ДОБАВИТЬ ИНСТРУМЕНТ:
  1. создайте класс-наследник Tool в новом или существующем модуле рядом;
  2. добавьте его в BUILTIN_TOOLS ниже;
  3. напишите тест в tests/test_tools.py.
Больше нигде ничего править не нужно.
"""

from __future__ import annotations

from core.tools.base import Tool
from core.tools.builtin.analytics_tools import AnalyzeStatementTool, CreateChartTool
from core.tools.builtin.android_tools import (
    AndroidAvdsTool,
    AndroidDevicesTool,
    AndroidDiagnoseTool,
    AndroidInstallTool,
    AndroidLogcatTool,
    AndroidScreenshotTool,
    AndroidStartTool,
    AndroidStopTool,
)
from core.tools.builtin.ask import AskTool
from core.tools.builtin.background_tools import (
    ReadBackgroundTool,
    RunBackgroundTool,
    StopBackgroundTool,
    WaitForTool,
    WatchBackgroundTool,
)
from core.tools.builtin.bridge_tools import (
    PhoneAskUserTool,
    PhoneCapabilityTool,
    PhoneRequestFileTool,
    PhoneRequestPhotoTool,
)
from core.tools.builtin.browser_tools import (
    BrowserBatchTool,
    BrowserClickTool,
    BrowserConsoleTool,
    BrowserDownloadsTool,
    BrowserFillTool,
    BrowserFindTool,
    BrowserHandoffTool,
    BrowserHoverTool,
    BrowserJsTool,
    BrowserNavigateTool,
    BrowserNetworkTool,
    BrowserPressTool,
    BrowserReadTool,
    BrowserRequestsTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserSelectTool,
    BrowserTabsTool,
    BrowserTextTool,
    BrowserTypeTool,
    BrowserUploadTool,
    BrowserWaitTool,
)
from core.tools.builtin.canvas_tools import (
    AttachFileTool,
    ShowGraphicTool,
    ShowInteractiveTool,
    ShowUITool,
)
from core.tools.builtin.codemap_tools import AstSearchTool, CodeMapTool, FindSymbolTool
from core.tools.builtin.context_tools import (
    ContextCompressTool,
    ContextDropTool,
    ToolOutputTool,
    ContextInfoTool,
)
from core.tools.builtin.coverage_tools import TestCoverageTool
from core.tools.builtin.data_tools import ProfileDataTool, QueryDataTool
from core.tools.builtin.db_tools import DbDiagramTool, DbQueryTool, DbSchemaTool
from core.tools.builtin.devserver_tools import (
    ListDevServersTool,
    ReadDevServerTool,
    StartDevServerTool,
    StopDevServerTool,
)
from core.tools.builtin.files import (
    DeletePathTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from core.tools.builtin.git_tools import (
    GitBlameTool,
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitRestoreTool,
    GitStatusTool,
)
from core.tools.builtin.image_search_tools import FindImagesTool
from core.tools.builtin.lsp_nav_tools import CodeIntelTool
from core.tools.builtin.lsp_tools import TypeCheckTool
from core.tools.builtin.media_tools import VideoTool
from core.tools.builtin.memory_tools import (
    MemoryDeleteTool,
    MemoryEditTool,
    MemoryReadTool,
    RecallTool,
    RememberTool,
    SearchChatsTool,
    SuggestMemoryTool,
)
from core.tools.builtin.patch import ApplyPatchTool
from core.tools.builtin.plan import UpdatePlanTool
from core.tools.builtin.python_exec import RunPythonTool
from core.tools.builtin.quality_tools import RunLintTool, RunTestsTool
from core.tools.builtin.bodies_tools import BodiesTool
from core.tools.builtin.self_check_tools import SelfCheckTool
from core.tools.builtin.system_change_tools import SafeSystemChangeTool
from core.tools.builtin.reminder_tools import (
    CancelReminderTool,
    ListRemindersTool,
    SetReminderTool,
    WatchConditionTool,
)
from core.tools.builtin.research import BrowsePageTool, DeepResearchTool, ReadDocumentTool
from core.tools.builtin.search import FindFilesTool, GrepSearchTool
from core.tools.builtin.secret_tools import ListSecretsTool, RequestSecretTool
from core.tools.builtin.security_tools import ScanSecretsTool, SecurityScanTool
from core.tools.builtin.shell import ExecuteCommandTool
from core.tools.builtin.skills_tools import CreateSkillTool, ListSkillsTool, ReadSkillTool
from core.tools.builtin.spec_tools import WritePlanTool
from core.tools.builtin.stem_tools import AnkiDeckTool, BibliographyTool, SolveMathTool
from core.tools.builtin.subagent_tools import SpawnSubagentTool
from core.tools.builtin.tool_search import ToolSearchTool
from core.tools.builtin.verify_tools import DifferentialCheckTool, ReviewChangesTool
from core.tools.builtin.vision_tools import (
    AuditUITool,
    ScreenshotUITool,
    ShowImageTool,
    ViewImageTool,
)
from core.tools.builtin.web import (
    DownloadFileTool,
    FetchUrlTool,
    HttpRequestTool,
    WebSearchTool,
)


def builtin_tools() -> list[Tool]:
    """Свежие экземпляры всех встроенных инструментов."""
    return [
        # deferred loading: finds and activates the rest of the tools on demand
        ToolSearchTool(),
        # the machines this agent works on (this PC, the servers)
        BodiesTool(),
        # sshd / firewall / network changes on a server, undone by the guardian unless confirmed
        SafeSystemChangeTool(),
        # the agent looks at itself: services, guardian, disk, recent failures
        SelfCheckTool(),
        # диалог с пользователем
        AskTool(),
        RequestSecretTool(),
        ListSecretsTool(),
        # планирование
        UpdatePlanTool(),
        WritePlanTool(),
        # файловая система
        ListDirectoryTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ApplyPatchTool(),
        DeletePathTool(),
        # поиск и навигация по коду
        FindFilesTool(),
        GrepSearchTool(),
        CodeMapTool(),
        FindSymbolTool(),
        AstSearchTool(),
        CodeIntelTool(),
        # субагенты (по галочке в настройках)
        SpawnSubagentTool(),
        # выполнение и проверка
        ExecuteCommandTool(),
        RunPythonTool(),
        RunTestsTool(),
        RunLintTool(),
        TypeCheckTool(),
        TestCoverageTool(),
        DifferentialCheckTool(),
        ReviewChangesTool(),
        ScanSecretsTool(),
        SecurityScanTool(),
        # мост к телефону: обратные запросы исполнителя ПК к координатору-телефону
        PhoneRequestFileTool(),
        PhoneRequestPhotoTool(),
        PhoneAskUserTool(),
        PhoneCapabilityTool(),
        # Android: внешний эмулятор и проверка UI
        AndroidDiagnoseTool(),
        AndroidDevicesTool(),
        AndroidAvdsTool(),
        AndroidStartTool(),
        AndroidStopTool(),
        AndroidInstallTool(),
        AndroidScreenshotTool(),
        AndroidLogcatTool(),
        # наблюдатель за dev-серверами
        StartDevServerTool(),
        ReadDevServerTool(),
        StopDevServerTool(),
        ListDevServersTool(),
        # фоновые команды общего назначения
        RunBackgroundTool(),
        ReadBackgroundTool(),
        StopBackgroundTool(),
        WaitForTool(),
        WatchBackgroundTool(),
        # git: статус, диффы, история, коммиты
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
        GitBlameTool(),
        GitCommitTool(),
        GitBranchTool(),
        GitRestoreTool(),
        # интернет
        WebSearchTool(),
        FetchUrlTool(),
        HttpRequestTool(),
        DownloadFileTool(),
        # интерактивный браузер агента (постоянная видимая сессия)
        BrowserNavigateTool(),
        BrowserReadTool(),
        BrowserClickTool(),
        BrowserTypeTool(),
        BrowserScrollTool(),
        BrowserTabsTool(),
        BrowserScreenshotTool(),
        BrowserHandoffTool(),
        BrowserFindTool(),
        BrowserPressTool(),
        BrowserSelectTool(),
        BrowserHoverTool(),
        BrowserUploadTool(),
        BrowserWaitTool(),
        BrowserDownloadsTool(),
        BrowserNetworkTool(),
        BrowserTextTool(),
        BrowserFillTool(),
        BrowserJsTool(),
        BrowserBatchTool(),
        BrowserConsoleTool(),
        BrowserRequestsTool(),
        # глубокое исследование
        BrowsePageTool(),
        ReadDocumentTool(),
        DeepResearchTool(),
        FindImagesTool(),
        # визуальная верификация вёрстки
        ScreenshotUITool(),
        AuditUITool(),
        ViewImageTool(),
        ShowImageTool(),
        # учёба и наука
        SolveMathTool(),
        BibliographyTool(),
        AnkiDeckTool(),
        # аналитика и быт
        CreateChartTool(),
        AnalyzeStatementTool(),
        # аналитика больших данных (DuckDB)
        ProfileDataTool(),
        QueryDataTool(),
        # видео (ffmpeg)
        VideoTool(),
        # базы данных (SQLite)
        DbSchemaTool(),
        DbQueryTool(),
        DbDiagramTool(),
        # memory (notes + index) and search in chats
        RememberTool(),
        MemoryReadTool(),
        MemoryEditTool(),
        MemoryDeleteTool(),
        RecallTool(),
        SearchChatsTool(),
        SuggestMemoryTool(),
        # самоконтроль контекстного окна
        ContextInfoTool(),
        ContextCompressTool(),
        ContextDropTool(),
        ToolOutputTool(),
        # инлайн-канвас в ленте: графика/виджет/вложение
        ShowGraphicTool(),
        ShowInteractiveTool(),
        ShowUITool(),
        AttachFileTool(),
        # напоминания и условные уведомления
        SetReminderTool(),
        WatchConditionTool(),
        ListRemindersTool(),
        CancelReminderTool(),
        # навыки
        ListSkillsTool(),
        ReadSkillTool(),
        CreateSkillTool(),
    ]


__all__ = ["builtin_tools"]
