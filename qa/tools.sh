#!/usr/bin/env bash
set -euo pipefail
stage="${RUNNER_TEMP:?}/garden-tools"
mkdir -p "$stage"
url='https://github.com/mobile-dev-inc/Maestro/releases/download/cli-2.12.1/maestro.zip'
curl -fsSL --retry 3 "$url" -o "$stage/maestro.zip"
echo "54dbb0716425626604973cbb374c585ca30272375d9ce15177a1152765ddd9d7  $stage/maestro.zip" | shasum -a 256 -c - >/dev/null
unzip -q "$stage/maestro.zip" -d "$stage"
maestro=$(find "$stage" -type f -path '*/bin/maestro' -print -quit)
chmod +x "$maestro"
npm install --prefix "$stage/harness" --no-audit --no-fund --loglevel=error @deepseek-ai/dsh@0.2.2-rc.5 > "$stage/npm.log" 2>&1
echo "QA_DSH=$stage/harness/node_modules/.bin/dsh" >> "$GITHUB_ENV"
echo "QA_MAESTRO=$maestro" >> "$GITHUB_ENV"
if ! command -v ffmpeg >/dev/null;then
 if [[ "$RUNNER_OS" = macOS ]];then brew install ffmpeg >/dev/null;else sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg >/dev/null;fi
fi
