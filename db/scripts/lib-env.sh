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
  guard_compose_identity "$file"
}

# guard_compose_identity FILE — Contabo layout (FILE defines ESB_STACK): refuse when the shell
# exports a Compose identity variable that differs from FILE, because docker compose would then
# act on the other environment's project with this environment's settings.
guard_compose_identity() {
  local file="$1" key want have
  grep -Eq '^[[:space:]]*(export[[:space:]]+)?ESB_STACK=' "$file" 2>/dev/null || return 0
  for key in ESB_STACK COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES; do
    printenv "$key" > /dev/null 2>&1 || continue
    have="$(printenv "$key")"
    want="$(sed -nE "s/^[[:space:]]*(export[[:space:]]+)?${key}=[\"']?([^\"'#]*)[\"']?.*$/\2/p" "$file" | tail -n 1 | sed 's/[[:space:]]*$//')"
    if [ "$have" != "$want" ]; then
      echo "refusing: exported $key overrides $file (unset $key)" >&2
      return 1
    fi
  done
  return 0
}

# env_file_keys FILE — the variable names an env file defines (same parsing rules as load_env).
env_file_keys() {
  local line
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "${line%$'\r'}" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)= ]] && echo "${BASH_REMATCH[2]}"
  done < "$1"
}

# shell_overrides FILE — names the current shell exports that would silently override FILE
# (docker compose and load_env both let the shell win). Empty output = none.
shell_overrides() {
  local key
  while IFS= read -r key; do
    # printenv succeeds for exported variables only (also when empty): exactly what children see.
    if printenv "$key" > /dev/null 2>&1; then echo "$key"; fi
  done < <(env_file_keys "$1" | sort -u)
  return 0
}
