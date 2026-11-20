#!/usr/bin/env bash
# Write one secret into an env file without it ever reaching the screen, shell history, `ps` or logs.
#
#   infrastructure/scripts/put-secret.sh <env-file> <KEY>
#
# Reads the value from the terminal with echo off (paste it, press Enter), then sets KEY=value in
# <env-file> (created with mode 600; an existing KEY line is replaced; other lines are kept).
# Prints only the key and the value's length.
set -euo pipefail
[ $# -eq 2 ] || { sed -n '2,8p' "$0"; exit 2; }
file="$1"; key="$2"
[[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "invalid key: $key" >&2; exit 2; }
[ -t 0 ] || { echo "run this in an interactive terminal (ssh -t)" >&2; exit 2; }
umask 077
IFS= read -r -s -p "Value for $key (input hidden): " value; echo
[ -n "$value" ] || { echo "empty value; nothing written" >&2; exit 1; }
[[ "$value" != *$'\n'* && "$value" != *"'"* ]] || { echo "value contains a newline or quote; refusing" >&2; exit 1; }
touch "$file"; chmod 600 "$file"
tmp="$(mktemp "$(dirname "$file")/.put-secret.XXXXXX")"
grep -v -E "^${key}=" "$file" > "$tmp" || true
printf '%s=%s\n' "$key" "$value" >> "$tmp"
chmod 600 "$tmp"; mv "$tmp" "$file"
unset value
echo "Wrote $key to $file (mode $(stat -c %a "$file" 2>/dev/null || stat -f %Lp "$file"))."
