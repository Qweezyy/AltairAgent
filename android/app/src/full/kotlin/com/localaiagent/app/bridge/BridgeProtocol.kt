package com.localaiagent.app.bridge

/**
 * Единый словарь протокола моста ПК ↔ телефон (см. BRIDGE_DESIGN.md).
 * Держим типы сообщений и возможности в одном месте, чтобы телефонная и ПК-стороны
 * не разъезжались по строковым литералам.
 */
object BridgeProtocol {
    // --- телефон → ПК (команды) ---
    const val HELLO = "hello"
    const val LIST_FILES = "list_files"
    const val STAT_FILE = "stat_file"
    const val GET_FILE = "get_file"
    const val PUT_FILE = "put_file"
    const val RUN = "run"
    const val SET_MODE = "set_mode"
    const val ANSWER = "answer"
    const val SYNC_MEMORY = "sync_memory"

    // --- ПК → телефон (ответы/события) ---
    const val R_READY = "ready"
    const val R_HELLO = "hello"
    const val R_FILES = "files"
    const val R_FILE_STAT = "file.stat"
    const val R_FILE = "file"
    const val R_FILE_MISSING = "file.missing"
    const val R_PUT_OK = "put_file.ok"
    const val R_PUT_ERROR = "put_file.error"
    const val R_MEMORY_SYNC = "memory_sync"

    // --- обратные запросы ПК → телефон (во время run) ---
    const val NEED_FILE = "need_file"
    const val NEED_PHOTO = "need_photo"
    const val ASK_USER = "ask_user"
    const val NEED_CAPABILITY = "need_capability"
    const val NEED_FILE_DONE = "need_file.done"
    const val NEED_FILE_CANCEL = "need_file.cancel"
    const val CAPABILITY_RESULT = "capability.result"

    /** Потолок размера файла в обмене (25 МБ) — чтобы не забить сокет. */
    const val MAX_FILE_BYTES: Long = 25L * 1024 * 1024

    /** Что телефон умеет делать для ПК (объявляем в hello). */
    val ANDROID_CAPABILITIES: List<String> = listOf(
        "camera", "photo_library", "files", "location", "notify_user",
        "ask_user", "sensors", "share", "clipboard", "draw",
    )
}
