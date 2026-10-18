plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.roborazzi)
}

// One version for the whole app: android/VERSION, also read by the hub's GUI.
val appVersion = rootProject.file("VERSION").readText().trim()
val (vMajor, vMinor, vPatch) = appVersion.split(".").map { it.toInt() }

// Settings from the repo's .env (gitignored; see .env.example), or the environment (tools/build-android.sh
// exports them). Nothing machine-specific or secret is written here.
val dotenv: Map<String, String> = rootProject.file("../.env").takeIf { it.isFile }?.readLines().orEmpty()
    .map { it.trim() }.filter { it.isNotEmpty() && !it.startsWith("#") && "=" in it }
    .associate { line ->
        val (k, v) = line.removePrefix("export ").split("=", limit = 2)
        k.trim() to v.trim().removeSurrounding("\"").removeSurrounding("'")
    }
fun setting(name: String, default: String? = null): String? =
    System.getenv(name)?.takeIf { it.isNotEmpty() } ?: dotenv[name]?.takeIf { it.isNotEmpty() } ?: default

val appId = setting("SWITCHER_ANDROID_APP_ID", "dev.switcher.app")!!
val configDir = setting("SWITCHER_CONFIG_DIR", "${System.getProperty("user.home")}/.config/pi-switcher-hub")!!
// The signing key: Android only installs an update signed with the same key, so it must never change.
// tools/build-android.sh creates it on first use.
val keystore = file(setting("SWITCHER_KEYSTORE", "$configDir/android-signing.p12")!!)
val keystorePassword = setting("SWITCHER_KEYSTORE_PASSWORD")
val signingAlias = setting("SWITCHER_KEY_ALIAS", "switcher")!!

android {
    namespace = "online.thrax.switcher"
    compileSdk = 36

    defaultConfig {
        applicationId = appId
        minSdk = 26
        targetSdk = 34
        versionName = appVersion
        versionCode = vMajor * 10_000 + vMinor * 100 + vPatch
    }

    signingConfigs {
        if (keystorePassword != null && keystore.isFile) {
            create("hub") {
                storeFile = keystore
                storeType = "PKCS12"
                storePassword = keystorePassword
                keyAlias = signingAlias
                keyPassword = keystorePassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Unsigned without SWITCHER_KEYSTORE_PASSWORD (and a keystore); tools/build-android.sh insists on both.
            signingConfig = signingConfigs.findByName("hub")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    buildFeatures {
        compose = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all { it.systemProperty("robolectric.graphicsMode", "NATIVE") }
        }
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)

    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.roborazzi)
    testImplementation(libs.roborazzi.compose)
    testImplementation(platform(libs.androidx.compose.bom))
    testImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
