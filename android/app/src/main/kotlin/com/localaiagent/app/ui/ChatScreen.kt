@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class,
    androidx.compose.foundation.ExperimentalFoundationApi::class,
)

package com.localaiagent.app.ui

import android.graphics.Bitmap
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.Crossfade
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.automirrored.rounded.AltRoute
import androidx.compose.material.icons.automirrored.rounded.Reply
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.Autorenew
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.DpOffset
import com.localaiagent.app.R
import androidx.compose.material.icons.rounded.ArrowUpward
import androidx.compose.material.icons.rounded.AttachFile
import androidx.compose.material.icons.rounded.AudioFile
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.ChevronLeft
import androidx.compose.material.icons.rounded.ChevronRight
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Dashboard
import androidx.compose.material.icons.rounded.Extension
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.Draw
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.FolderOpen
import androidx.compose.material.icons.rounded.Computer
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material.icons.rounded.Key
import androidx.compose.material.icons.rounded.Memory
import androidx.compose.material.icons.rounded.Menu
import androidx.compose.material.icons.rounded.Palette
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.PhotoLibrary
import androidx.compose.material.icons.rounded.PlayCircle
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Summarize
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.TableChart
import androidx.compose.material.icons.rounded.Videocam
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.localaiagent.app.ChatMessage
import com.localaiagent.app.ChatUiState
import com.localaiagent.app.LibraryItem
import com.localaiagent.app.bridge.PcBridgeFacade
import com.localaiagent.app.data.ALL_CAPS
import com.localaiagent.app.data.CAP_LABEL
import com.localaiagent.app.data.ModelProfile
import androidx.compose.foundation.Canvas
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.animation.core.LinearEasing
import androidx.compose.ui.platform.LocalClipboardManager
import com.localaiagent.app.parsePairLink
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.unit.Dp
import com.localaiagent.app.ui.theme.ACCENT_CHOICES
import com.localaiagent.app.ui.theme.Brand
import com.localaiagent.app.ui.theme.Dims
import com.localaiagent.app.ui.theme.Semantic
import kotlinx.coroutines.launch

private const val CONTEXT_BUDGET = 128_000f

@Composable
fun ChatScreen(
    state: ChatUiState,
    onSend: (String) -> Unit,
    onSaveBridge: (String, String, String) -> Unit = { _, _, _ -> },
    onTestBridge: (String, String) -> Unit = { _, _ -> },
    onSaveTheme: (String, Long) -> Unit = { _, _ -> },
    onSelectAppIcon: (String) -> Unit = {},
    onSetLanguage: (String) -> Unit = {},
    onSaveUserProfile: (String) -> Unit = {},
    onSelectModel: (String) -> Unit = {},
    onSaveModel: (ModelProfile) -> Unit = {},
    onDeleteModel: (String) -> Unit = {},
    onNewChat: () -> Unit = {},
    onSwitchChat: (String) -> Unit = {},
    onAttachCamera: (Bitmap) -> Unit = {},
    onPickImageUri: (android.net.Uri) -> Unit = {},
    onPickFileUri: (android.net.Uri) -> Unit = {},
    onClearAttachment: () -> Unit = {},
    chatMemoryProvider: () -> String = { "" },
    onSaveChatMemory: (String) -> Unit = {},
    chatItemsProvider: () -> List<LibraryItem> = { emptyList() },
    searchProvider: (String) -> List<com.localaiagent.app.ChatSearchHit> = { emptyList() },
    onEditMessage: (Int, String) -> Unit = { _, _ -> },
    onRegenerate: (Int) -> Unit = {},
    onRevert: (Int) -> Unit = {},
    onBranch: (Int) -> Unit = {},
    onQuote: (String) -> Unit = {},
    onSteer: (String) -> Unit = {},
    onSaveSelection: (String, Boolean) -> Unit = { _, _ -> },
    onAskSelection: (String, String) -> Unit = { _, _ -> },
    onSummarize: () -> Unit = {},
    onSwitchVersion: (String, Int) -> Unit = { _, _ -> },
    onRemix: (String, String) -> Unit = { _, _ -> },
    onReact: (String, String) -> Unit = { _, _ -> },
    onSetReactionsOnUser: (Boolean) -> Unit = {},
    onClearQuote: () -> Unit = {},
    onCancelRun: () -> Unit = {},
    onSubmitAsk: (List<com.localaiagent.app.AskAnswer>) -> Unit = {},
    onCancelAsk: () -> Unit = {},
    onProvideFiles: (List<android.net.Uri>) -> Unit = {},
    onCancelFileReq: () -> Unit = {},
    onSubmitSecret: (String, String, String) -> Unit = { _, _, _ -> },
    onCancelSecret: () -> Unit = {},
    onConfirmSecret: (Boolean) -> Unit = {},
    onConfirmMemory: (Boolean) -> Unit = {},
    onConfirmApproval: (String) -> Unit = {},
    onSetHaptics: (Boolean) -> Unit = {},
    onAddToBoard: (String) -> Unit = {},
    onRemoveFromBoard: (Int) -> Unit = {},
    onClearBoard: () -> Unit = {},
    onAddSecret: (String, String, String, String) -> Unit = { _, _, _, _ -> },
    onRemoveSecret: (String) -> Unit = {},
    onSetSecretAvailability: (String, String) -> Unit = { _, _ -> },
    // Plugins (MCP) and skills
    pluginActions: PluginActions = PluginActions(),
    bridgeSyncSupported: Boolean = false,
) {
    var showSettings by remember { mutableStateOf(false) }
    var showLibrary by remember { mutableStateOf(false) }
    var openedAttachment by remember { mutableStateOf<LibraryItem?>(null) }
    var showChatMenu by remember { mutableStateOf(false) }
    var showChatSearch by remember { mutableStateOf(false) }
    var showSecrets by remember { mutableStateOf(false) }
    var showPlugins by remember { mutableStateOf(false) }
    var showBoard by remember { mutableStateOf(false) }
    var searching by remember { mutableStateOf(false) }
    var searchQuery by remember { mutableStateOf("") }
    // Меню действий над сообщением: индекс сообщения в state.messages.
    var actionFor by remember { mutableStateOf<Int?>(null) }
    var editingFor by remember { mutableStateOf<Int?>(null) }
    var selectingText by remember { mutableStateOf<String?>(null) }
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val haptics = androidx.compose.ui.platform.LocalHapticFeedback.current
    val clipboard = androidx.compose.ui.platform.LocalClipboardManager.current
    val speak = rememberSpeaker()
    val pickReqMultiple = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) onProvideFiles(uris) else onCancelFileReq()
    }
    val pickReqSingle = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) onProvideFiles(listOf(uri)) else onCancelFileReq()
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        // Свайп-жест открытия отключаем (иначе горизонтальное перетаскивание ползунков в
        // виджетах ИИ открывало шторку). Свайпом можно ЗАКРЫТЬ уже открытую; открывать — кнопкой ☰.
        gesturesEnabled = drawerState.isOpen,
        drawerContent = {
            DrawerContent(
                state = state,
                onNewChat = { scope.launch { drawerState.close() }; onNewChat() },
                onSwitchChat = { scope.launch { drawerState.close() }; onSwitchChat(it) },
                onOpenLibrary = { scope.launch { drawerState.close() }; showLibrary = true },
                onOpenSearch = { scope.launch { drawerState.close() }; showChatSearch = true },
                onOpenSecrets = { scope.launch { drawerState.close() }; showSecrets = true },
                onOpenPlugins = { scope.launch { drawerState.close() }; showPlugins = true },
                onOpenBoard = { scope.launch { drawerState.close() }; showBoard = true },
                onOpenSettings = { scope.launch { drawerState.close() }; showSettings = true },
            )
        },
    ) {
        // Borderless chat: no top bar, the buttons float over the content.
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
            // Live cosmos behind the empty chat. After the first message it does not cut to the plain
            // background: it fades out while drifting slightly forward, as if flying past the stars.
            val welcome = state.messages.isEmpty() && !searching
            val cosmos by animateFloatAsState(
                targetValue = if (welcome) 1f else 0f,
                animationSpec = tween(if (welcome) 900 else 1600, easing = FastOutSlowInEasing),
                label = "cosmos",
            )
            if (cosmos > 0.002f) {
                CosmosBackground(
                    dark = MaterialTheme.colorScheme.background.luminance() < 0.5f,
                    modifier = Modifier.fillMaxSize().graphicsLayer {
                        alpha = cosmos
                        val zoom = 1f + (1f - cosmos) * 0.12f
                        scaleX = zoom
                        scaleY = zoom
                    },
                )
            }
            Column(Modifier.fillMaxSize()) {
                val listState = rememberLazyListState()
                val topInset = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
                val topClear = topInset + 56.dp
                // #4: поиск по текущему чату — фильтруем сообщения (кэшируем, чтобы не гонять filter каждую рекомпозицию).
                val displayed = remember(state.messages, searching, searchQuery) {
                    if (searching && searchQuery.isNotBlank())
                        state.messages.filter { it.text.contains(searchQuery, ignoreCase = true) }
                    else state.messages
                }
                // Новое сообщение — плавно проматываем вниз.
                LaunchedEffect(state.messages.size) {
                    if (state.messages.isNotEmpty() && !searching) listState.animateScrollToItem(state.messages.size - 1)
                }
                // P3: во время стрима держим ленту прижатой к низу по мере роста последнего
                // пузыря — но только если пользователь и так у низа (не мешаем читать выше).
                val lastLen = state.messages.lastOrNull()?.text?.length ?: 0
                LaunchedEffect(lastLen) {
                    if (state.running && !searching && state.messages.isNotEmpty()) {
                        val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
                        // Прижимаем к НИЗУ последнего сообщения (большой offset → клэмп к концу),
                        // а не к его верху — иначе растущий ответ «прыгал» вверх (мигание).
                        if (lastVisible >= state.messages.size - 2) {
                            listState.scrollToItem(state.messages.size - 1, Int.MAX_VALUE)
                        }
                    }
                }
                if (searching) {
                    OutlinedTextField(
                        value = searchQuery, onValueChange = { searchQuery = it },
                        modifier = Modifier.fillMaxWidth().padding(top = topClear).padding(horizontal = 12.dp, vertical = 4.dp),
                        placeholder = { Text(stringResource(R.string.chat_search_here)) },
                        leadingIcon = { Icon(Icons.Rounded.Search, null) },
                        singleLine = true,
                    )
                }
                Crossfade(targetState = displayed.isEmpty(), modifier = Modifier.weight(1f), animationSpec = tween(700), label = "chat") { empty ->
                    if (empty) {
                        if (searching && searchQuery.isNotBlank()) {
                            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text(stringResource(R.string.nothing_found), color = MaterialTheme.colorScheme.outline)
                            }
                        } else WelcomeState(Modifier.fillMaxSize())
                    } else {
                        LazyColumn(
                            state = listState,
                            modifier = Modifier.fillMaxSize().padding(horizontal = Dims.screenPad),
                            verticalArrangement = Arrangement.spacedBy(Dims.messageGap),
                            contentPadding = PaddingValues(top = if (searching) 8.dp else topClear, bottom = 16.dp),
                        ) {
                            itemsIndexed(
                                displayed,
                                key = { _, it -> it.id },
                                contentType = { _, it -> msgContentType(it) },
                            ) { index, msg ->
                                val actionsEnabled = !(searching && searchQuery.isNotBlank())
                                Box(
                                    Modifier.animateItem().fillMaxWidth().combinedClickable(
                                        enabled = actionsEnabled,
                                        onClick = {},
                                        onLongClick = {
                                            if (actionsEnabled) {
                                                haptics.performHapticFeedback(
                                                    androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress,
                                                )
                                                actionFor = index
                                            }
                                        },
                                    ),
                                ) {
                                    Column(Modifier.fillMaxWidth()) {
                                        MessageBubble(
                                            msg,
                                            onOpenAttachment = { openedAttachment = it },
                                            onSwitchVersion = onSwitchVersion,
                                            showActions = index == displayed.lastIndex && !state.running,
                                            onRemix = onRemix,
                                            onCopy = { clipboard.setText(androidx.compose.ui.text.AnnotatedString(msg.text)) },
                                            onSpeak = { speak(msg.text) },
                                            onRegenerate = { onRegenerate(index) },
                                            // Статус («Думаю…», «Инструмент: …») — у активного ответа ИИ.
                                            statusText = if (state.running && index == displayed.lastIndex) state.status else "",
                                        )
                                        msg.reaction?.let { ReactionBadge(it, msg.fromUser) }
                                    }
                                }
                            }
                        }
                    }
                }

                if (state.suggestions.isNotEmpty() && !state.running) {
                    Row(
                        Modifier.fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(start = 12.dp, end = 12.dp, top = 2.dp, bottom = 6.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        state.suggestions.forEach { s -> QuickReplyChip(s) { onSend(s) } }
                    }
                }
                state.pendingQuote?.let { QuoteChip(it, onClear = onClearQuote) }
                InputBar(
                    running = state.running,
                    pendingImagePath = state.pendingImagePath,
                    pendingFileName = state.pendingFileName,
                    caps = state.activeCaps,
                    contextTokens = state.contextTokens,
                    onSend = onSend,
                    onClearAttachment = onClearAttachment,
                    onCamera = onAttachCamera,
                    onPickImage = onPickImageUri,
                    onPickFile = onPickFileUri,
                    onCancel = onCancelRun,
                    onSteer = onSteer,
                )
            }
            // Плавающие кнопки поверх безграничного чата — без шапки.
            Row(
                Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 6.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FloatIcon(Icons.Rounded.Menu, stringResource(R.string.menu)) { scope.launch { drawerState.open() } }
                if (PcBridgeFacade.SUPPORTED && state.pcUrl.isNotBlank()) {
                    Spacer(Modifier.width(6.dp))
                    PcPresenceChip(state.pcPresence)
                }
                Spacer(Modifier.weight(1f))
                FloatIcon(Icons.Rounded.Search, stringResource(R.string.search_in_chat)) { searching = !searching; if (!searching) searchQuery = "" }
                FloatIcon(Icons.Rounded.FolderOpen, stringResource(R.string.chat_menu)) { showChatMenu = true }
                FloatIcon(Icons.Rounded.EditNote, stringResource(R.string.nav_new_chat)) { onNewChat() }
            }
        }
    }

    if (showSettings || state.needsKey) {
        SettingsScreen(
            state = state,
            onTest = onTestBridge,
            onSaveTheme = onSaveTheme,
            onSelectAppIcon = onSelectAppIcon,
            onSetLanguage = onSetLanguage,
            onSaveUserProfile = onSaveUserProfile,
            onSelectModel = onSelectModel,
            onSaveModel = onSaveModel,
            onDeleteModel = onDeleteModel,
            onDismiss = { showSettings = false },
            onSaveBridge = onSaveBridge,
            onSetReactionsOnUser = onSetReactionsOnUser,
            onSetHaptics = onSetHaptics,
        )
    }
    if (showLibrary) LibraryScreen(state.libraryItems) { showLibrary = false }
    openedAttachment?.let { AttachmentViewer(it) { openedAttachment = null } }
    if (showChatMenu) {
        ChatMenuScreen(
            items = chatItemsProvider(),
            memory = chatMemoryProvider(),
            onSaveMemory = onSaveChatMemory,
            onOpenItem = { openedAttachment = it },
            onSummarize = { showChatMenu = false; onSummarize() },
            onDismiss = { showChatMenu = false },
        )
    }
    if (showChatSearch) {
        ChatSearchDialog(
            search = searchProvider,
            onOpenChat = { onSwitchChat(it); showChatSearch = false },
            onDismiss = { showChatSearch = false },
        )
    }

    // Меню действий над выбранным сообщением.
    actionFor?.let { idx ->
        val msg = state.messages.getOrNull(idx)
        if (msg == null) { actionFor = null; return@let }
        MessageActionsSheet(
            msg = msg,
            onCopy = { clipboard.setText(androidx.compose.ui.text.AnnotatedString(msg.text)) },
            onSpeak = { speak(msg.text) },
            onEdit = { editingFor = idx; actionFor = null },
            onRegenerate = { onRegenerate(idx) },
            onRevert = { onRevert(idx) },
            onBranch = { onBranch(idx) },
            onSelectText = { selectingText = msg.text; actionFor = null },
            onReact = { emoji -> onReact(msg.id, emoji) },
            onFormat = { mode -> onRemix(msg.id, mode) },
            onDismiss = { actionFor = null },
        )
    }
    editingFor?.let { idx ->
        val msg = state.messages.getOrNull(idx)
        if (msg == null) { editingFor = null; return@let }
        EditMessageDialog(
            initial = msg.text,
            onConfirm = { newText -> onEditMessage(idx, newText); editingFor = null },
            onDismiss = { editingFor = null },
        )
    }
    selectingText?.let { txt ->
        SelectionSheet(
            text = txt,
            onQuote = onQuote,
            onSaveChat = { onSaveSelection(it, false) },
            onSaveGlobal = { onSaveSelection(it, true) },
            onAsk = onAskSelection,
            onBoard = onAddToBoard,
            onDismiss = { selectingText = null },
        )
    }
    if (showBoard) {
        BoardSheet(
            items = state.board,
            onRemove = onRemoveFromBoard,
            onClear = { onClearBoard() },
            onCopyAll = {
                clipboard.setText(androidx.compose.ui.text.AnnotatedString(state.board.joinToString("\n") { "• $it" }))
            },
            onDismiss = { showBoard = false },
        )
    }
    state.pendingAsk?.let { req ->
        AskDialog(request = req, onSubmit = onSubmitAsk, onCancel = onCancelAsk)
    }
    state.pendingFileReq?.let { req ->
        FileRequestDialog(
            req = req,
            onPick = {
                val mimes = mimeArray(req.accept)
                if (req.multiple) pickReqMultiple.launch(mimes) else pickReqSingle.launch(mimes)
            },
            onCancel = onCancelFileReq,
        )
    }
    state.pendingSecretReq?.let { req ->
        SecretRequestDialog(req = req, onSubmit = onSubmitSecret, onCancel = onCancelSecret)
    }
    state.pendingSecretConfirm?.let { name ->
        SecretConfirmDialog(name = name, onAllow = { onConfirmSecret(true) }, onDeny = { onConfirmSecret(false) })
    }
    state.pendingApproval?.let { req ->
        ApprovalDialog(req = req, onDecision = onConfirmApproval)
    }
    state.pendingMemory?.let { sug ->
        MemorySuggestDialog(suggest = sug, onConfirm = onConfirmMemory)
    }
    if (showSecrets) {
        SecretsScreen(
            secrets = state.secrets,
            onAdd = onAddSecret,
            onRemove = onRemoveSecret,
            onSetAvailability = onSetSecretAvailability,
            onClose = { showSecrets = false },
        )
    }
    if (showPlugins) {
        PluginsScreen(
            servers = state.mcpServers,
            status = state.mcpStatus,
            notice = state.mcpNotice,
            busy = state.mcpBusy,
            skills = state.skills,
            skillReplaceAsk = state.skillReplaceAsk,
            syncSupported = bridgeSyncSupported,
            actions = pluginActions,
            onClose = { showPlugins = false },
        )
    }
}

/** accept-строка ИИ (image, application/pdf, csv…) → массив MIME для SAF. */
private fun mimeArray(accept: String): Array<String> {
    if (accept.isBlank() || accept.contains("*/*")) return arrayOf("*/*")
    val ext = mapOf(
        "csv" to "text/csv", "tsv" to "text/tab-separated-values", "txt" to "text/plain",
        "pdf" to "application/pdf", "json" to "application/json",
        "xlsx" to "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx" to "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image" to "image/*", "video" to "video/*", "audio" to "audio/*",
    )
    val out = accept.split(",").map { it.trim() }.filter { it.isNotEmpty() }.map { ext[it] ?: it }
    return if (out.isEmpty()) arrayOf("*/*") else out.toTypedArray()
}

/** #3 Меню чата: файлы/медиа этого чата + редактор памяти чата. */
@Composable
private fun ChatMenuScreen(
    items: List<LibraryItem>,
    memory: String,
    onSaveMemory: (String) -> Unit,
    onOpenItem: (LibraryItem) -> Unit,
    onSummarize: () -> Unit,
    onDismiss: () -> Unit,
) {
    var mem by remember { mutableStateOf(memory) }
    FullScreenScaffold(
        title = stringResource(R.string.chat_menu),
        onBack = onDismiss,
        actions = { TextButton(onClick = { onSaveMemory(mem); onDismiss() }) { Text(stringResource(R.string.action_save)) } },
    ) { pad ->
        SettingsScroll(pad) {
            SettingsGroup {
                Row(
                    Modifier.fillMaxWidth().clickable { onSummarize() }
                        .padding(horizontal = 16.dp, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Rounded.Summarize, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.width(16.dp))
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.chat_summary), style = MaterialTheme.typography.bodyLarge)
                        Text(stringResource(R.string.chat_summary_sub),
                            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Text(stringResource(R.string.files_media), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp))
            if (items.isEmpty()) {
                SettingsGroup {
                    Text(stringResource(R.string.files_media_empty),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(16.dp))
                }
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Fixed(4),
                    modifier = Modifier.fillMaxWidth().heightIn(max = 260.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    gridItems(items) { it -> ChatItemThumb(it) { onOpenItem(it) } }
                }
            }
            Text(stringResource(R.string.chat_memory), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp, top = 8.dp))
            Text(stringResource(R.string.chat_memory_hint),
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp))
            OutlinedTextField(
                value = mem, onValueChange = { mem = it },
                modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp),
                shape = RoundedCornerShape(16.dp),
                placeholder = { Text(stringResource(R.string.chat_memory_ph)) },
            )
        }
    }
}

@Composable
private fun ChatItemThumb(item: LibraryItem, onOpen: () -> Unit) {
    Box(
        Modifier.aspectRatio(1f).clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant).clickable { onOpen() },
        contentAlignment = Alignment.Center,
    ) {
        when (item.kind) {
            "image", "video" -> {
                val model: Any = if (item.path.startsWith("http")) item.path else java.io.File(item.path)
                AsyncImage(model, item.name, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                if (item.kind == "video") Icon(Icons.Rounded.PlayCircle, null, Modifier.size(22.dp), tint = Color.White)
            }
            "audio" -> Icon(Icons.Rounded.AudioFile, null, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.primary)
            else -> Icon(Icons.Rounded.Description, null, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.primary)
        }
    }
}

/** #5 Поиск по всем чатам. */
@Composable
private fun ChatSearchDialog(
    search: (String) -> List<com.localaiagent.app.ChatSearchHit>,
    onOpenChat: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    val results = remember(query) { if (query.isBlank()) emptyList() else search(query) }
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_close)) } },
        title = { Text(stringResource(R.string.search_all_chats)) },
        text = {
            Column {
                OutlinedTextField(
                    value = query, onValueChange = { query = it },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text(stringResource(R.string.search_word_ph)) },
                    leadingIcon = { Icon(Icons.Rounded.Search, null) }, singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                if (query.isNotBlank() && results.isEmpty()) {
                    Text(stringResource(R.string.nothing_found), color = MaterialTheme.colorScheme.outline)
                }
                LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(results) { hit ->
                        Column(
                            Modifier.fillMaxWidth().clickable { onOpenChat(hit.chatId) }
                                .padding(vertical = 8.dp),
                        ) {
                            Text(hit.title, fontSize = 14.sp, maxLines = 1, color = MaterialTheme.colorScheme.onBackground)
                            Text(hit.snippet, style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.outline, maxLines = 2)
                        }
                    }
                }
            }
        },
    )
}

// ---------------------------------------------------------------- drawer

@Composable
private fun DrawerContent(
    state: ChatUiState,
    onNewChat: () -> Unit,
    onSwitchChat: (String) -> Unit,
    onOpenLibrary: () -> Unit,
    onOpenSearch: () -> Unit,
    onOpenSecrets: () -> Unit,
    onOpenPlugins: () -> Unit,
    onOpenBoard: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    ModalDrawerSheet(
        modifier = Modifier.fillMaxWidth(0.86f),
        drawerContainerColor = MaterialTheme.colorScheme.background,
        drawerTonalElevation = 0.dp,
    ) {
        Column(Modifier.fillMaxHeight().statusBarsPadding().padding(horizontal = 8.dp)) {
            // Шапка: имя/название + круглая кнопка поиска (как в референсе).
            Row(
                Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    state.nickname.ifBlank { "Altair" },
                    style = MaterialTheme.typography.titleLarge, maxLines = 1,
                    modifier = Modifier.weight(1f),
                )
                DrawerCircleButton(Icons.Rounded.Search, stringResource(R.string.nav_search), onOpenSearch)
            }

            // Навигация по реальным разделам приложения.
            NavigationDrawerItem(
                label = { Text(stringResource(R.string.nav_library)) }, icon = { Icon(Icons.Rounded.PhotoLibrary, null) },
                selected = false, onClick = onOpenLibrary,
                badge = { if (state.libraryItems.isNotEmpty()) Text("${state.libraryItems.size}") },
            )
            NavigationDrawerItem(
                label = { Text(stringResource(R.string.nav_board)) }, icon = { Icon(Icons.Rounded.Dashboard, null) },
                selected = false, onClick = onOpenBoard,
                badge = { if (state.board.isNotEmpty()) Text("${state.board.size}") },
            )
            NavigationDrawerItem(
                label = { Text(stringResource(R.string.nav_secrets)) }, icon = { Icon(Icons.Rounded.Key, null) },
                selected = false, onClick = onOpenSecrets,
                badge = { if (state.secrets.isNotEmpty()) Text("${state.secrets.size}") },
            )
            NavigationDrawerItem(
                label = { Text(stringResource(R.string.nav_plugins)) }, icon = { Icon(Icons.Rounded.Extension, null) },
                selected = false, onClick = onOpenPlugins,
                badge = {
                    val n = state.mcpServers.size + state.skills.size
                    if (n > 0) Text("$n")
                },
            )

            Text(
                stringResource(R.string.nav_recent), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.outline,
                modifier = Modifier.padding(start = 16.dp, top = 14.dp, bottom = 4.dp),
            )
            LazyColumn(Modifier.weight(1f)) {
                items(state.chats) { c ->
                    NavigationDrawerItem(
                        label = { Text(c.title, maxLines = 1) },
                        selected = c.id == state.currentChatId,
                        onClick = { onSwitchChat(c.id) },
                    )
                }
            }

            // Нижняя панель: пилюля «Чат» (новый чат) + аватар-инициалы (настройки).
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    shape = RoundedCornerShape(50),
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.weight(1f).clickable(onClick = onNewChat),
                ) {
                    Row(
                        Modifier.padding(vertical = 12.dp),
                        horizontalArrangement = Arrangement.Center,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Rounded.EditNote, null, tint = MaterialTheme.colorScheme.onPrimary)
                        Spacer(Modifier.width(8.dp))
                        Text(
                            stringResource(R.string.nav_new_chat), color = MaterialTheme.colorScheme.onPrimary,
                            fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
                        )
                    }
                }
                Spacer(Modifier.width(10.dp))
                Surface(
                    shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    modifier = Modifier.size(46.dp).clickable(onClick = onOpenSettings),
                ) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            drawerInitials(state.nickname),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            }
        }
    }
}

/** Инициалы для аватара в шторке: до 2 букв ника, иначе «AI». */
private fun drawerInitials(nickname: String): String {
    val n = nickname.trim()
    if (n.isBlank()) return "AI"
    val parts = n.split(Regex("\\s+")).filter { it.isNotBlank() }
    return when {
        parts.size >= 2 -> (parts[0].take(1) + parts[1].take(1)).uppercase()
        else -> n.take(2).uppercase()
    }
}

/** Круглая иконка-кнопка в шапке шторки. */
@Composable
private fun DrawerCircleButton(icon: androidx.compose.ui.graphics.vector.ImageVector, desc: String, onClick: () -> Unit) {
    Surface(
        shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.size(40.dp).clickable(onClick = onClick),
    ) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Icon(icon, desc, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurface)
        }
    }
}

// ---------------------------------------------------------------- messages

@Composable
private fun MessageBubble(
    msg: ChatMessage,
    onOpenAttachment: (LibraryItem) -> Unit = {},
    onSwitchVersion: (String, Int) -> Unit = { _, _ -> },
    showActions: Boolean = false,
    onRemix: (String, String) -> Unit = { _, _ -> },
    onCopy: () -> Unit = {},
    onSpeak: () -> Unit = {},
    onRegenerate: () -> Unit = {},
    statusText: String = "",
) {
    when {
        // Встроенная графика/интерактив ИИ (SVG/HTML).
        msg.html != null -> Column(Modifier.fillMaxWidth()) {
            HtmlBlock(msg.html)
            if (msg.text.isNotBlank()) {
                Text(msg.text, Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline)
            }
        }
        msg.imageUrl != null -> Column(Modifier.fillMaxWidth()) {
            val model: Any = if (msg.imageUrl.startsWith("http")) msg.imageUrl else java.io.File(msg.imageUrl)
            AsyncImage(
                model = model, contentDescription = msg.text,
                modifier = Modifier.fillMaxWidth().heightIn(max = 360.dp).clip(RoundedCornerShape(16.dp))
                    .clickable { onOpenAttachment(LibraryItem(msg.imageUrl, msg.imageUrl.substringAfterLast('/'), "image")) },
                contentScale = ContentScale.Fit,
            )
            if (msg.text.isNotBlank()) {
                Text(msg.text, Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline)
            }
        }
        // Файл-вложение: полноценное превью (видео — кадр+play) или карточка-иконка.
        msg.attachPath != null -> Column(Modifier.fillMaxWidth(0.85f), horizontalAlignment = Alignment.End) {
            val item = LibraryItem(msg.attachPath, msg.attachName ?: stringResource(R.string.file_generic), msg.attachKind ?: "file")
            if (msg.attachKind == "video") {
                Box(
                    Modifier.fillMaxWidth().heightIn(max = 320.dp).clip(RoundedCornerShape(16.dp))
                        .clickable { onOpenAttachment(item) },
                    contentAlignment = Alignment.Center,
                ) {
                    AsyncImage(java.io.File(msg.attachPath), msg.attachName, Modifier.fillMaxWidth(), contentScale = ContentScale.Fit)
                    Icon(Icons.Rounded.PlayCircle, null, Modifier.size(52.dp), tint = Color.White.copy(alpha = 0.9f))
                }
            } else {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.clickable { onOpenAttachment(item) },
                ) {
                    Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            if (msg.attachKind == "audio") Icons.Rounded.AudioFile
                            else if (msg.attachName?.substringAfterLast('.', "")?.lowercase() in setOf("csv", "tsv", "xlsx")) Icons.Rounded.TableChart
                            else Icons.Rounded.Description,
                            null, Modifier.size(30.dp), tint = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(msg.attachName ?: stringResource(R.string.file_generic), style = MaterialTheme.typography.bodyLarge, maxLines = 1)
                    }
                }
            }
            if (msg.text.isNotBlank()) {
                Text(msg.text, Modifier.padding(top = 6.dp), color = MaterialTheme.colorScheme.onBackground)
            }
        }
        // Пользователь — акцентный пузырь (цвет выбирает юзер), справа.
        msg.fromUser -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Surface(
                color = MaterialTheme.colorScheme.primary,
                shape = RoundedCornerShape(Dims.rBubble, Dims.rBubble, Dims.sm, Dims.rBubble),
                modifier = Modifier.fillMaxWidth(0.85f).wrapContentWidth(Alignment.End),
            ) {
                Text(msg.text, Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                    color = MaterialTheme.colorScheme.onPrimary, style = MaterialTheme.typography.bodyLarge)
            }
        }
        // Ассистент — во весь экран, без рамок, крупным текстом.
        else -> Column(Modifier.fillMaxWidth()) {
            msg.replyQuote?.let { ReplyQuoteHeader(it) }
            if (msg.text.isBlank()) {
                // Ответ ещё не начался — пульсирующий индикатор акцентного цвета + статус
                // ровно там, где появится ответ (а не над строкой ввода). Показываем ТОЛЬКО
                // пока идёт прогон (statusText непустой) — иначе пустой хвостовой пузырь
                // (напр. после ответа-картинки) не «мигает» вечно.
                if (statusText.isNotBlank()) ThinkingIndicator(statusText)
            } else {
                MarkdownText(msg.text, Modifier.fillMaxWidth())
                // Показываем полный индикатор под текстом ТОЛЬКО во время инструмента (напр.
                // «Инструмент: run_python»). Для обычного стрима токенов — тонкая каретка-искра
                // в конце (пока прогон идёт, т.е. statusText непустой), чтобы было видно «печатает».
                if (statusText.startsWith(stringResource(R.string.tool_label))) {
                    Spacer(Modifier.height(4.dp))
                    ThinkingIndicator(statusText)
                } else if (statusText.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    StreamingCaret()
                }
            }
            if (msg.versions.size > 1) {
                VersionSwitcher(
                    index = msg.verIndex, total = msg.versions.size,
                    onPrev = { onSwitchVersion(msg.id, -1) }, onNext = { onSwitchVersion(msg.id, 1) },
                )
            }
            if (showActions && msg.text.isNotBlank()) {
                AnswerActionBar(
                    onCopy = onCopy, onSpeak = onSpeak, onRegenerate = onRegenerate,
                    onRemix = { mode -> onRemix(msg.id, mode) },
                )
            }
        }
    }
}

/** Бейдж эмодзи-реакции под сообщением (справа у юзера, слева у ИИ). */
@Composable
private fun ReactionBadge(emoji: String, fromUser: Boolean) {
    Row(
        Modifier.fillMaxWidth().padding(top = 4.dp),
        horizontalArrangement = if (fromUser) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
        ) {
            Text(emoji, fontSize = 15.sp, modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
        }
    }
}

/**
 * Аккуратная панель действий под ответом ИИ: копировать, озвучить, и «перегенерировать»
 * с выпадающим вверх меню (простая перегенерация + варианты короче/длиннее/проще/формальнее).
 * Варианты больше не висят все сразу — прячутся в меню.
 */
@Composable
private fun AnswerActionBar(
    onCopy: () -> Unit,
    onSpeak: () -> Unit,
    onRegenerate: () -> Unit,
    onRemix: (String) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    val remix = listOf(
        "shorter" to stringResource(R.string.remix_shorter), "longer" to stringResource(R.string.remix_longer),
        "simpler" to stringResource(R.string.sel_simpler), "formal" to stringResource(R.string.remix_formal),
    )
    Row(
        Modifier.fillMaxWidth().padding(top = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ActionIcon(Icons.Rounded.ContentCopy, stringResource(R.string.act_copy), onCopy)
        ActionIcon(Icons.AutoMirrored.Rounded.VolumeUp, stringResource(R.string.act_speak), onSpeak)
        Box {
            ActionIcon(Icons.Rounded.Autorenew, stringResource(R.string.act_regenerate)) { menuOpen = true }
            DropdownMenu(
                expanded = menuOpen,
                onDismissRequest = { menuOpen = false },
                offset = DpOffset(0.dp, (-8).dp), // смещаем вверх от кнопки
            ) {
                DropdownMenuItem(
                    text = { Text(stringResource(R.string.act_regenerate)) },
                    leadingIcon = { Icon(Icons.Rounded.Autorenew, null) },
                    onClick = { menuOpen = false; onRegenerate() },
                )
                HorizontalDivider()
                remix.forEach { (mode, label) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = { menuOpen = false; onRemix(mode) },
                    )
                }
            }
        }
    }
}

/** Мелкая приглушённая иконка-кнопка действия под ответом. */
@Composable
private fun ActionIcon(icon: ImageVector, desc: String, onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.size(36.dp)) {
        Icon(icon, desc, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Предлагаемый быстрый ответ над строкой ввода — лёгкая контурная «пилюля». */
@Composable
private fun QuickReplyChip(text: String, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(50),
        color = Color.Transparent,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = Modifier.clickable(onClick = onClick),
    ) {
        Row(
            Modifier.padding(start = 10.dp, end = 14.dp, top = 7.dp, bottom = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.AutoMirrored.Rounded.Reply, null,
                Modifier.size(14.dp), tint = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.width(6.dp))
            Text(
                text, style = MaterialTheme.typography.labelMedium, maxLines = 1,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

/** Переключатель версий ответа ИИ: ‹ 2/3 › (появляется после регенерации). */
@Composable
private fun VersionSwitcher(index: Int, total: Int, onPrev: () -> Unit, onNext: () -> Unit) {
    Row(
        Modifier.padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onPrev, enabled = index > 0, modifier = Modifier.size(28.dp)) {
            Icon(Icons.Rounded.ChevronLeft, stringResource(R.string.version_prev), Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("${index + 1}/$total", style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        IconButton(onClick = onNext, enabled = index < total - 1, modifier = Modifier.size(28.dp)) {
            Icon(Icons.Rounded.ChevronRight, stringResource(R.string.version_next), Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Цитата-ответ ИИ на конкретный фрагмент сообщения пользователя. */
@Composable
private fun ReplyQuoteHeader(quote: String) {
    Row(
        Modifier.fillMaxWidth().padding(bottom = 6.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            Modifier.width(3.dp).heightIn(min = 18.dp)
                .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(2.dp)),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            quote, fontSize = 13.sp, maxLines = 3,
            color = MaterialTheme.colorScheme.outline,
        )
    }
}

/**
 * Рендер SVG/HTML-графики и интерактива ИИ в WebView. Ведёт себя как «рантайм виджетов»:
 *  • инжектит тему приложения (CSS-переменные + нативные стили кнопок/полей/таблиц),
 *    чтобы виджет выглядел частью приложения, а не белым прямоугольником;
 *  • авто-подгоняет высоту под контент (нет внутренней прокрутки/обрезки);
 *  • перезагружает страницу ТОЛЬКО при смене html (не сбрасывает состояние виджета
 *    на каждой рекомпозиции — иначе калькулятор «забывал» ввод).
 */
@Composable
private fun HtmlBlock(html: String) {
    val cs = MaterialTheme.colorScheme
    fun cssColor(c: Color): String = "#%06X".format(0xFFFFFF and c.toArgb())
    val scheme = if (cs.background.luminance() < 0.5f) "dark" else "light"
    // canvas/анимация: известный баг Android WebView — прозрачный фон + аппаратный canvas
    // рисуется чёрным. Лечим НЕПРОЗРАЧНЫМ фоном + программным слоем рендеринга.
    val hasCanvas = remember(html) {
        val h = html.lowercase(); "<canvas" in h || "requestanimationframe" in h
    }
    // Настоящая полноэкранная игра (100vh) — нужна фикс-высота вьюпорта; фикс-canvas-виджеты
    // (как калькулятор) прекрасно измеряются авто-высотой.
    val fixedHeight = remember(html) { "100vh" in html.lowercase() }
    val doc = remember(html, scheme, cs.primary, fixedHeight) {
        wrapThemedHtml(
            inner = html,
            fg = cssColor(cs.onBackground), muted = cssColor(cs.onSurfaceVariant),
            surface = cssColor(cs.surfaceContainerHigh), border = cssColor(cs.outlineVariant),
            accent = cssColor(cs.primary), onAccent = cssColor(cs.onPrimary), scheme = scheme,
            fullBleed = fixedHeight,
        )
    }
    var heightDp by remember { mutableStateOf(0.dp) }
    val webViewBg = if (hasCanvas) cs.surfaceContainerHigh.toArgb() else android.graphics.Color.TRANSPARENT
    androidx.compose.ui.viewinterop.AndroidView(
        factory = { ctx ->
            android.webkit.WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                // Widget HTML is model-generated: no access to app files / content providers
                // (minSdk 26 defaults allowFileAccess to true).
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                // Never navigate the widget itself (phishing inside the app) — hand links to the browser.
                webViewClient = object : android.webkit.WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        view: android.webkit.WebView,
                        request: android.webkit.WebResourceRequest,
                    ): Boolean {
                        val uri = request.url
                        if (uri.scheme == "http" || uri.scheme == "https") {
                            runCatching {
                                view.context.startActivity(
                                    android.content.Intent(android.content.Intent.ACTION_VIEW, uri)
                                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                                )
                            }
                        }
                        return true
                    }
                }
                isVerticalScrollBarEnabled = false
                overScrollMode = android.view.View.OVER_SCROLL_NEVER
                // Мост только для авто-высоты — ничего чувствительного не отдаём.
                addJavascriptInterface(object {
                    @android.webkit.JavascriptInterface
                    fun setHeight(px: Int) {
                        // scrollHeight приходит в CSS-px; при viewport width=device-width это
                        // те же dp, поэтому конвертировать через density НЕ нужно (иначе высота
                        // делилась на плотность и виджет обрезался).
                        post { heightDp = px.dp.coerceIn(0.dp, 4000.dp) }
                    }
                }, "AgentBridge")
            }
        },
        update = { wv ->
            wv.setBackgroundColor(webViewBg)
            // Программный слой для canvas — снимает баг чёрного аппаратного рендера.
            wv.setLayerType(
                if (hasCanvas) android.view.View.LAYER_TYPE_SOFTWARE else android.view.View.LAYER_TYPE_HARDWARE,
                null,
            )
            if (wv.tag != doc) {
                wv.tag = doc
                heightDp = 0.dp
                wv.loadDataWithBaseURL(null, doc, "text/html", "utf-8", null)
            }
        },
        modifier = Modifier.fillMaxWidth()
            .then(
                when {
                    fixedHeight -> Modifier.height(460.dp) // полноэкранная игра (100vh)
                    heightDp > 0.dp -> Modifier.height(heightDp)
                    else -> Modifier.heightIn(min = 48.dp)
                },
            )
            .clip(RoundedCornerShape(14.dp)),
    )
}

/**
 * Оборачивает html ИИ в тему приложения: CSS-переменные + базовые стили + скрипт
 * авто-высоты. Работает и с полным документом (инжектит в head), и с фрагментом.
 */
private fun wrapThemedHtml(
    inner: String,
    fg: String, muted: String, surface: String, border: String,
    accent: String, onAccent: String, scheme: String,
    fullBleed: Boolean = false,
): String {
    // Игровой/анимационный виджет: реальная высота вьюпорта (100%/100vh работают), без auto.
    // Обычный виджет (калькулятор, таблица): высота по контенту, overflow видим.
    val sizing = if (fullBleed)
        "height:100%;width:100%;overflow:hidden"
    else
        "height:auto!important;min-height:0!important;max-height:none!important;overflow:visible!important"
    val head = """
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
:root{color-scheme:$scheme;--bg:transparent;--fg:$fg;--muted:$muted;--surface:$surface;--border:$border;--accent:$accent;--on-accent:$onAccent;--radius:12px}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent;color:var(--fg);font-family:-apple-system,Roboto,'Segoe UI',system-ui,sans-serif;font-size:16px;line-height:1.5;$sizing}
canvas{display:block;max-width:100%}
body{padding:${if (fullBleed) "0" else "2px"}}
svg{max-width:100%;height:auto;display:block}
h1,h2,h3{margin:.2em 0 .4em;line-height:1.25}
button{background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--radius);padding:10px 16px;font-size:15px;font-weight:600;cursor:pointer}
button:active{filter:brightness(.9)}
input,select,textarea{background:var(--surface);color:var(--fg);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:15px;width:100%;outline:none}
input[type=range]{padding:0;accent-color:var(--accent)}
input[type=checkbox],input[type=radio]{width:auto;accent-color:var(--accent)}
label{color:var(--muted);font-size:13px;display:block;margin-bottom:4px}
table{border-collapse:collapse;width:100%}th,td{border:1px solid var(--border);padding:8px 10px;text-align:left}
a{color:var(--accent)}
.card,fieldset{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin:0 0 10px}
.result{font-size:22px;font-weight:700;color:var(--accent)}
</style>
"""
    val autoHeight = """
<script>
(function(){
  function h(){return Math.max(document.body?document.body.scrollHeight:0,document.documentElement.scrollHeight,document.body?document.body.offsetHeight:0)}
  function r(){try{AgentBridge.setHeight(h())}catch(e){}}
  window.addEventListener('load',r);window.addEventListener('resize',r);
  document.addEventListener('input',function(){setTimeout(r,30)},true);
  document.addEventListener('click',function(){setTimeout(r,30)},true);
  // ResizeObserver ловит любые изменения размера (переток текста, показ результата) надёжнее.
  try{new ResizeObserver(r).observe(document.documentElement);if(document.body)new ResizeObserver(r).observe(document.body)}catch(e){}
  try{new MutationObserver(function(){setTimeout(r,30)}).observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true})}catch(e){}
  [60,200,400,800,1500].forEach(function(t){setTimeout(r,t)});
})();
</script>
"""
    val hasHead = inner.contains("</head>", ignoreCase = true)
    val hasBody = inner.contains("</body>", ignoreCase = true)
    return when {
        hasHead -> {
            var out = inner.replaceFirst(Regex("</head>", RegexOption.IGNORE_CASE), "$head</head>")
            out = if (hasBody) out.replaceFirst(Regex("</body>", RegexOption.IGNORE_CASE), "$autoHeight</body>")
            else out + autoHeight
            out
        }
        else -> "<!doctype html><html><head>$head</head><body>$inner$autoHeight</body></html>"
    }
}

/** Плавающая кнопка поверх контента — круглый лёгкий скрим для читаемости без шапки. */
@Composable
private fun FloatIcon(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    desc: String,
    onClick: () -> Unit,
) {
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.6f),
        modifier = Modifier.padding(2.dp),
    ) {
        IconButton(onClick = onClick) {
            Icon(icon, desc, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * Плашка среды: живое присутствие ПК по мосту. Зелёный — свободен, оранжевый
 * (пульсирует) — идёт делегирование, серый — оффлайн. Снимает тревогу «дойдёт ли».
 */
@Composable
private fun PcPresenceChip(presence: com.localaiagent.app.PcPresence) {
    val (dot, label) = when (presence) {
        com.localaiagent.app.PcPresence.ONLINE -> Semantic.success to stringResource(R.string.pc_label)
        com.localaiagent.app.PcPresence.BUSY -> Semantic.warning to stringResource(R.string.pc_busy)
        com.localaiagent.app.PcPresence.OFFLINE -> MaterialTheme.colorScheme.outline to stringResource(R.string.pc_offline)
    }
    Surface(
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.6f),
        modifier = Modifier.padding(2.dp),
    ) {
        Row(
            Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val busy = presence == com.localaiagent.app.PcPresence.BUSY
            val alpha by if (busy) {
                rememberInfiniteTransition(label = "pc").animateFloat(
                    initialValue = 1f, targetValue = 0.3f,
                    animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "pca",
                )
            } else {
                remember { mutableStateOf(1f) }
            }
            Box(Modifier.size(8.dp).clip(CircleShape).background(dot.copy(alpha = alpha)))
            Spacer(Modifier.width(6.dp))
            Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Путь 4-конечной звезды-искры Altair (как на иконке), центр (cx,cy), лучи r / впадины inner. */
private fun DrawScope.starPath(cx: Float, cy: Float, r: Float, inner: Float): Path {
    val p = Path()
    for (k in 0 until 8) {
        val a = Math.toRadians((-90 + k * 45).toDouble())
        val rad = if (k % 2 == 0) r else inner
        val x = cx + rad * kotlin.math.cos(a).toFloat()
        val y = cy + rad * kotlin.math.sin(a).toFloat()
        if (k == 0) p.moveTo(x, y) else p.lineTo(x, y)
    }
    p.close()
    return p
}

/** Мерцающая золотая звезда Altair — «дыхание» перед/во время ответа ИИ (замена PulsingDot). */
@Composable
private fun StarPulse(size: Dp = 13.dp) {
    val t = rememberInfiniteTransition(label = "star")
    val twinkle by t.animateFloat(
        initialValue = 1f, targetValue = 0.5f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "tw",
    )
    val scale by t.animateFloat(
        initialValue = 1f, targetValue = 0.72f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "sc",
    )
    Canvas(Modifier.size(size)) {
        val cx = this.size.width / 2f
        val cy = this.size.height / 2f
        val r = (this.size.minDimension / 2f) * scale
        // мягкое золотое свечение
        drawCircle(Brand.glow.copy(alpha = 0.30f * twinkle), radius = r * 1.2f, center = Offset(cx, cy))
        // тело звезды — вертикальный золотой градиент
        drawPath(
            starPath(cx, cy, r, r * 0.30f),
            brush = Brush.verticalGradient(
                listOf(Brand.goldTop, Brand.goldMid, Brand.goldLow),
                startY = cy - r, endY = cy + r,
            ),
            alpha = twinkle.coerceIn(0f, 1f),
        )
    }
}

/** Каретка-искра: мерцающая золотая звёздочка в конце растущего текста во время стрима. */
@Composable
private fun StreamingCaret() {
    val t = rememberInfiniteTransition(label = "caret")
    val a by t.animateFloat(
        initialValue = 1f, targetValue = 0.15f,
        animationSpec = infiniteRepeatable(tween(650), RepeatMode.Reverse), label = "ca",
    )
    Canvas(Modifier.size(11.dp)) {
        val cx = this.size.width / 2f
        val cy = this.size.height / 2f
        val r = this.size.minDimension / 2f
        drawCircle(Brand.glow.copy(alpha = 0.28f * a), radius = r, center = Offset(cx, cy))
        drawPath(
            starPath(cx, cy, r * 0.9f, r * 0.28f),
            brush = Brush.verticalGradient(
                listOf(Brand.goldTop, Brand.goldMid, Brand.goldLow),
                startY = cy - r, endY = cy + r,
            ),
            alpha = a.coerceIn(0f, 1f),
        )
    }
}

/** Статичная золотая звезда Altair (герой пустого экрана): свечение + градиентное тело. */
@Composable
private fun AltairStar(size: Dp) {
    Canvas(Modifier.size(size)) {
        val cx = this.size.width / 2f
        val cy = this.size.height / 2f
        val r = this.size.minDimension / 2f * 0.92f
        drawCircle(Brand.glow.copy(alpha = 0.22f), radius = r * 1.25f, center = Offset(cx, cy))
        drawCircle(Brand.glow.copy(alpha = 0.16f), radius = r * 0.9f, center = Offset(cx, cy))
        drawPath(
            starPath(cx, cy, r, r * 0.30f),
            brush = Brush.verticalGradient(
                listOf(Brand.goldTop, Brand.goldMid, Brand.goldLow),
                startY = cy - r, endY = cy + r,
            ),
        )
        // маленькая звезда-спутник справа-сверху (как на иконке)
        val sx = cx + r * 0.62f
        val sy = cy - r * 0.66f
        drawPath(
            starPath(sx, sy, r * 0.26f, r * 0.07f),
            brush = Brush.verticalGradient(
                listOf(Brand.goldTop, Brand.goldMid),
                startY = sy - r * 0.26f, endY = sy + r * 0.26f,
            ),
        )
    }
}

/** Бурст золотых искр (успех/отправка): по смене [trigger] звёздочки разлетаются и гаснут. */
@Composable
private fun StarBurst(trigger: Int, modifier: Modifier = Modifier) {
    val progress = remember { Animatable(0f) }
    LaunchedEffect(trigger) {
        if (trigger > 0) {
            progress.snapTo(0f)
            progress.animateTo(1f, tween(560))
        }
    }
    val p = progress.value
    if (p > 0.001f && p < 0.999f) {
        Canvas(modifier) {
            val n = 8
            val cx = size.width / 2f
            val cy = size.height / 2f
            val maxR = size.minDimension * 1.5f // искры вылетают за пределы кнопки (Canvas не обрезает)
            val eased = 1f - (1f - p) * (1f - p) // easeOutQuad
            for (i in 0 until n) {
                val a = Math.toRadians((i * 360.0 / n) - 90.0)
                val dist = maxR * eased
                val x = cx + dist * kotlin.math.cos(a).toFloat()
                val y = cy + dist * kotlin.math.sin(a).toFloat()
                val r = (size.minDimension * 0.07f) * (1f - p * 0.6f)
                drawPath(
                    starPath(x, y, r, r * 0.35f),
                    brush = Brush.verticalGradient(
                        listOf(Brand.goldTop, Brand.goldMid, Brand.goldLow),
                        startY = y - r, endY = y + r,
                    ),
                    alpha = (1f - p).coerceIn(0f, 1f),
                )
            }
        }
    }
}

/** Тонкое созвездие для фона пустого экрана: редкие звёзды + едва заметные линии-грани. */
@Composable
private fun ConstellationBackground(modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "constel")
    val tw by t.animateFloat(
        initialValue = 0.5f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(2600), RepeatMode.Reverse), label = "ctw",
    )
    val line = MaterialTheme.colorScheme.onSurfaceVariant
    Canvas(modifier) {
        val w = size.width
        val h = size.height
        // нормированные точки созвездия (условный «Орёл»/Альтаир — декоративно)
        val pts = listOf(
            0.22f to 0.30f, 0.38f to 0.20f, 0.52f to 0.34f,
            0.68f to 0.24f, 0.80f to 0.40f, 0.60f to 0.52f, 0.34f to 0.50f,
        ).map { Offset(it.first * w, it.second * h) }
        // грани
        val edges = listOf(0 to 1, 1 to 2, 2 to 3, 3 to 4, 2 to 5, 5 to 6, 6 to 0)
        edges.forEach { (a, b) ->
            drawLine(line.copy(alpha = 0.10f * tw), pts[a], pts[b], strokeWidth = 1f)
        }
        pts.forEachIndexed { i, pt ->
            val r = if (i % 2 == 0) 2.4f else 1.6f
            drawCircle(Brand.glow.copy(alpha = 0.35f * tw), radius = r * 2.2f, center = pt)
            drawCircle(Brand.goldTop.copy(alpha = 0.7f * tw), radius = r, center = pt)
        }
    }
}

/** Индикатор «ИИ думает/работает»: мерцающая звезда Altair + статус, там где начнётся ответ. */
@Composable
private fun ThinkingIndicator(status: String) {
    Row(
        Modifier.padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        StarPulse()
        if (status.isNotBlank()) {
            Spacer(Modifier.width(8.dp))
            Text(
                status,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** Тип элемента ленты для `contentType` LazyColumn — чтобы Compose переиспользовал слоты по типу. */
private fun msgContentType(msg: ChatMessage): String = when {
    msg.html != null -> "html"
    msg.imageUrl != null -> "image"
    msg.attachPath != null -> "file"
    msg.fromUser -> "user"
    else -> "assistant"
}

// ---------------------------------------------------------------- mascot "Alti"

/** Alti's star outline (the cubic Béziers from mascot.js, 24×24 grid); center (cx, cy), R to a tip. */
private fun altiPath(cx: Float, cy: Float, R: Float): Path {
    val s = R / 11.4f
    fun px(x: Float) = cx + (x - 12f) * s
    fun py(y: Float) = cy + (y - 12f) * s
    return Path().apply {
        moveTo(px(12f), py(0.6f))
        cubicTo(px(13.2f), py(7.7f), px(16.3f), py(10.8f), px(23.4f), py(12f))
        cubicTo(px(16.3f), py(13.2f), px(13.2f), py(16.3f), px(12f), py(23.4f))
        cubicTo(px(10.8f), py(16.3f), px(7.7f), py(13.2f), px(0.6f), py(12f))
        cubicTo(px(7.7f), py(10.8f), px(10.8f), py(7.7f), px(12f), py(0.6f))
        close()
    }
}

/** The star body: a volumetric radial gradient, shading at the bottom, gloss and a rim (mascot.js spec). */
private fun DrawScope.drawAltiBody(cx: Float, cy: Float, R: Float) {
    val path = altiPath(cx, cy, R)
    drawPath(
        path,
        Brush.radialGradient(
            0f to Color(0xFFFFF6DA), 0.42f to Color(0xFFFFD37A), 0.80f to Color(0xFFF1A93C), 1f to Color(0xFFD6811E),
            center = Offset(cx - 0.28f * R, cy - 0.40f * R), radius = 1.7f * R,
        ),
    )
    // darker toward the bottom for volume
    drawPath(
        path,
        Brush.verticalGradient(0f to Color.Transparent, 0.55f to Color.Transparent, 1f to Color(0x6B5A2D08), startY = cy - R, endY = cy + R),
    )
    // glossy highlight at the upper left, inside the outline
    clipPath(path) {
        drawCircle(
            Brush.radialGradient(listOf(Color.White.copy(alpha = 0.5f), Color.Transparent),
                center = Offset(cx - 0.30f * R, cy - 0.45f * R), radius = 0.95f * R),
            radius = 0.95f * R, center = Offset(cx - 0.30f * R, cy - 0.45f * R),
        )
    }
    // rim outline
    drawPath(path, Brush.verticalGradient(listOf(Color(0xFFFFE9A8), Color(0xFFC9761A)), startY = cy - R, endY = cy + R),
        style = Stroke(width = R * 0.05f), alpha = 0.55f)
}

/**
 * Alti: the main star, two satellites (the three bodies) and a minimal face, animated like on the PC
 * (redesign.layout.css): the core floats with a slight sway, its contact shadow shrinks as it rises,
 * the satellites bob on their own rhythms, and the whole mascot pops in on appearance.
 */
@Composable
private fun AltiMascot(size: Dp, satellites: Boolean = true) {
    val t = rememberInfiniteTransition(label = "alti")
    val ease = androidx.compose.animation.core.CubicBezierEasing(0.45f, 0f, 0.55f, 1f)
    fun spec(ms: Int) = infiniteRepeatable<Float>(tween(ms / 2, easing = ease), RepeatMode.Reverse)
    val float by t.animateFloat(0f, 1f, spec(4500), label = "float")
    val bobA by t.animateFloat(0f, 1f, spec(5500), label = "bobA")
    val bobB by t.animateFloat(0f, 1f, spec(6800), label = "bobB")
    val glow by t.animateFloat(0.12f, 0.20f, infiniteRepeatable(tween(2200), RepeatMode.Reverse), label = "gl")
    val appear = remember { Animatable(0f) }
    LaunchedEffect(Unit) { appear.animateTo(1f, tween(600, easing = androidx.compose.animation.core.CubicBezierEasing(0.2f, 0.8f, 0.2f, 1f))) }

    Canvas(Modifier.size(size).graphicsLayer {
        alpha = appear.value
        val sc = 0.82f + 0.18f * appear.value
        scaleX = sc
        scaleY = sc
    }) {
        val w = this.size.width
        val cx = w / 2f
        val baseCy = w / 2f
        val R = w * 0.33f
        val s = R / 11.4f
        // Rise by 5% of the star with a ±1.5° sway, as in the PC keyframes.
        val lift = -0.05f * (24f * s) * float
        val sway = -1.5f + 3f * float
        val cy = baseCy + lift

        // contact shadow: narrower and fainter while the star is up
        val shW = 6.2f * s * (1f - 0.14f * float)
        drawOval(
            Color.Black.copy(alpha = 0.28f - 0.10f * float),
            topLeft = Offset(cx - shW, baseCy + 10.4f * s - 1.15f * s), size = Size(shW * 2, 2.3f * s),
        )
        drawCircle(
            Brush.radialGradient(listOf(Brand.glow.copy(alpha = glow), Color.Transparent), center = Offset(cx, cy), radius = R * 2f),
            radius = R * 2f, center = Offset(cx, cy),
        )
        if (satellites) {
            val aY = -0.08f * (0.4f * 24f * s) * bobA
            drawAltiBody(cx + (20.6f - 12f) * s, baseCy + (4.4f - 12f) * s + aY, R * 0.40f * (1f + 0.07f * bobA))
            val bY = 0.08f * (0.28f * 24f * s) * bobB
            drawAltiBody(cx + (3.6f - 12f) * s, baseCy + (19.2f - 12f) * s + bY, R * 0.28f * (1f - 0.08f * bobB))
        }
        rotate(sway, pivot = Offset(cx, cy)) {
            drawAltiBody(cx, cy, R)
            val eye = Color(0xFF241608)
            val ew = 1.5f * s
            val eh = 3.0f * s
            val er = 0.75f * s
            val ey = cy + (12.4f - 12f) * s
            val ex1 = cx + (9.7f - 12f) * s
            val ex2 = cx + (14.3f - 12f) * s
            drawRoundRect(eye, topLeft = Offset(ex1 - ew / 2, ey - eh / 2), size = Size(ew, eh), cornerRadius = CornerRadius(er, er))
            drawRoundRect(eye, topLeft = Offset(ex2 - ew / 2, ey - eh / 2), size = Size(ew, eh), cornerRadius = CornerRadius(er, er))
            drawCircle(Color.White.copy(alpha = 0.9f), radius = 0.42f * s, center = Offset(ex1 + 0.45f * s, ey - eh / 2 + 0.55f * s))
            drawCircle(Color.White.copy(alpha = 0.9f), radius = 0.42f * s, center = Offset(ex2 + 0.45f * s, ey - eh / 2 + 0.55f * s))
        }
    }
}

@Composable
private fun WelcomeState(modifier: Modifier = Modifier) {
    Box(modifier, contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            AltiMascot(size = 118.dp)
            Text(stringResource(R.string.welcome_title), style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.onBackground, modifier = Modifier.padding(top = 12.dp))
            Text(
                if (PcBridgeFacade.SUPPORTED) stringResource(R.string.welcome_subtitle_bridge)
                else stringResource(R.string.welcome_subtitle),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.outline,
                textAlign = TextAlign.Center, modifier = Modifier.padding(top = 8.dp, start = 24.dp, end = 24.dp),
            )
        }
    }
}

// ---------------------------------------------------------------- input bar

/** Премиальное меню вложений: крупные иконки-чипы, гейтятся возможностями модели. */
@Composable
private fun AttachMenu(
    expanded: Boolean,
    caps: Set<String>,
    onCamera: () -> Unit,
    onPhoto: () -> Unit,
    onDraw: () -> Unit,
    onVideo: () -> Unit,
    onAudio: () -> Unit,
    onFile: () -> Unit,
    onDismiss: () -> Unit,
) {
    DropdownMenu(
        expanded = expanded,
        onDismissRequest = onDismiss,
        offset = DpOffset(0.dp, (-8).dp), // всплывает над «+»
        shape = RoundedCornerShape(22.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainer,
        modifier = Modifier.width(240.dp),
    ) {
        if (caps.isEmpty()) {
            Text(
                stringResource(R.string.attach_enable_hint),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            )
        }
        if ("image" in caps) {
            AttachRow(Icons.Rounded.PhotoCamera, stringResource(R.string.attach_camera)) { onCamera(); onDismiss() }
            AttachRow(Icons.Rounded.Image, stringResource(R.string.attach_photo)) { onPhoto(); onDismiss() }
            AttachRow(Icons.Rounded.Draw, stringResource(R.string.attach_draw)) { onDraw(); onDismiss() }
        }
        if ("video" in caps) AttachRow(Icons.Rounded.Videocam, stringResource(R.string.attach_video)) { onVideo(); onDismiss() }
        if ("audio" in caps) AttachRow(Icons.Rounded.AudioFile, stringResource(R.string.attach_audio)) { onAudio(); onDismiss() }
        if ("file" in caps) AttachRow(Icons.Rounded.AttachFile, stringResource(R.string.attach_files)) { onFile(); onDismiss() }
    }
}

@Composable
private fun AttachRow(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHighest, modifier = Modifier.size(40.dp)) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Icon(icon, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurface)
            }
        }
        Spacer(Modifier.width(14.dp))
        Text(
            label, style = MaterialTheme.typography.bodyLarge,
            fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
private fun InputBar(
    running: Boolean,
    pendingImagePath: String?,
    pendingFileName: String?,
    caps: Set<String>,
    contextTokens: Int,
    onSend: (String) -> Unit,
    onClearAttachment: () -> Unit,
    onCancel: () -> Unit,
    onSteer: (String) -> Unit,
    onCamera: (Bitmap) -> Unit,
    onPickImage: (android.net.Uri) -> Unit,
    onPickFile: (android.net.Uri) -> Unit,
) {
    var input by remember { mutableStateOf("") }
    var attachSheet by remember { mutableStateOf(false) }
    var drawing by remember { mutableStateOf(false) }
    val cameraLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicturePreview(),
    ) { bmp -> if (bmp != null) onCamera(bmp) }
    val galleryLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri -> if (uri != null) onPickImage(uri) }
    val fileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> if (uri != null) onPickFile(uri) }


    if (drawing) {
        DrawCanvas(
            onDone = { bmp -> onCamera(bmp); drawing = false },
            onDismiss = { drawing = false },
        )
    }

    // Поднимаем строку ровно на высоту клавиатуры (ime), а без клавиатуры — держим над
    // навигационной панелью. union берёт максимум по стороне → без двойного смещения.
    Column(
        Modifier
            .windowInsetsPadding(WindowInsets.ime.union(WindowInsets.navigationBars))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        if (pendingImagePath != null) {
            Row(Modifier.padding(start = 8.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                AsyncImage(
                    model = java.io.File(pendingImagePath), contentDescription = stringResource(R.string.attachment),
                    modifier = Modifier.size(46.dp).clip(RoundedCornerShape(10.dp)), contentScale = ContentScale.Crop,
                )
                IconButton(onClick = onClearAttachment) { Icon(Icons.Rounded.Close, stringResource(R.string.remove)) }
            }
        } else if (pendingFileName != null) {
            Row(Modifier.padding(start = 8.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Rounded.Description, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                Text(pendingFileName, fontSize = 14.sp, maxLines = 1)
                IconButton(onClick = onClearAttachment) { Icon(Icons.Rounded.Close, stringResource(R.string.remove)) }
            }
        }
        if (running && input.isNotBlank()) {
            Row(
                Modifier.padding(start = 14.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.AutoMirrored.Rounded.AltRoute, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                Text(stringResource(R.string.steer_hint), fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
            }
        }
        Surface(
            color = MaterialTheme.colorScheme.surfaceContainer,
            shape = RoundedCornerShape(26.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                Modifier.padding(horizontal = 6.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box {
                    IconButton(onClick = { attachSheet = true }, enabled = !running) {
                        Icon(Icons.Rounded.Add, stringResource(R.string.attach), tint = MaterialTheme.colorScheme.onSurface)
                    }
                    AttachMenu(
                        expanded = attachSheet,
                        caps = caps,
                        onCamera = { cameraLauncher.launch(null) },
                        onPhoto = {
                            galleryLauncher.launch(
                                androidx.activity.result.PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                            )
                        },
                        onDraw = { drawing = true },
                        onVideo = { fileLauncher.launch(arrayOf("video/*")) },
                        onAudio = { fileLauncher.launch(arrayOf("audio/*")) },
                        onFile = { fileLauncher.launch(arrayOf("*/*")) },
                        onDismiss = { attachSheet = false },
                    )
                }
                TextField(
                    value = input,
                    onValueChange = { input = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text(stringResource(R.string.ask_placeholder), color = MaterialTheme.colorScheme.outline) },
                    maxLines = 6,
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        disabledContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        cursorColor = MaterialTheme.colorScheme.primary, // мигающая палочка — акцент
                    ),
                )
                Box(Modifier.padding(end = 2.dp), contentAlignment = Alignment.Center) {
                    if (contextTokens > 0) {
                        CircularProgressIndicator(
                            progress = { (contextTokens / CONTEXT_BUDGET).coerceIn(0.02f, 1f) },
                            modifier = Modifier.size(46.dp), strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.primary,
                            trackColor = MaterialTheme.colorScheme.surface,
                        )
                    }
                    val canSend = (input.isNotBlank() || pendingImagePath != null || pendingFileName != null) && !running
                    // #2 «Стиринг на лету»: во время стрима текст в поле → подкрутить направление.
                    val canSteer = running && input.isNotBlank()
                    val active = canSend || canSteer || running
                    val sendScale by animateFloatAsState(if (active) 1f else 0.9f, label = "send")
                    // #Фаза2: при отправке кнопка «выстреливает» золотыми искрами (комета/бурст).
                    var sendBurst by remember { mutableStateOf(0) }
                    Box(contentAlignment = Alignment.Center) {
                        FilledIconButton(
                            onClick = {
                                if (canSteer) { onSteer(input); input = "" }
                                else if (running) onCancel()
                                else if (canSend) { onSend(input); input = ""; sendBurst++ }
                            },
                            enabled = active,
                            modifier = Modifier.graphicsLayer { scaleX = sendScale; scaleY = sendScale },
                            colors = IconButtonDefaults.filledIconButtonColors(
                                containerColor = MaterialTheme.colorScheme.primary,
                                contentColor = MaterialTheme.colorScheme.onPrimary,
                            ),
                        ) {
                            when {
                                canSteer -> Icon(Icons.AutoMirrored.Rounded.AltRoute, stringResource(R.string.steer_action))
                                running -> Icon(Icons.Rounded.Stop, stringResource(R.string.stop))
                                else -> Icon(Icons.Rounded.ArrowUpward, stringResource(R.string.send))
                            }
                        }
                        StarBurst(sendBurst, Modifier.matchParentSize())
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- settings

/** Полноэкранные настройки: хаб → подразделы (не «остров», разбито по разделам). */
@Composable
private fun SettingsScreen(
    state: ChatUiState,
    onTest: (pcUrl: String, pcToken: String) -> Unit,
    onSaveTheme: (String, Long) -> Unit,
    onSelectAppIcon: (String) -> Unit,
    onSetLanguage: (String) -> Unit,
    onSaveUserProfile: (String) -> Unit,
    onSelectModel: (String) -> Unit,
    onSaveModel: (ModelProfile) -> Unit,
    onDeleteModel: (String) -> Unit,
    onDismiss: () -> Unit,
    onSaveBridge: (pcUrl: String, pcToken: String, pcWorkspace: String) -> Unit,
    onSetReactionsOnUser: (Boolean) -> Unit = {},
    onSetHaptics: (Boolean) -> Unit = {},
) {
    var section by remember { mutableStateOf<String?>(null) }
    // #Фаза2: переход между экранами настроек — мягкий «zoom into space» (scale + fade).
    androidx.compose.animation.AnimatedContent(
        targetState = section,
        transitionSpec = {
            (androidx.compose.animation.fadeIn(tween(220)) +
                androidx.compose.animation.scaleIn(initialScale = 0.94f, animationSpec = tween(220))) togetherWith
                (androidx.compose.animation.fadeOut(tween(140)) +
                    androidx.compose.animation.scaleOut(targetScale = 1.03f, animationSpec = tween(140)))
        },
        label = "settingsNav",
    ) { sec ->
    when (sec) {
        "appearance" -> AppearanceScreen(state, onSaveTheme, onSelectAppIcon, onSetLanguage) { section = null }
        "account" -> AccountScreen(state, onSaveUserProfile, onSetReactionsOnUser, onSetHaptics) { section = null }
        "bridge" -> BridgeScreen(state, onTest, onSaveBridge) { section = null }
        "models" -> FullScreenScaffold(stringResource(R.string.settings_models), { section = null }) { pad ->
            SettingsScroll(pad) { ModelsSection(state, onSelectModel, onSaveModel, onDeleteModel) }
        }
        else -> FullScreenScaffold(stringResource(R.string.settings_title), onDismiss) { pad ->
            SettingsScroll(pad) {
                val themeSub = when (state.themeMode) {
                    "light" -> stringResource(R.string.theme_light)
                    "dark" -> stringResource(R.string.theme_dark)
                    "black" -> stringResource(R.string.theme_black)
                    else -> stringResource(R.string.theme_system)
                }
                val activeTitle = state.models.firstOrNull { it.id == state.activeModelId }?.title
                    ?.ifBlank { null } ?: state.models.firstOrNull { it.id == state.activeModelId }?.model ?: "—"
                SettingsGroup {
                    SettingRow(Icons.Rounded.Palette, stringResource(R.string.settings_appearance), stringResource(R.string.settings_appearance_sub, themeSub)) { section = "appearance" }
                    SettingRow(Icons.Rounded.Memory, stringResource(R.string.settings_models), "${state.models.size} · $activeTitle") { section = "models" }
                    if (PcBridgeFacade.SUPPORTED) {
                        SettingRow(
                            Icons.Rounded.Computer, stringResource(R.string.settings_bridge),
                            if (state.pcUrl.isBlank()) stringResource(R.string.bridge_not_set) else state.pcUrl,
                        ) { section = "bridge" }
                    }
                    SettingRow(
                        Icons.Rounded.Person, stringResource(R.string.settings_account),
                        state.nickname.ifBlank { stringResource(R.string.account_no_name) },
                    ) { section = "account" }
                }
            }
        }
    }
    }
}

/** Один вариант иконки в выборе: фон-превью + общая золотая звезда сверху + рамка выбора. */
@Composable
private fun IconOption(id: String, label: String, bgRes: Int, selected: Boolean, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(66.dp).clip(RoundedCornerShape(18.dp))
                .border(
                    width = if (selected) 2.dp else 1.dp,
                    color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant,
                    shape = RoundedCornerShape(18.dp),
                )
                .clickable { onClick() },
            contentAlignment = Alignment.Center,
        ) {
            Image(
                painterResource(bgRes), null,
                modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop,
            )
            AltairStar(size = 40.dp)
            if (selected) {
                Box(
                    Modifier.align(Alignment.TopEnd).padding(4.dp).size(18.dp)
                        .clip(CircleShape).background(MaterialTheme.colorScheme.primary),
                    contentAlignment = Alignment.Center,
                ) { Icon(Icons.Rounded.Check, null, tint = Color.White, modifier = Modifier.size(12.dp)) }
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(label, style = MaterialTheme.typography.labelSmall,
            color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun AppearanceScreen(
    state: ChatUiState,
    onSaveTheme: (String, Long) -> Unit,
    onSelectAppIcon: (String) -> Unit,
    onSetLanguage: (String) -> Unit,
    onBack: () -> Unit,
) {
    // id варианта → (подпись, ресурс фона). deep — базовый фон, остальные — bg-варианты.
    val iconChoices = listOf(
        Triple("deep", stringResource(R.string.icon_deep), R.drawable.ic_launcher_background),
        Triple("blue", stringResource(R.string.icon_blue), R.drawable.ic_launcher_bg_blue),
        Triple("aurora", stringResource(R.string.icon_aurora), R.drawable.ic_launcher_bg_aurora),
        Triple("violet", stringResource(R.string.icon_violet), R.drawable.ic_launcher_bg_violet),
        Triple("ember", stringResource(R.string.icon_ember), R.drawable.ic_launcher_bg_ember),
        Triple("minimal", stringResource(R.string.icon_minimal), R.drawable.ic_launcher_bg_minimal),
        Triple("milky", stringResource(R.string.icon_milky), R.drawable.ic_launcher_bg_milky),
        Triple("rose", stringResource(R.string.icon_rose), R.drawable.ic_launcher_bg_rose),
    )
    FullScreenScaffold(stringResource(R.string.settings_appearance), onBack) { pad ->
        SettingsScroll(pad) {
            SettingsGroup(stringResource(R.string.theme_group)) {
                val themes = listOf(
                    "system" to stringResource(R.string.theme_system),
                    "light" to stringResource(R.string.theme_light),
                    "dark" to stringResource(R.string.theme_dark),
                    "black" to stringResource(R.string.theme_black),
                )
                themes.forEach { (mode, label) ->
                    val sel = state.themeMode == mode
                    Row(
                        Modifier.fillMaxWidth().clickable { onSaveTheme(mode, state.accent) }
                            .padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
                        if (sel) Icon(Icons.Rounded.Check, null, tint = MaterialTheme.colorScheme.primary)
                    }
                }
            }
            SettingsGroup(stringResource(R.string.lang_group)) {
                val langs = listOf(
                    "system" to stringResource(R.string.lang_system),
                    "en" to stringResource(R.string.lang_en),
                    "ru" to stringResource(R.string.lang_ru),
                )
                langs.forEach { (code, label) ->
                    val sel = state.language == code
                    Row(
                        Modifier.fillMaxWidth().clickable { if (!sel) onSetLanguage(code) }
                            .padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
                        if (sel) Icon(Icons.Rounded.Check, null, tint = MaterialTheme.colorScheme.primary)
                    }
                }
            }
            SettingsGroup(stringResource(R.string.theme_accent)) {
                Row(
                    Modifier.fillMaxWidth().padding(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    ACCENT_CHOICES.forEach { c ->
                        val selected = c == state.accent
                        Box(
                            Modifier.size(34.dp).clip(CircleShape).background(Color(c))
                                .clickable { onSaveTheme(state.themeMode, c) },
                            contentAlignment = Alignment.Center,
                        ) {
                            if (selected) Icon(Icons.Rounded.Check, null, tint = Color.White, modifier = Modifier.size(18.dp))
                        }
                    }
                }
            }
            SettingsGroup(stringResource(R.string.icon_group)) {
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    iconChoices.forEach { (id, label, bgRes) ->
                        IconOption(id, label, bgRes, selected = state.appIcon == id) { onSelectAppIcon(id) }
                    }
                }
                Text(
                    stringResource(R.string.icon_change_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun AccountScreen(
    state: ChatUiState,
    onSaveUserProfile: (String) -> Unit,
    onSetReactionsOnUser: (Boolean) -> Unit,
    onSetHaptics: (Boolean) -> Unit,
    onBack: () -> Unit,
) {
    var nickname by remember { mutableStateOf(state.nickname) }
    FullScreenScaffold(
        title = stringResource(R.string.settings_account), onBack = onBack,
        actions = { TextButton(onClick = { onSaveUserProfile(nickname); onBack() }) { Text(stringResource(R.string.action_save)) } },
    ) { pad ->
        SettingsScroll(pad) {
            OutlinedTextField(
                value = nickname, onValueChange = { nickname = it },
                label = { Text(stringResource(R.string.account_name_label)) }, singleLine = true,
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp),
                supportingText = { Text(stringResource(R.string.account_name_hint)) },
            )
            SettingsGroup {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.reactions_title), style = MaterialTheme.typography.bodyLarge)
                        Text(stringResource(R.string.reactions_sub),
                            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    androidx.compose.material3.Switch(
                        checked = state.reactionsOnUser,
                        onCheckedChange = { onSetReactionsOnUser(it) },
                    )
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.haptics_title), style = MaterialTheme.typography.bodyLarge)
                        Text(stringResource(R.string.haptics_sub),
                            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    androidx.compose.material3.Switch(
                        checked = state.hapticsEnabled,
                        onCheckedChange = { onSetHaptics(it) },
                    )
                }
            }
        }
    }
}

@Composable
private fun BridgeScreen(
    state: ChatUiState,
    onTest: (pcUrl: String, pcToken: String) -> Unit,
    onSaveBridge: (String, String, String) -> Unit,
    onBack: () -> Unit,
) {
    var pcUrl by remember { mutableStateOf(state.pcUrl) }
    var pcToken by remember { mutableStateOf("") }
    var pcWorkspace by remember { mutableStateOf(state.pcWorkspace) }
    FullScreenScaffold(
        title = stringResource(R.string.settings_bridge), onBack = onBack,
        actions = { TextButton(onClick = { onSaveBridge(pcUrl, pcToken, pcWorkspace); onBack() }) { Text(stringResource(R.string.action_save)) } },
    ) { pad ->
        SettingsScroll(pad) {
            Text(stringResource(R.string.bridge_intro),
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 4.dp))
            val shape = RoundedCornerShape(16.dp)

            // ---- Быстрое связывание: QR / ссылка (без ручного ввода) ----
            val clipboard = LocalClipboardManager.current
            var link by remember { mutableStateOf("") }
            SettingsGroup(stringResource(R.string.bridge_quick)) {
                Text(
                    stringResource(R.string.bridge_quick_hint),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                )
                OutlinedTextField(
                    link, { link = it }, label = { Text(stringResource(R.string.bridge_link_label)) },
                    singleLine = true, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp), shape = shape,
                )
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TextButton(onClick = { link = clipboard.getText()?.text?.toString().orEmpty() }) { Text(stringResource(R.string.bridge_from_clipboard)) }
                    Spacer(Modifier.weight(1f))
                    Button(
                        onClick = {
                            parsePairLink(link)?.let { p ->
                                if (p.url.isNotBlank()) pcUrl = p.url
                                if (p.token.isNotBlank()) pcToken = p.token
                                if (p.workspace.isNotBlank()) pcWorkspace = p.workspace
                                onSaveBridge(pcUrl, pcToken, pcWorkspace)
                                onTest(pcUrl, pcToken)
                            }
                        },
                        enabled = parsePairLink(link) != null,
                    ) { Text(stringResource(R.string.bridge_connect)) }
                }
            }

            Text(stringResource(R.string.bridge_or_manual), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 4.dp, top = 8.dp))
            OutlinedTextField(pcUrl, { pcUrl = it }, label = { Text(stringResource(R.string.bridge_pc_address)) },
                modifier = Modifier.fillMaxWidth(), shape = shape,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
            OutlinedTextField(pcToken, { pcToken = it }, label = { Text(stringResource(R.string.bridge_token)) },
                modifier = Modifier.fillMaxWidth(), shape = shape,
                visualTransformation = PasswordVisualTransformation())
            OutlinedTextField(pcWorkspace, { pcWorkspace = it }, label = { Text(stringResource(R.string.bridge_workspace)) },
                modifier = Modifier.fillMaxWidth(), shape = shape,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { onTest(pcUrl, pcToken) }) { Text(stringResource(R.string.bridge_test)) }
                if (state.bridgeStatus.isNotBlank()) {
                    Text(state.bridgeStatus, style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** Менеджер моделей: список с выбором активной + форма добавления с capabilities. */
@Composable
private fun ModelsSection(
    state: ChatUiState,
    onSelect: (String) -> Unit,
    onSave: (ModelProfile) -> Unit,
    onDelete: (String) -> Unit,
) {
    Text(stringResource(R.string.settings_models), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
    state.models.forEach { m ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            RadioButton(selected = m.id == state.activeModelId, onClick = { onSelect(m.id) })
            Column(Modifier.weight(1f)) {
                Text(m.title.ifBlank { m.model }, fontSize = 15.sp)
                val capCtx = androidx.compose.ui.platform.LocalContext.current
                val caps = if (m.caps.isEmpty()) stringResource(R.string.caps_text_only)
                else m.caps.mapNotNull { CAP_LABEL[it]?.let(capCtx::getString) }.joinToString(", ")
                Text("${m.model} · $caps", style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline)
            }
            if (state.models.size > 1) {
                IconButton(onClick = { onDelete(m.id) }) { Icon(Icons.Rounded.Close, stringResource(R.string.action_delete)) }
            }
        }
    }

    var expanded by remember { mutableStateOf(false) }
    TextButton(onClick = { expanded = !expanded }) {
        Icon(Icons.Rounded.Add, null, Modifier.size(18.dp)); Spacer(Modifier.width(4.dp)); Text(stringResource(R.string.add_model))
    }
    if (expanded) {
        var title by remember { mutableStateOf("") }
        var model by remember { mutableStateOf("") }
        var url by remember { mutableStateOf("https://api.gateyourway.com/v1") }
        var key by remember { mutableStateOf("") }
        val caps = remember { mutableStateListOf<String>() }
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(title, { title = it }, label = { Text(stringResource(R.string.model_title)) }, singleLine = true)
            OutlinedTextField(model, { model = it }, label = { Text(stringResource(R.string.model_id)) }, singleLine = true)
            OutlinedTextField(url, { url = it }, label = { Text("Endpoint") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), singleLine = true)
            OutlinedTextField(key, { key = it }, label = { Text(stringResource(R.string.api_key)) },
                visualTransformation = PasswordVisualTransformation(), singleLine = true)
            Text(stringResource(R.string.model_accepts), style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                ALL_CAPS.forEach { cap ->
                    val on = cap in caps
                    FilterChipLike(CAP_LABEL[cap]?.let { stringResource(it) } ?: cap, on) {
                        if (on) caps.remove(cap) else caps.add(cap)
                    }
                }
            }
            Button(
                onClick = {
                    if (model.isNotBlank()) {
                        onSave(
                            ModelProfile(
                                id = "m_" + java.util.UUID.randomUUID().toString().take(8),
                                title = title.ifBlank { model }, model = model.trim(),
                                baseUrl = url.trim(), apiKey = key.trim(), caps = caps.toSet(),
                            ),
                        )
                        expanded = false
                    }
                },
                enabled = model.isNotBlank(),
            ) { Text(stringResource(R.string.save_model)) }
        }
    }
}

@Composable
private fun FilterChipLike(label: String, selected: Boolean, onClick: () -> Unit) {
    Surface(
        color = if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
        else MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.clip(RoundedCornerShape(14.dp)),
    ) {
        Row(
            Modifier.clickable { onClick() }.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (selected) { Icon(Icons.Rounded.Check, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.primary); Spacer(Modifier.width(3.dp)) }
            Text(label, fontSize = 13.sp,
                color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
        }
    }
}
