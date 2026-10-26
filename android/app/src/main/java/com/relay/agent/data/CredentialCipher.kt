package com.relay.agent.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.relay.agent.domain.SecureStorageFailure
import java.nio.ByteBuffer
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

interface CredentialCipher {
    fun encrypt(plaintext: ByteArray): ByteArray
    fun decrypt(ciphertext: ByteArray): ByteArray
}

/** Key material remains in AndroidKeyStore. Ciphertext includes a version and a fresh 96-bit IV. */
class KeystoreCredentialCipher(private val alias: String = "relay.agent.credentials.v1") :
    CredentialCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray = guarded {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(plaintext)
        ByteBuffer.allocate(1 + 12 + encrypted.size).put(1).put(cipher.iv).put(encrypted).array()
    }

    override fun decrypt(ciphertext: ByteArray): ByteArray = guarded {
        require(ciphertext.size >= 29 && ciphertext[0] == 1.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            key(),
            GCMParameterSpec(128, ciphertext.copyOfRange(1, 13))
        )
        cipher.doFinal(ciphertext.copyOfRange(13, ciphertext.size))
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(
                KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setRandomizedEncryptionRequired(true)
                    .build()
            )
            generateKey()
        }
    }

    private fun <T> guarded(operation: () -> T): T = try {
        operation()
    } catch (_: Exception) {
        // Never include provider exception text or credential contents in a crash/error payload.
        throw SecureStorageFailure()
    }
}
