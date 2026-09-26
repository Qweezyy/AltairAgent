package com.localaiagent.app.data

import org.junit.Assert.assertEquals
import org.junit.Test

class UserProfileTest {
    @Test
    fun `normalizes nickname before saving`() {
        val profile = UserProfile.fromNickname("  Алиса\n\t ИИ  ")

        assertEquals("Алиса ИИ", profile.nickname)
    }

    @Test
    fun `limits nickname length`() {
        val profile = UserProfile.fromNickname("a".repeat(UserProfile.MAX_NICKNAME_LENGTH + 1))

        assertEquals(UserProfile.MAX_NICKNAME_LENGTH, profile.nickname.length)
    }

    @Test
    fun `adds prompt rule only for a nickname`() {
        assertEquals("", userProfilePromptRule("   "))
        assertEquals(
            "\n\nUSER PROFILE: the user's name is \"Алиса\". " +
                "Address them by name naturally when appropriate, but not in every reply.",
            userProfilePromptRule(" Алиса "),
        )
    }
}
