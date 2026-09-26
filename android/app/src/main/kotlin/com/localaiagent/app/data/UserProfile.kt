package com.localaiagent.app.data

/** Локальный профиль пользователя; позднее источник можно заменить аккаунтным. */
data class UserProfile(val nickname: String) {
    companion object {
        const val MAX_NICKNAME_LENGTH = 40

        fun fromNickname(value: String): UserProfile = UserProfile(
            value.replace(Regex("\\s+"), " ").trim().take(MAX_NICKNAME_LENGTH),
        )
    }
}

/** Правило обращения добавляется только при явно указанном нике. */
fun userProfilePromptRule(nickname: String): String {
    val value = UserProfile.fromNickname(nickname).nickname
    if (value.isBlank()) return ""
    return "\n\nUSER PROFILE: the user's name is \"$value\". " +
        "Address them by name naturally when appropriate, but not in every reply."
}
