package com.relay.agent.data

import com.relay.agent.domain.Configuration
import com.relay.agent.domain.InvalidConfiguration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ConfigurationCodecTest {
    private val codec = ConfigurationCodec()

    @Test
    fun parsesSupportedDocumentAndInterval() {
        val value = codec.parse(
            """{"schema_version":1,"version":2,"settings":{"sync_interval_seconds":1800,"flag":true}}""",
            Configuration(1, """{"schema_version":1,"version":1,"settings":{}}""")
        )
        assertEquals(2, value.version)
        assertEquals(1800L, value.syncIntervalSeconds)
    }

    @Test
    fun defaultsIntervalAndAllowsExactIdempotentDocument() {
        val document = """{"schema_version":1,"version":1,"settings":{}}"""
        val first = codec.parse(document, null)
        assertEquals(900L, first.syncIntervalSeconds)
        assertEquals(first, codec.parse(document, first))
    }

    @Test
    fun rejectsRollbackMutationUnsupportedSchemaAndBounds() {
        val current = Configuration(2, """{"schema_version":1,"version":2,"settings":{}}""")
        listOf(
            """{"schema_version":1,"version":1,"settings":{}}""",
            """{"schema_version":2,"version":3,"settings":{}}""",
            """{"schema_version":1,"version":2,"settings":{"changed":true}}""",
            """{"schema_version":1,"version":3,"settings":{"sync_interval_seconds":899}}""",
            """{"schema_version":1,"version":3,"settings":{"sync_interval_seconds":86401}}""",
            """{"schema_version":1,"version":3,"settings":{"sync_interval_seconds":true}}"""
        ).forEach { document ->
            assertThrows(InvalidConfiguration::class.java) { codec.parse(document, current) }
        }
    }

    @Test
    fun rejectsOversizedAndDeepDocuments() {
        val oversized = """{"schema_version":1,"version":1,"settings":{"x":"${"x".repeat(
            65536
        )}"}}"""
        assertThrows(InvalidConfiguration::class.java) { codec.parse(oversized, null) }
        val deep = "{".repeat(14) + "\"value\":1" + "}".repeat(14)
        assertThrows(InvalidConfiguration::class.java) { codec.parse(deep, null) }
    }

    @Test
    fun parsesStrictDeclarativeSmsRulesWithoutExecutableExpressions() {
        val document = """
            {
              "schema_version":1,
              "version":3,
              "settings":{
                "collectors":{
                  "sms":{
                    "enabled":true,
                    "rules":[
                      {
                        "id":"receipt",
                        "enabled":true,
                        "sender":{"mode":"prefix","value":"Example"},
                        "body":{
                          "contains":"amount",
                          "any_contains":["received","credited"],
                          "all_contains":["reference"]
                        }
                      }
                    ]
                  }
                }
              }
            }
        """.trimIndent()
        val value = codec.parse(document, null)
        assertEquals(true, value.sms.enabled)
        assertEquals("receipt", value.sms.rules.single().id)
        assertEquals("prefix", value.sms.rules.single().senderMode)
        assertEquals(listOf("reference"), value.sms.rules.single().bodyAllContains)
    }

    @Test
    fun rejectsUnsafeOrIllTypedSmsRulesAndPreservesCallerLastGoodConfiguration() {
        val invalid = listOf(
            """{"id":1,"enabled":true,"body":{"contains":"x"}}""",
            """{"id":"x","enabled":true,"sender":{"mode":"regex","value":".*"}}""",
            """{"id":"x","enabled":true,"body":{"contains":1}}""",
            """{"id":"x","enabled":true,"body":{"any_contains":[1]}}""",
            """{"id":"x","enabled":true,"body":{"all_contains":[]}}""",
            """{"id":"x","enabled":true,"script":"return true"}"""
        )
        val good = Configuration(2, """{"schema_version":1,"version":2,"settings":{}}""")
        invalid.forEach { rule ->
            val document =
                """{"schema_version":1,"version":3,"settings":{"collectors":{"sms":""" +
                    """{"enabled":true,"rules":[$rule]}}}}"""
            assertThrows(InvalidConfiguration::class.java) { codec.parse(document, good) }
            assertEquals(2, good.version)
            assertEquals(false, good.sms.enabled)
        }
    }

    @Test
    fun parsesBoundedSafImageRulesAndRejectsExecutableOrBroadSources() {
        val document = """
            {
              "schema_version":1,
              "version":4,
              "settings":{"collectors":{"images":{
                "enabled":true,
                "sources":[{"id":"approved","type":"saf_tree"}],
                "rules":[{"id":"png","enabled":true,"source_id":"approved",
                  "mime_types":["image/png"],"filename":{"mode":"suffix","value":".png"},
                  "min_bytes":1,"max_bytes":1048576,"max_width":4000,"max_height":4000}]
              }}}
            }
        """.trimIndent()
        val parsed = codec.parse(document, null)
        assertEquals(true, parsed.images.enabled)
        assertEquals("approved", parsed.images.sources.single().id)
        assertEquals("png", parsed.images.rules.single().id)
        listOf(
            document.replace("saf_tree", "filesystem_crawl"),
            document.replace("\"max_height\":4000", "\"script\":\"run\"")
        ).forEach {
            assertThrows(InvalidConfiguration::class.java) { codec.parse(it, null) }
        }
    }
}
