package com.localaiagent.core

/**
 * История одного диалога. Оценка токенов — как на ПК (грубо ~4 символа на токен),
 * этого достаточно для кольца заполнения контекста.
 */
class Session(
    var systemPrompt: String = "",
    private val _messages: MutableList<Message> = mutableListOf(),
) {
    val messages: List<Message> get() = _messages

    fun addUser(text: String, parts: List<Part> = emptyList()) {
        _messages += Message(Role.USER, text, parts)
    }

    fun addAssistant(turn: AssistantTurn) {
        _messages += Message(Role.ASSISTANT, turn.content, toolCalls = turn.toolCalls)
    }

    fun addToolResult(callId: String, name: String, output: String) {
        _messages += Message(Role.TOOL, output, toolCallId = callId)
    }

    /** Снимок для отправки модели: системный промпт + история. */
    fun snapshot(): List<Message> =
        buildList {
            if (systemPrompt.isNotBlank()) add(Message(Role.SYSTEM, systemPrompt))
            addAll(_messages)
        }

    /** Грубая оценка токенов контекста (для индикатора). */
    fun tokenEstimate(): Int {
        val chars = snapshot().sumOf { m ->
            m.content.length + m.parts.sumOf { p -> if (p is Part.Text) p.text.length else 800 }
        }
        return chars / 4
    }

    fun reset() {
        _messages.clear()
    }

    /** Полностью заменяет историю (для правки/отката/ветвления диалога). */
    fun loadHistory(msgs: List<Message>) {
        _messages.clear()
        _messages.addAll(msgs)
    }
}
