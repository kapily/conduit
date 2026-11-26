#!/usr/bin/env bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && $(uname -m) == arm64 ]]
stage=${QA_STAGE:?}
dest="$stage/axe-2.2.0"
mkdir -p "$dest"
archive="$dest/release.tgz"
curl --fail --silent --show-error --location --retry 3 --proto '=https' --proto-redir '=https' 'https://github.com/cameroncooke/AXe/releases/download/v2.2.0/AXe-macOS-v2.2.0-universal.tar.gz' -o "$archive"
printf '%s  %s\n' '456772eeec29cc3a4bcdf7f8fa46dfea39aa5e21350feea027e7e7c2f1c76922' "$archive" | shasum -a 256 --check --status
tar -xzf "$archive" -C "$dest"
test "$("$dest/axe" --version)" = v2.2.0
