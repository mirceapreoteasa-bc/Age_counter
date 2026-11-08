plugins {
    id("com.android.application")
    kotlin("android")
}

android {
    namespace = "org.agecore.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "org.agecore.app"
        minSdk = 26            // scoped storage friendly; API 26 is 2017
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    // one source tree, two installers: Android 9 (API 28) and Android 12 (API 31)
    flavorDimensions += "sdk"
    productFlavors {
        create("android9") {
            dimension = "sdk"
            targetSdk = 28
        }
        create("android12") {
            dimension = "sdk"
            targetSdk = 31
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // no signing config yet; the debug build is what gets installed first
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { viewBinding = true }
}

dependencies {
    implementation(project(":core"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.8.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.0.1")
    // SAF reads/writes for the work folder chosen in Settings
    implementation("androidx.documentfile:documentfile:1.0.1")
}
