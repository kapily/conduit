#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "$(uname)" = Linux

driver="$GITHUB_WORKSPACE/driver/tool/scroll_benchmark"
mkdir -p "$BENCH_OUT" "$BENCH_APKS" "$BENCH_TOOLS"
cp "$driver/plan.json" "$BENCH_OUT/plan.json"
python3 -m unittest discover -s "$driver" -p 'test_*.py'
cd "$GITHUB_WORKSPACE/app"
test "$(git rev-parse HEAD)" = "$BASELINE"
git submodule update --init --depth 1 third_party/katex third_party/mermaid
flutter pub get
dart run build_runner build
(cd packages/conduit_core && dart run build_runner build)

# Split builds retain the requested x64 ABI; ordinary distribution builds
# intentionally restrict this app to ARM. Both revisions use the same flags.
flutter build apk --profile --split-per-abi --target-platform android-x64 --no-pub
cp build/app/outputs/flutter-apk/app-x86_64-profile.apk "$BENCH_APKS/a.apk"
git fetch --depth=1 origin "$CANDIDATE"
git checkout --detach "$CANDIDATE"
test "$(git rev-parse HEAD)" = "$CANDIDATE"
flutter build apk --profile --split-per-abi --target-platform android-x64 --no-pub
cp build/app/outputs/flutter-apk/app-x86_64-profile.apk "$BENCH_APKS/b.apk"

dart run tool/run_test_shards.dart --no-pub --concurrency=2 > "$BENCH_OUT/tests.log" 2>&1
flutter analyze --no-pub --no-fatal-warnings --no-fatal-infos > "$BENCH_OUT/analysis.log" 2>&1
dart tool/platform_ui_lint.dart --base-ref "$BASELINE" > "$BENCH_OUT/platform-lint.log" 2>&1
(cd android && ./gradlew --stop)

for tool in open_capture capture_scroll; do
  dart compile exe "$driver/$tool.dart" \
    --packages="$PWD/.dart_tool/package_config.json" -o "$BENCH_TOOLS/$tool"
done
python3 - <<'PY'
import hashlib, json, os, pathlib, subprocess
root = pathlib.Path(os.environ['BENCH_OUT'])
sdk = json.loads(subprocess.check_output(['flutter', '--version', '--machine']))
result = {
    'baseline': os.environ['BASELINE'], 'candidate': os.environ['CANDIDATE'],
    'sdk': {k: sdk[k] for k in ('frameworkVersion', 'frameworkRevision', 'engineRevision', 'dartSdkVersion')},
    'apkSha256': {k: hashlib.sha256((pathlib.Path(os.environ['BENCH_APKS']) / (k+'.apk')).read_bytes()).hexdigest() for k in ('a','b')},
    'lockSha256': hashlib.sha256(pathlib.Path('pubspec.lock').read_bytes()).hexdigest(),
}
(root / 'builds.json').write_text(json.dumps(result, indent=2)+'\n')
PY
