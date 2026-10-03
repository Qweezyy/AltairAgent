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

    private fun pairFrom(intent: android.content.Intent?): String? {
        val d = intent?.data ?: return null
        return if ("altair".equals(d.scheme, ignoreCase = true)) d.toString() else null
    }

    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(LocaleManager.wrap(newBase))
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pairFrom(intent)?.let { pendingPair.value = it }
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
                      com.localaiagent.app.ui.ModelActions(vm::toggleFallback, vm::setReasoningEffort)
                  },
                  com.localaiagent.app.ui.LocalRefreshPresence provides vm::refreshPresence,
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
                    onEditMessage = vm::editUserMessage,
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
                )
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
