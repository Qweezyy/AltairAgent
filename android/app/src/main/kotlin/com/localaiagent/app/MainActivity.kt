package com.localaiagent.app

import androidx.compose.foundation.layout.fillMaxSize
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.localaiagent.app.ui.ChatScreen
import com.localaiagent.app.ui.theme.LocalAIAgentTheme
import com.localaiagent.app.ui.theme.ThemePrefs

class MainActivity : ComponentActivity() {

    private val requestNotify =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* без повторных запросов */ }

    // Ссылка связывания altair://pair… из QR/браузера (deeplink) — применяется в Compose.
    private val pendingPair = androidx.compose.runtime.mutableStateOf<String?>(null)

    // Servers: the screen, a server link to confirm, a chat to open from a notification.
    private val showServers = androidx.compose.runtime.mutableStateOf(false)
    private val serverLink = androidx.compose.runtime.mutableStateOf<String?>(null)
    private val serverChat = androidx.compose.runtime.mutableStateOf<Pair<String, String>?>(null)

    private fun pairFrom(intent: android.content.Intent?): String? {
        val d = intent?.data ?: return null
        if (!"altair".equals(d.scheme, ignoreCase = true)) return null
        // A server's link goes to Servers, which asks before pairing.
        if ("body".equals(d.host, ignoreCase = true)) {
            serverLink.value = d.toString()
            showServers.value = true
            return null
        }
        return d.toString()
    }

    /** A tap on a server's notification: open that server's chat. */
    private fun serverChatFrom(intent: android.content.Intent?) {
        val server = intent?.getStringExtra(com.localaiagent.app.servers.ServerHub.EXTRA_SERVER) ?: return
        serverChat.value = server to intent.getStringExtra(com.localaiagent.app.servers.ServerHub.EXTRA_CHAT).orEmpty()
        showServers.value = true
        intent.removeExtra(com.localaiagent.app.servers.ServerHub.EXTRA_SERVER)
    }

    // The Servers screen and a chat still to open survive a recreate (language, rotation).
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_SERVERS, showServers.value)
        serverChat.value?.let { (server, chat) -> outState.putStringArray(STATE_SERVER_CHAT, arrayOf(server, chat)) }
    }

    private companion object {
        const val STATE_SERVERS = "servers_open"
        const val STATE_SERVER_CHAT = "servers_chat"
    }

    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(LocaleManager.wrap(newBase))
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pairFrom(intent)?.let { pendingPair.value = it }
        serverChatFrom(intent)
    }

    // Смену иконки применяем только когда ушли в фон — иначе система гасит активный лаунчер-алиас
    // текущей задачи и приложение вылетает. Здесь безопасно: активити уже не на переднем плане.
    override fun onStop() {
        super.onStop()
        AppIcons.applyPending(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, false)
        maybeAskNotifications()
        pendingPair.value = pairFrom(intent)
        savedInstanceState?.let { saved ->
            showServers.value = saved.getBoolean(STATE_SERVERS)
            saved.getStringArray(STATE_SERVER_CHAT)?.takeIf { it.size == 2 }?.let { serverChat.value = it[0] to it[1] }
        }
        serverChatFrom(intent)
        setContent {
            val vm: ChatViewModel = viewModel()
            val state by vm.ui.collectAsState()
            // A pairing deeplink can be fired by any app or web page, so never apply it silently:
            // a malicious link would re-point the bridge at an attacker's server. Ask first.
            val pairCtx = androidx.compose.ui.platform.LocalContext.current
            pendingPair.value?.let { link ->
                val pair = parsePairLink(link)
                if (pair == null) {
                    androidx.compose.runtime.LaunchedEffect(link) {
                        android.widget.Toast.makeText(pairCtx, pairCtx.getString(R.string.pair_fail),
                            android.widget.Toast.LENGTH_SHORT).show()
                        pendingPair.value = null
                    }
                } else LocalAIAgentTheme(prefs = ThemePrefs(state.themeMode, state.accent)) {
                    androidx.compose.material3.AlertDialog(
                        onDismissRequest = { pendingPair.value = null },
                        title = { androidx.compose.material3.Text(pairCtx.getString(R.string.pair_confirm_title)) },
                        text = {
                            androidx.compose.material3.Text(
                                pairCtx.getString(R.string.pair_confirm_body, pair.url.ifBlank { "?" }),
                            )
                        },
                        confirmButton = {
                            androidx.compose.material3.TextButton(onClick = {
                                val ok = vm.applyPairLink(link)
                                android.widget.Toast.makeText(
                                    pairCtx,
                                    pairCtx.getString(if (ok) R.string.pair_ok else R.string.pair_fail),
                                    android.widget.Toast.LENGTH_SHORT,
                                ).show()
                                pendingPair.value = null
                            }) { androidx.compose.material3.Text(pairCtx.getString(R.string.pair_confirm_ok)) }
                        },
                        dismissButton = {
                            androidx.compose.material3.TextButton(onClick = { pendingPair.value = null }) {
                                androidx.compose.material3.Text(pairCtx.getString(R.string.action_cancel))
                            }
                        },
                    )
                }
            }
            // Прозрачные системные бары уже заданы в теме; здесь только контраст иконок
            // статус-/нав-бара под текущую тему, чтобы они были видны на фоне приложения.
            val view = androidx.compose.ui.platform.LocalView.current
            val dark = when (state.themeMode) {
                "light", "snow" -> false
                "dark", "black", "graphite" -> true
                else -> androidx.compose.foundation.isSystemInDarkTheme()
            }
            androidx.compose.runtime.LaunchedEffect(dark) {
                val controller = androidx.core.view.WindowCompat.getInsetsController(window, view)
                controller.isAppearanceLightStatusBars = !dark
                controller.isAppearanceLightNavigationBars = !dark
            }
            LocalAIAgentTheme(prefs = ThemePrefs(state.themeMode, state.accent)) {
              val overlays = androidx.compose.runtime.remember { com.localaiagent.app.ui.OverlayHost() }
              val baseDensity = androidx.compose.ui.platform.LocalDensity.current
              androidx.compose.runtime.CompositionLocalProvider(
                  com.localaiagent.app.ui.LocalOverlayHost provides overlays,
                  // Text size from Settings scales every sp in the app on top of the system setting.
                  androidx.compose.ui.platform.LocalDensity provides androidx.compose.ui.unit.Density(
                      baseDensity.density, baseDensity.fontScale * state.uiScale,
                  ),
                  com.localaiagent.app.ui.LocalUiScale provides (state.uiScale to vm::setUiScale),
                  com.localaiagent.app.ui.LocalAnswerSpacing provides (state.answerSpacing to vm::setAnswerSpacing),
                  com.localaiagent.app.ui.LocalModelActions provides androidx.compose.runtime.remember {
                      com.localaiagent.app.ui.ModelActions(vm::toggleFallback, vm::setReasoningEffort) { vm.saveModel(it, makeActive = false) }
                  },
                  com.localaiagent.app.ui.LocalRefreshPresence provides vm::refreshPresence,
                  com.localaiagent.app.ui.LocalOpenServerLink provides { link: String ->
                      serverLink.value = link
                      showServers.value = true
                  },
              ) {
               androidx.compose.foundation.layout.Box(androidx.compose.ui.Modifier.fillMaxSize()) {
                ChatScreen(
                    state = state,
                    onSend = vm::send,
                    onSaveBridge = vm::saveBridge,
                    onTestBridge = vm::testBridge,
                    onSaveTheme = vm::saveTheme,
                    onSelectAppIcon = vm::selectAppIcon,
                    onSetLanguage = { lang -> vm.setLanguage(lang); this@MainActivity.recreate() },
                    onSaveUserProfile = vm::saveUserProfile,
                    onSelectModel = vm::selectModel,
                    onSaveModel = { vm.saveModel(it) },
                    onDeleteModel = vm::deleteModel,
                    onNewChat = vm::newChat,
                    onSwitchChat = vm::switchChat,
                    onAttachCamera = vm::attachCamera,
                    onPickImageUri = vm::attachImageUri,
                    onPickFileUri = vm::attachFileUri,
                    onClearAttachment = vm::clearAttachment,
                    onRemoveAttachment = vm::removePendingAttachment,
                    onPickUris = vm::attachUris,
                    chatMemoryProvider = vm::chatMemory,
                    onSaveChatMemory = vm::saveChatMemory,
                    chatItemsProvider = vm::currentChatItems,
                    searchProvider = vm::searchAllChats,
                    onStartEdit = vm::startEdit,
                    onCancelEdit = vm::cancelEdit,
                    onSubmitEdit = vm::submitEdit,
                    onRegenerate = vm::regenerateAt,
                    onContinueAnswer = vm::continueAnswer,
                    onRevert = vm::revertToMessage,
                    onBranch = vm::branchFromMessage,
                    onQuote = vm::setQuote,
                    onSteer = vm::steerRun,
                    onSaveSelection = vm::saveSelectionToMemory,
                    onAskSelection = vm::askAboutSelection,
                    onSummarize = vm::summarizeChat,
                    onSwitchVersion = vm::switchVersion,
                    onRemix = vm::remixAnswer,
                    onReact = vm::reactToMessage,
                    onSetReactionsOnUser = vm::setReactionsOnUser,
                    onClearQuote = vm::clearQuote,
                    onCancelRun = vm::cancelRun,
                    onSubmitAsk = vm::submitAsk,
                    onCancelAsk = vm::cancelAsk,
                    onProvideFiles = vm::provideRequestedFiles,
                    onCancelFileReq = vm::cancelFileRequest,
                    onSubmitSecret = vm::submitSecret,
                    onCancelSecret = vm::cancelSecretRequest,
                    onConfirmSecret = vm::confirmSecret,
                    onConfirmMemory = vm::confirmMemory,
                    onConfirmApproval = vm::confirmApproval,
                    onSetHaptics = vm::setHaptics,
                    onAddToBoard = vm::addToBoard,
                    onRemoveFromBoard = vm::removeFromBoard,
                    onClearBoard = vm::clearBoard,
                    onAddSecret = vm::addOrUpdateSecret,
                    onRemoveSecret = vm::removeSecret,
                    onSetSecretAvailability = vm::setSecretAvailability,
                    pluginActions = com.localaiagent.app.ui.PluginActions(
                        saveServer = vm::saveMcpServer,
                        importJson = vm::importMcpJson,
                        removeServer = vm::removeMcpServer,
                        toggleServer = vm::setMcpEnabled,
                        refresh = { vm.refreshMcp() },
                        testServer = vm::testMcpServer,
                        sync = vm::syncPluginsFromPc,
                        importSkill = { vm.importSkill(it) },
                        confirmSkillReplace = vm::confirmSkillReplace,
                        deleteSkill = vm::deleteSkill,
                        clearNotice = vm::clearMcpNotice,
                    ),
                    bridgeSyncSupported = vm.bridgeSyncSupported,
                    onKeyPromptShown = vm::consumeKeyPrompt,
                    onOpenServers = { showServers.value = true },
                )
                if (showServers.value) {
                    val serversVm: com.localaiagent.app.servers.ServersViewModel = viewModel()
                    com.localaiagent.app.ui.ServersScreen(
                        vm = serversVm,
                        initialLink = serverLink.value,
                        openChat = serverChat.value,
                        onConsumed = { serverLink.value = null; serverChat.value = null },
                        onClose = { showServers.value = false },
                    )
                }
                // Full-screen pages (settings, plugins, library…) above the chat, in this window.
                com.localaiagent.app.ui.OverlayLayer(overlays)
               }
              }
            }
        }
    }

    /** На Android 13+ уведомления требуют рантайм-разрешения — спросим один раз. */
    private fun maybeAskNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) requestNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
