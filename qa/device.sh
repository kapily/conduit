#!/usr/bin/env bash
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-github-hosted}" == github-hosted ]] || { echo 'This script requires a fresh GitHub-hosted runner.' >&2; exit 1; }
stage="${RUNNER_TEMP:?}/garden-qa"
mkdir -p "$stage"; chmod 700 "$stage"
echo "QA_STAGE=$stage" >> "$GITHUB_ENV"
case "$1" in
 ios)
  export XCODE_XCCONFIG_FILE="$PWD/ios/Flutter/ArmOnly.xcconfig"
  if [[ -f ../tooling/inputs/ort-1.22.0.tgz ]]; then
    export CP_CACHE_DIR="$stage/cocoapods-cache"
    python3 ../tooling/qa/ort_cache.py restore ../tooling/inputs/ort-1.22.0.tgz "$CP_CACHE_DIR"
  fi
  flutter build ios --simulator --debug --no-pub > "$stage/build.log" 2>&1 || { echo 'iOS build failed. Check the locked CocoaPods and Swift package dependencies.'; exit 1; }
  rt=$(xcrun simctl list runtimes -j | python3 -c 'import json,sys;print(next(r["identifier"] for r in json.load(sys.stdin)["runtimes"] if r.get("isAvailable") and r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-27-")))')
  uid=$(xcrun simctl create 'Garden QA' com.apple.CoreSimulator.SimDeviceType.iPhone-17 "$rt")
  xcrun simctl boot "$uid";xcrun simctl bootstatus "$uid" -b
  xcrun simctl status_bar "$uid" override --time 9:41 --batteryState charged --batteryLevel 100 --cellularBars 4 --wifiBars 3
  app=$(find build/ios/iphonesimulator -maxdepth 1 -name '*.app' -type d -print -quit)
  test -d "$app";xcrun simctl install "$uid" "$app"
  ;;
 android)
  test -c /dev/kvm; sudo setfacl -m "u:$(id -un):rw" /dev/kvm
  sdkmanager 'system-images;android-35;google_apis;x86_64' 'emulator' > "$stage/sdk.log"
  echo no | avdmanager create avd -n GardenQA -k 'system-images;android-35;google_apis;x86_64' --device pixel_7 > "$stage/avd.log"
  # Conduit ships ARM ABIs; its split build honors the explicit x86_64 target for this emulator.
  flutter build apk --debug --split-per-abi --target-platform android-x64 --no-pub > "$stage/build.log" 2>&1 || { echo 'Android x86_64 debug build failed.'; exit 1; }
  apk=$(find build/app/outputs/flutter-apk -name '*x86_64*debug*.apk' -print -quit)
  test -f "$apk";unzip -Z1 "$apk" | grep '^lib/x86_64/libflutter.so$' >/dev/null
  "$ANDROID_HOME/emulator/emulator" -avd GardenQA -port 5554 -no-window -no-audio -no-snapshot -no-boot-anim -gpu swiftshader_indirect -accel on > "$stage/emulator.log" 2>&1 &
  echo $! > "$stage/emulator.pid"
  uid=emulator-5554
  for i in $(seq 1 120);do [[ "$(adb -s "$uid" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && break;sleep 2;done
  test "$(adb -s "$uid" shell getprop sys.boot_completed | tr -d '\r')" = 1
  adb -s "$uid" reverse tcp:43671 tcp:43671
  adb -s "$uid" install "$apk" > /dev/null
  ;;
 *) exit 2 ;;
esac
echo "QA_DEVICE=$uid" >> "$GITHUB_ENV"
