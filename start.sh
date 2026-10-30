#!/usr/bin/env bash
#
# Start dyva from this checkout's virtualenv. Re-runnable; any arguments are
# passed straight through to dyva.
#
#   ./start.sh            # listen on the default port (11434)
#   ./start.sh -p 8080    # any dyva flag works
#
set -eu

# resolve this script's real directory, following symlinks
src="${BASH_SOURCE[0]}"
while [ -h "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
  src="$(readlink "$src")"
  case "$src" in /*) ;; *) src="$dir/$src" ;; esac
done
DIR="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"

VENV="$DIR/.venv"
if [ ! -x "$VENV/bin/dyva" ]; then
  echo "No virtualenv found in $VENV -- run ./install.sh first, or:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/kristopolous/free-ollama/main/install.sh | bash" >&2
  exit 1
fi

exec "$VENV/bin/dyva" "$@"
