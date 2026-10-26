package com.relay.agent.data

import com.relay.agent.domain.Credentials
import com.relay.agent.domain.SecureStorageFailure
import java.util.Base64
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class CredentialCodecTest {
    private val cipher = ReversibleCipher()
    private val codec = CredentialCodec(cipher)
    private val credentials = Credentials(
        UUID.randomUUID().toString(),
        UUID.randomUUID().toString(),
        "Managed phone",
        "secret-access",
        "secret-refresh",
        "2099-01-01T00:00:00Z",
        UUID.randomUUID().toString()
    )

    @Test
    fun encryptedRoundTripDoesNotExposeSecrets() {
        val encoded = codec.encode(credentials)
        assertFalse(encoded.contains(credentials.accessToken))
        assertFalse(encoded.contains(credentials.refreshToken))
        val decoded = codec.decode(encoded)
        assertEquals(credentials.deviceId, decoded.deviceId)
        assertEquals(credentials.installationId, decoded.installationId)
        assertEquals(credentials.refreshToken, decoded.refreshToken)
        assertEquals(credentials.pendingRenewalId, decoded.pendingRenewalId)
        assertEquals("Credentials([REDACTED])", decoded.toString())
    }

    @Test
    fun malformedOrTamperedBlobFailsClosed() {
        val bytes = Base64.getDecoder().decode(codec.encode(credentials))
        bytes[bytes.lastIndex] = (bytes.last() + 1).toByte()
        assertThrows(SecureStorageFailure::class.java) {
            CredentialCodec(RejectingCipher()).decode(Base64.getEncoder().encodeToString(bytes))
        }
        assertThrows(SecureStorageFailure::class.java) { codec.decode("not-base64") }
    }

    @Test
    fun pendingRenewalKeepsStableCredentialAndRequest() {
        val requestId = UUID.randomUUID().toString()
        val pending = credentials.pending(requestId)
        assertEquals(credentials.refreshToken, pending.refreshToken)
        assertEquals(requestId, pending.pendingRenewalId)
        assertNotEquals(credentials.pendingRenewalId, pending.pendingRenewalId)
    }
}

private class ReversibleCipher : CredentialCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray =
        plaintext.map { (it.toInt() xor 0x55).toByte() }.toByteArray()
    override fun decrypt(ciphertext: ByteArray): ByteArray = encrypt(ciphertext)
}

private class RejectingCipher : CredentialCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray = plaintext
    override fun decrypt(ciphertext: ByteArray): ByteArray = error("authentication failed")
}
