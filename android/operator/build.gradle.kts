import java.net.URI

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jlleitschuh.gradle.ktlint")
}

// Relay Operator: the operator portal as its own phone app (a WebView shell).
// The screens come live from the server, so they update with every deploy.
val configuredUrl = providers.gradleProperty("relayBaseUrl").orElse("https://relay.invalid/").get()
val endpoint = URI(configuredUrl)
require(endpoint.host != null && endpoint.path == "/") {
    "relayBaseUrl must be an origin ending with /."
}
val releaseUrl = if (endpoint.scheme == "https") configuredUrl else "https://relay.invalid/"
val releaseKeystore = providers.environmentVariable("RELAY_KEYSTORE_FILE").orNull

android {
    namespace = "com.relay.operator"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.relay.operator"
        minSdk = 26
        targetSdk = 36
        versionCode = providers.gradleProperty("relayVersionCode").orNull?.toInt() ?: 1
        versionName = providers.gradleProperty("relayVersionName").orNull ?: "0.1.0"
    }
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = providers.environmentVariable("RELAY_KEYSTORE_PASSWORD").get()
                keyAlias = providers.environmentVariable("RELAY_KEY_ALIAS").get()
                keyPassword = providers.environmentVariable("RELAY_KEY_PASSWORD").get()
            }
        }
    }
    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            buildConfigField("String", "RELAY_BASE_URL", "\"$configuredUrl\"")
        }
        release {
            isMinifyEnabled = true
            if (releaseKeystore != null) signingConfig = signingConfigs.getByName("release")
            buildConfigField("String", "RELAY_BASE_URL", "\"$releaseUrl\"")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    buildFeatures.buildConfig = true
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        abortOnError = true
        warningsAsErrors = true
        checkReleaseBuilds = true
        disable += setOf(
            "AndroidGradlePluginVersion",
            "GradleDependency",
            "NewerVersionAvailable",
            "OldTargetApi"
        )
    }
}
kotlin.compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
ktlint {
    android.set(true)
    version.set("1.7.1")
}
dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-ktx:1.11.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
