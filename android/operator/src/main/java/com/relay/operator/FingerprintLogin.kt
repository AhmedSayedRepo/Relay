package com.relay.operator

import android.app.AlertDialog
import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.fragment.app.FragmentActivity
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

/**
 * Sign-in details kept on this phone, encrypted with a key that only the
 * fingerprint (or face) unlocks. The portal page asks through this bridge;
 * the details go back into the page only after the fingerprint is accepted.
 */
class FingerprintLogin(private val activity: FragmentActivity, private val web: WebView) {
    private val prefs = activity.getSharedPreferences("relay_login", Context.MODE_PRIVATE)

    private fun available(): Boolean = BiometricManager.from(activity)
        .canAuthenticate(BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS

    @JavascriptInterface
    fun hasSaved(): Boolean = available() && prefs.contains(DATA)

    @JavascriptInterface
    fun offerSave(email: String, password: String) {
        if (!available() || email.length > 254 || password.length > 1024) return
        activity.runOnUiThread {
            AlertDialog.Builder(activity)
                .setTitle(R.string.fingerprint_offer_title)
                .setMessage(R.string.fingerprint_offer_body)
                .setPositiveButton(R.string.fingerprint_offer_yes) { _, _ -> save(email, password) }
                .setNegativeButton(R.string.not_now, null)
                .show()
        }
    }

    @JavascriptInterface
    fun unlock() {
        activity.runOnUiThread {
            val iv = prefs.getString(IV, null)
            val data = prefs.getString(DATA, null)
            if (iv == null || data == null) return@runOnUiThread
            val cipher = cipher(Cipher.DECRYPT_MODE, Base64.decode(iv, Base64.NO_WRAP))
                ?: return@runOnUiThread
            prompt(cipher, R.string.fingerprint_unlock_title) { ready ->
                val plain = ready.doFinal(Base64.decode(data, Base64.NO_WRAP))
                    .toString(Charsets.UTF_8)
                val email = plain.substringBefore('\n')
                val password = plain.substringAfter('\n')
                web.evaluateJavascript(
                    "window.__relayCredentials && window.__relayCredentials(" +
                        JSONObject.quote(email) + "," + JSONObject.quote(password) + ")",
                    null
                )
            }
        }
    }

    private fun save(email: String, password: String) {
        val cipher = cipher(Cipher.ENCRYPT_MODE, null) ?: return
        prompt(cipher, R.string.fingerprint_save_title) { ready ->
            val sealed = ready.doFinal("$email\n$password".toByteArray(Charsets.UTF_8))
            prefs.edit {
                putString(IV, Base64.encodeToString(ready.iv, Base64.NO_WRAP))
                putString(DATA, Base64.encodeToString(sealed, Base64.NO_WRAP))
            }
        }
    }

    private fun prompt(cipher: Cipher, title: Int, done: (Cipher) -> Unit) {
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(activity.getString(title))
            .setNegativeButtonText(activity.getString(R.string.not_now))
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .build()
        BiometricPrompt(
            activity,
            ContextCompat.getMainExecutor(activity),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(
                    result: BiometricPrompt.AuthenticationResult
                ) {
                    val ready = result.cryptoObject?.cipher ?: return
                    try {
                        done(ready)
                    } catch (_: Exception) {
                        forget()
                    }
                }
            }
        ).authenticate(info, BiometricPrompt.CryptoObject(cipher))
    }

    /** A cipher bound to the fingerprint key; null (and details forgotten)
     * when a new fingerprint was added and the old key no longer works. */
    private fun cipher(mode: Int, iv: ByteArray?): Cipher? = try {
        Cipher.getInstance("AES/GCM/NoPadding").apply {
            if (iv == null) init(mode, key()) else init(mode, key(), GCMParameterSpec(128, iv))
        }
    } catch (_: KeyPermanentlyInvalidatedException) {
        forget()
        null
    } catch (_: Exception) {
        null
    }

    private fun forget() {
        prefs.edit { clear() }
        KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(ALIAS)
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val spec = KeyGenParameterSpec.Builder(
            ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(true)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
                }
            }
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
            .apply { init(spec) }
            .generateKey()
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "relay-operator-login"
        const val IV = "iv"
        const val DATA = "data"
    }
}
