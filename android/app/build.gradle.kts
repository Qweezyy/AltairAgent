import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.chaquopy) // embedded Python (offline, both flavors)
}

// The one product version lives in the repository root VERSION file (shared with the PC app), so the
// APK can never drift from it. versionCode is derived: MAJOR*10000 + MINOR*100 + PATCH (0.1.0 -> 100),
// which keeps every release strictly increasing as Android requires for updates.
val altairVersion: String = rootProject.file("../VERSION").takeIf { it.isFile }?.readText()?.trim()
    ?.takeIf { it.matches(Regex("""\d+\.\d+\.\d+""")) }
    ?: error("VERSION at the repository root must hold MAJOR.MINOR.PATCH")
val altairVersionCode: Int = altairVersion.split('.').map { it.toInt() }.let { (ma, mi, pa) ->
    require(mi < 100 && pa < 100) { "MINOR and PATCH must be below 100 for versionCode" }
    ma * 10_000 + mi * 100 + pa
}

android {
    namespace = "com.localaiagent.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.altair"
        minSdk = 26
        targetSdk = 35
        versionCode = altairVersionCode
        versionName = altairVersion
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Chaquopy bundles a native CPython, so a single ABI keeps the APK small (each ABI adds
        // several MB). arm64-v8a covers every current device; add "x86_64" temporarily if an
        // emulator needs it.
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    // Two builds from one codebase:
    //  - full: everything, including the PC bridge (pc/server/ws.py). The MAIN build, kept up to date;
    //  - lite: the same app without the PC bridge or any mention of it. FROZEN.
    // They differ only in the src/full vs src/lite source sets (the PcBridgeFacade class).
    // lite is left out of the regular build but can still be built on demand:
    //   ./gradlew -PwithLite assembleLiteDebug
    val withLite = project.hasProperty("withLite")
    flavorDimensions += "edition"
    productFlavors {
        create("full") {
            dimension = "edition"
            manifestPlaceholders["appLabel"] = "Altair"
        }
        if (withLite) {
            create("lite") {
                dimension = "edition"
                // A separate applicationId, so both builds can be installed side by side.
                applicationIdSuffix = ".lite"
                versionNameSuffix = "-lite"
                manifestPlaceholders["appLabel"] = "Altair Lite"
            }
        }
    }

    // Release signing. The keystore lives outside git; its path and passwords come from
    // android/local.properties (never committed) or the environment (CI). Without them a release
    // build is simply left unsigned instead of failing, so debug work never needs the key.
    val localProps = Properties().apply {
        rootProject.file("local.properties").takeIf { it.isFile }?.inputStream()?.use { load(it) }
    }
    fun secret(prop: String, env: String): String? =
        (localProps.getProperty(prop) ?: System.getenv(env))?.takeIf { it.isNotBlank() }
    val storePath = secret("altair.keystore.file", "ALTAIR_KEYSTORE_FILE")
    val releaseSigning = if (storePath != null && file(storePath).isFile) {
        signingConfigs.create("release") {
            storeFile = file(storePath)
            storePassword = secret("altair.keystore.password", "ALTAIR_KEYSTORE_PASSWORD")
            keyAlias = secret("altair.key.alias", "ALTAIR_KEY_ALIAS") ?: "altair"
            keyPassword = secret("altair.key.password", "ALTAIR_KEY_PASSWORD")
                ?: secret("altair.keystore.password", "ALTAIR_KEYSTORE_PASSWORD")
        }
    } else {
        null
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = releaseSigning
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true // BuildConfig.VERSION_NAME for the MCP client info, User-Agent and Settings
    }
}

// Embedded Python interpreter (offline, in both full and lite). Packages are installed at build
// time (Chaquopy's pip runs during the build); there is no pip at runtime.
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("sympy")   // exact symbolic math (integrals, equations) for studying
            install("numpy")   // arrays and numeric computation
        }
    }
}

dependencies {
    implementation(project(":core"))
    implementation(project(":llm"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.coil.compose)
    implementation(libs.coil.video)
    implementation(libs.okhttp)
    // In-app QR scanner for PC pairing (ZXing, Apache-2.0, no Google Play Services).
    implementation(libs.zxing.embedded)
    // The phone's own Ed25519 identity for servers (0.3.0 stage 6): Android < 13 has no Ed25519 in
    // the platform JCA. BouncyCastle (MIT), lightweight API only.
    implementation(libs.bouncycastle.prov)
    // Background polling of the servers' news.
    implementation(libs.androidx.work)
    implementation(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    debugImplementation(libs.androidx.compose.ui.tooling)

    // On-device UI tests (gestures like pinch and double-tap need a real input pipeline).
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.runner)
    // Espresso before 3.6 calls a hidden InputManager API that newer Android removed.
    androidTestImplementation(libs.androidx.test.espresso)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
