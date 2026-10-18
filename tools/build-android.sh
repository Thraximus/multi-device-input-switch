#!/usr/bin/env bash
# Build the Android app (Kotlin + Compose, Gradle) and put the APK where the hub installs it from.
# Needs, in .env: ANDROID_HOME (Android SDK), JAVA_HOME (JDK 17/21: the SDK tools don't run on newer
# Java), SWITCHER_KEYSTORE_PASSWORD; optional SWITCHER_KEYSTORE, SWITCHER_KEY_ALIAS, SWITCHER_ANDROID_APP_ID.
# Version: android/VERSION.
# Screenshots of the UI without a device: (cd android && ./gradlew :app:recordRoborazziDebug)
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
. "$repo/tools/env.sh"
: "${ANDROID_HOME:?set ANDROID_HOME (Android SDK) in .env}"
: "${JAVA_HOME:?set JAVA_HOME (JDK 17 or 21) in .env}"
: "${SWITCHER_KEYSTORE_PASSWORD:?set SWITCHER_KEYSTORE_PASSWORD in .env (signs the app; keep it, updates need the same key)}"
export ANDROID_HOME JAVA_HOME
cfg="$SWITCHER_CONFIG_DIR"
export SWITCHER_KEYSTORE="${SWITCHER_KEYSTORE:-$cfg/android-signing.p12}"
export SWITCHER_KEY_ALIAS="${SWITCHER_KEY_ALIAS:-switcher}"
if [ ! -f "$SWITCHER_KEYSTORE" ]; then  # one stable key: Android only updates an app signed with the same key
    mkdir -p "$(dirname "$SWITCHER_KEYSTORE")"
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$SWITCHER_KEYSTORE" -storetype PKCS12 \
        -storepass "$SWITCHER_KEYSTORE_PASSWORD" -alias "$SWITCHER_KEY_ALIAS" -keyalg EC -groupname secp256r1 \
        -validity 36500 -dname "CN=pi-switcher-hub" >/dev/null
fi
[ -f "$repo/android/local.properties" ] || echo "sdk.dir=$ANDROID_HOME" > "$repo/android/local.properties"
(cd "$repo/android" && ./gradlew --quiet :app:assembleRelease)
mkdir -p "$cfg/dist"
cp "$repo/android/app/build/outputs/apk/release/app-release.apk" "$cfg/dist/agent-android.apk"
# What the hub serves for updates over Wi-Fi (get_update/get_file, platform "android").
mkdir -p "$cfg/dist/android"
cp "$cfg/dist/agent-android.apk" "$cfg/dist/android/switcher.apk"
cp "$repo/android/VERSION" "$cfg/dist/android/VERSION"
echo "android app $(cat "$repo/android/VERSION") built: $cfg/dist/agent-android.apk ($(du -h "$cfg/dist/agent-android.apk" | cut -f1))"
