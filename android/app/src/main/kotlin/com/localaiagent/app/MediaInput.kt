package com.localaiagent.app

/**
 * What of a message's attachments goes to the model itself and what it only gets the path to.
 * Photos always go in. Video and audio go in when the active model takes them (its profile caps),
 * PDFs when it takes files; everything else, and anything past the size budget, is pointed to by path
 * for the agent's tools. Measured on GateYourWay + gemini-3.8-flash: video and audio passed this way
 * are watched and heard.
 */
object MediaInput {
    /**
     * Raw bytes passed in one message. Providers cap inline media near 20 MB of request (Gemini), and
     * base64 grows it by a third; past this the file is pointed to instead, and the model is told why.
     */
    const val INLINE_BUDGET_BYTES = 14L * 1024 * 1024

    data class Plan(
        /** Passed to the model directly, in order. */
        val inline: List<LibraryItem>,
        /** Pointed to by path (the model has no way to take them in). */
        val byPath: List<LibraryItem>,
        /** Would go in, but the message is over the size budget: pointed to, with the reason. */
        val tooLarge: List<LibraryItem>,
    )

    /** Whether the model takes this attachment in directly, size aside. */
    fun takesDirectly(item: LibraryItem, caps: Set<String>): Boolean = when (item.kind) {
        "image" -> true
        "video" -> "video" in caps
        "audio" -> "audio" in caps
        else -> "file" in caps && item.name.lowercase().endsWith(".pdf")
    }

    fun plan(items: List<LibraryItem>, caps: Set<String>, sizeOf: (LibraryItem) -> Long): Plan {
        val inline = mutableListOf<LibraryItem>()
        val byPath = mutableListOf<LibraryItem>()
        val tooLarge = mutableListOf<LibraryItem>()
        var used = 0L
        for (item in items) {
            if (!takesDirectly(item, caps)) { byPath += item; continue }
            val size = sizeOf(item)
            // Photos are small and were always sent; the budget is for video, audio and documents.
            if (item.kind != "image" && used + size > INLINE_BUDGET_BYTES) { tooLarge += item; continue }
            if (item.kind != "image") used += size
            inline += item
        }
        return Plan(inline, byPath, tooLarge)
    }

    /** The MIME type of an attachment by its name, as the data: URI needs it. */
    fun mimeOf(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "mp4", "m4v" -> "video/mp4"
        "webm" -> "video/webm"
        "mov" -> "video/quicktime"
        "3gp" -> "video/3gpp"
        "mkv" -> "video/x-matroska"
        "avi" -> "video/x-msvideo"
        "mp3" -> "audio/mp3"
        "wav" -> "audio/wav"
        "m4a", "aac" -> "audio/aac"
        "ogg", "oga", "opus" -> "audio/ogg"
        "flac" -> "audio/flac"
        "amr" -> "audio/amr"
        "pdf" -> "application/pdf"
        "png" -> "image/png"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        else -> "application/octet-stream"
    }
}
