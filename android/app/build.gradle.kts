import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.chaquopy) // embedded Python (offline, both flavors)
}

android {
    namespace = "com.localaiagent.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.altair"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

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
    implementation(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
