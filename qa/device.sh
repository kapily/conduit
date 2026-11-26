#!/usr/bin/env bash
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-github-hosted}" == github-hosted ]] || { echo 'This script requires a fresh GitHub-hosted runner.' >&2; exit 1; }
stage="${RUNNER_TEMP:?}/garden-qa"
product="$stage/app-build"
mkdir -p "$product"
chmod 700 "$stage"
printf 'QA_STAGE=%s\n' "$stage" >> "$GITHUB_ENV"
case "$1-$2" in
 prepare-ios)
  test "$(uname -m)" = arm64
  xcodebuild -version
  xcrun simctl list devicetypes | grep -F 'com.apple.CoreSimulator.SimDeviceType.iPhone-17)' >/dev/null
  xcrun simctl list runtimes -j | python3 -c '
import json,sys
runtimes=[r for r in json.load(sys.stdin)["runtimes"] if r.get("isAvailable") and r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-") and r["version"].split(".")[0] in ("26","27")]
print(max(runtimes,key=lambda r:tuple(map(int,r["version"].split("."))))["identifier"])
' > "$stage/ios-runtime"
  ;;
 prepare-android)
  available_kb=$(df -Pk "$stage" | awk 'NR==2 {print $4}')
  echo "Free space before installing emulator and building: ${available_kb} KiB"
  if (( available_kb < 18874368 )); then
   for unused in /usr/share/dotnet /opt/ghc /usr/local/.ghcup; do
    if [[ -d "$unused" ]]; then sudo rm -rf -- "$unused"; fi
   done
  fi
  test -c /dev/kvm
  sudo setfacl -m "u:$(id -un):rw" /dev/kvm
  sdk_tools="$ANDROID_HOME/cmdline-tools/latest/bin"
  if [[ ! -x "$sdk_tools/sdkmanager" ]]; then
   sdk_tools=$(dirname "$(find "$ANDROID_HOME/cmdline-tools" -path '*/bin/sdkmanager' -type f -print -quit)")
  fi
  test -x "$sdk_tools/sdkmanager" && test -x "$sdk_tools/avdmanager"
  "$sdk_tools/sdkmanager" 'system-images;android-35;google_apis;x86_64' emulator platform-tools > "$stage/sdk.log"
  export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$sdk_tools:$PATH"
  printf '%s\n' "$ANDROID_HOME/platform-tools" "$ANDROID_HOME/emulator" "$sdk_tools" >> "$GITHUB_PATH"
  test -x "$ANDROID_HOME/platform-tools/adb" && test -x "$ANDROID_HOME/emulator/emulator"
  adb version
  emulator -accel-check
  echo no | "$sdk_tools/avdmanager" create avd -n GardenQA -k 'system-images;android-35;google_apis;x86_64' --device pixel_7 > "$stage/avd.log"
  emulator -list-avds | grep -Fx GardenQA >/dev/null
  ;;
 build-ios)
  export XCODE_XCCONFIG_FILE="$PWD/ios/Flutter/ArmOnly.xcconfig"
  export CP_CACHE_DIR="$stage/cocoapods-cache"
  python3 ../tooling/qa/ort_cache.py restore ../tooling/inputs/ort-1.22.0.tgz "$CP_CACHE_DIR"
  flutter build ios --simulator --debug --no-pub > "$stage/build.log" 2>&1 || { echo 'iOS build failed.'; exit 1; }
  app=$(find build/ios/iphonesimulator -maxdepth 1 -name '*.app' -type d -print -quit)
  test -d "$app"
  ditto "$app" "$product/Conduit.app"
  ;;
 build-android)
  flutter build apk --debug --split-per-abi --target-platform android-x64 --no-pub > "$stage/build.log" 2>&1 || { echo 'Android x86_64 debug build failed.'; exit 1; }
  apk=$(find build/app/outputs/flutter-apk -name '*x86_64*debug*.apk' -print -quit)
  test -f "$apk"
  unzip -Z1 "$apk" | grep '^lib/x86_64/libflutter.so$' >/dev/null
  cp "$apk" "$product/Conduit.apk"
  ;;
 boot-ios)
  test -f "$product/Conduit.app/Info.plist"
  uid=$(xcrun simctl create 'Garden QA' com.apple.CoreSimulator.SimDeviceType.iPhone-17 "$(cat "$stage/ios-runtime")")
  xcrun simctl boot "$uid"
  xcrun simctl bootstatus "$uid" -b
  xcrun simctl status_bar "$uid" override --time 9:41 --batteryState charged --batteryLevel 100 --cellularBars 4 --wifiBars 3
  xcrun simctl install "$uid" "$product/Conduit.app"
  echo "QA_DEVICE=$uid" >> "$GITHUB_ENV"
  ;;
 boot-android)
  test -f "$product/Conduit.apk"
  emulator -avd GardenQA -port 5554 -no-window -no-audio -no-snapshot -no-boot-anim -gpu software -accel on > "$stage/emulator.log" 2>&1 &
  pid=$!
  echo "$pid" > "$stage/emulator.pid"
  uid=emulator-5554
  for i in $(seq 1 140); do
   if [[ "$(adb -s "$uid" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then break; fi
   if ! kill -0 "$pid" 2>/dev/null; then grep -Ei 'PANIC|FATAL|ERROR' "$stage/emulator.log" | tail -8 || true; exit 1; fi
   sleep 2
  done
  test "$(adb -s "$uid" shell getprop sys.boot_completed | tr -d '\r')" = 1
  adb -s "$uid" shell input keyevent 82
  adb -s "$uid" reverse tcp:43671 tcp:43671
  adb -s "$uid" install "$product/Conduit.apk" > /dev/null
  echo "QA_DEVICE=$uid" >> "$GITHUB_ENV"
  ;;
 *) exit 2 ;;
esac
