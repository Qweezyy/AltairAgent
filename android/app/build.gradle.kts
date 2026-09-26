plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.chaquopy) // встроенный Python (офлайн, обе сборки)
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

        // Chaquopy встраивает нативный CPython — ограничиваем один ABI, чтобы не раздувать
        // APK (каждый ABI = +неск. МБ). arm64-v8a покрывает все актуальные устройства;
        // для эмулятора при необходимости временно добавить "x86_64".
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    // Две сборки из одного кода:
    //  • full — всё, включая мост к ПК (pc/server/ws.py) — ОСНОВНАЯ, её и обновляем;
    //  • lite — то же приложение, но без моста к ПК и упоминаний — ЗАМОРОЖЕНА.
    // Разница только в наборе исходников src/full vs src/lite (класс PcBridgeFacade).
    // lite убрана из обычной сборки, но остаётся собираемой по требованию:
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
                // Отдельный applicationId — обе сборки можно поставить рядом.
                applicationIdSuffix = ".lite"
                versionNameSuffix = "-lite"
                manifestPlaceholders["appLabel"] = "Altair Lite"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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

// Встроенный интерпретатор Python (офлайн, в обеих сборках full/lite). Пакеты ставятся
// во время сборки (pip у Chaquopy — build-time); в рантайме pip нет.
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("sympy")   // точная символьная математика (интегралы/уравнения) — для учёбы
            install("numpy")   // массивы/численные расчёты
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
