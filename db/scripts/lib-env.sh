# shellcheck shell=bash
# load_env FILE — export KEY=VALUE lines the way docker compose reads an env file, without
# executing it as shell code (values may contain spaces, parentheses, $ or quotes).
# Skips blank lines and comments, strips an unquoted trailing " # comment" and one pair of
# surrounding quotes. Variables already set in the environment win, as with compose.
load_env() {
  local file="$1" line key value
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*|[[:space:]]'#'*) continue ;; esac
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"; value="${BASH_REMATCH[3]}"
    if [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]] || [[ "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"
    else
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    [ -n "${!key+x}" ] || export "$key=$value"
  done < "$file"
}
