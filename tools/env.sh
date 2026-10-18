# Sourced by the tools: loads the repo's .env (gitignored; see .env.example) without overriding variables
# already set in the environment, like hub/env.py does for the Python side.
_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
_env="${SWITCHER_ENV_FILE:-$_repo/.env}"
if [ -f "$_env" ]; then
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line="${_line#"${_line%%[![:space:]]*}"}"
        case "$_line" in ''|'#'*) continue ;; esac
        _line="${_line#export }"
        _key="${_line%%=*}"; _val="${_line#*=}"
        _key="${_key%"${_key##*[![:space:]]}"}"
        case "$_val" in \"*\") _val="${_val:1:${#_val}-2}" ;; \'*\') _val="${_val:1:${#_val}-2}" ;; esac
        [ -n "${!_key+x}" ] || export "$_key=$_val"
    done < "$_env"
fi
# Defaults for anything .env leaves out (generic, nothing machine-specific).
export SWITCHER_SERIAL_PORT="${SWITCHER_SERIAL_PORT:-/dev/ttyUSB0}"
export SWITCHER_CONFIG_DIR="${SWITCHER_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/pi-switcher-hub}"
export SWITCHER_CACHE_DIR="${SWITCHER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/pi-switcher-hub}"
unset _repo _env _line _key _val
