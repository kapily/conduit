#!/usr/bin/env bash
set -euo pipefail
stage="${RUNNER_TEMP:?}/garden-tools"
mkdir -p "$stage"
npm install --prefix "$stage/harness" --no-audit --no-fund --loglevel=error @deepseek-ai/dsh@0.2.2-rc.5 > "$stage/npm.log" 2>&1
echo "QA_DSH=$stage/harness/node_modules/.bin/dsh" >> "$GITHUB_ENV"
if ! command -v ffmpeg >/dev/null;then
 if [[ "$RUNNER_OS" = macOS ]];then brew install ffmpeg >/dev/null;else sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg >/dev/null;fi
fi
