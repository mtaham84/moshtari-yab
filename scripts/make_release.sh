#!/usr/bin/env sh
set -eu

root=$(git rev-parse --show-toplevel)
cd "$root"
date=$(date +%Y-%m-%d)
archive="moshtari-yab-$date.zip"

git archive --format=zip --output="$archive" HEAD
printf 'Created %s from tracked files at HEAD.\n' "$archive"
