package com.relay.agent.presentation

import android.content.Context
import android.content.res.Configuration
import androidx.core.content.edit
import java.util.Locale

/** Relay opens in Arabic; the header button switches to English and back. */
object AppLanguage {
    private const val PREFS = "relay_ui"
    private const val KEY = "language"
    const val DEFAULT = "ar"

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun current(context: Context): String = prefs(context).getString(KEY, DEFAULT) ?: DEFAULT

    fun toggle(context: Context) {
        val next = if (current(context) == "ar") "en" else "ar"
        prefs(context).edit { putString(KEY, next) }
    }

    fun wrap(base: Context): Context {
        val locale = Locale.forLanguageTag(current(base))
        val configuration = Configuration(base.resources.configuration)
        configuration.setLocale(locale)
        configuration.setLayoutDirection(locale)
        return base.createConfigurationContext(configuration)
    }
}
