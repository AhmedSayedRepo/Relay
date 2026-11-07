import java.net.URI

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jlleitschuh.gradle.ktlint")
}

val configuredUrl = providers.gradleProperty("relayBaseUrl").orElse("https://relay.invalid/").get()
val endpoint = URI(configuredUrl)
require(endpoint.rawUserInfo == null && endpoint.rawQuery == null && endpoint.rawFragment == null)
require(endpoint.host != null && endpoint.path == "/") {
    "relayBaseUrl must be an origin ending with /."
}
val safeLocal =
    endpoint.scheme == "http" && endpoint.host in setOf("localhost", "127.0.0.1", "10.0.2.2")
require(endpoint.scheme == "https" || safeLocal) {
    "HTTPS is required except explicit debug loopback."
}
val releaseUrl = if (endpoint.scheme == "https") configuredUrl else "https://relay.invalid/"

// Release signing comes only from the environment (CI secrets); nothing is committed.
val releaseKeystore = providers.environmentVariable("RELAY_KEYSTORE_FILE").orNull
val releaseVersionCode = providers.gradleProperty("relayVersionCode").orNull?.toInt()
val releaseVersionName = providers.gradleProperty("relayVersionName").orNull

android {
    namespace = "com.relay.agent"
    compileSdk = 36
    buildToolsVersion = "36.0.0"
    defaultConfig {
        applicationId = "com.relay.agent"
        minSdk = 26
        targetSdk = 36
        versionCode = releaseVersionCode ?: 4
        versionName = releaseVersionName ?: "0.4.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    // "receipts" has no SMS permission, so Play Protect has nothing to flag;
    // "full" adds bank SMS capture for the SMS phone. Same app id: one per phone.
    flavorDimensions += "capture"
    productFlavors {
        create("full") {
            dimension = "capture"
            buildConfigField("boolean", "SMS_CAPTURE", "true")
        }
        create("receipts") {
            dimension = "capture"
            buildConfigField("boolean", "SMS_CAPTURE", "false")
            versionNameSuffix = "-receipts"
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
    buildFeatures {
        compose = true
        buildConfig = true
    }
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
            // Time-dependent; targetSdk upgrades are tracked as tasks.
            "OldTargetApi"
        )
    }
    packaging.resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    // Screenshot tests render Compose under Robolectric with real resources.
    testOptions.unitTests.isIncludeAndroidResources = true
}
kotlin.compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
// Screenshot renders run only in the "Android screenshots" workflow (-Pscreenshots),
// so a rendering problem never fails the main CI build.
tasks.withType<Test>().configureEach {
    if (!providers.gradleProperty("screenshots").isPresent) {
        exclude("**/*ScreenshotTest*")
    }
}
ktlint {
    android.set(true)
    version.set("1.7.1")
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.10.01"))
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.datastore:datastore-preferences:1.1.7")
    implementation("androidx.work:work-runtime-ktx:2.10.5")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Google code scanner: Play services shows the camera UI, so Relay needs no CAMERA permission.
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation(platform("androidx.compose:compose-bom:2025.10.01"))
    testImplementation("androidx.compose.ui:ui-test-junit4")
    testImplementation("org.robolectric:robolectric:4.16.1")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
