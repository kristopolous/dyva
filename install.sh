#!/usr/bin/env bash
#
# dyva bootstrap installer -- for people who don't have (or don't want) astral uv / pipx.
# Needs only `git` and Python 3.10+. It clones the repo, builds a virtualenv, installs
# dyva into it, and starts it up.
#
#   curl -fsSL https://raw.githubusercontent.com/kristopolous/free-ollama/main/install.sh | bash
#
# Pass dyva flags through the pipe with `-s --`:
#   curl -fsSL .../install.sh | bash -s -- -p 8080
#
# Env knobs:
#   DYVA_DIR=/path      where to clone/install        (default: ~/free-ollama)
#   DYVA_NO_START=1     install only, don't launch dyva
#   DYVA_REPO=url       clone source                  (default: the GitHub repo)
#   DYVA_BRANCH=name    branch to clone               (default: main)
#
set -eu

REPO_URL="${DYVA_REPO:-https://github.com/kristopolous/free-ollama.git}"
BRANCH="${DYVA_BRANCH:-main}"
DIR="${DYVA_DIR:-$HOME/free-ollama}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

# --- git ------------------------------------------------------------------
command -v git >/dev/null 2>&1 || \
  die "git not found. On macOS run:  xcode-select --install   (then re-run this)."

# --- find a Python >= 3.10 that can build virtualenvs ---------------------
# PY      = first candidate that is new enough AND has ensurepip (venv works)
# PY_ANY  = first candidate that is just new enough (used only for diagnostics)
PY=""
PY_ANY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null || continue
  [ -n "$PY_ANY" ] || PY_ANY="$cand"
  if "$cand" -c 'import ensurepip' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ] && [ -n "$PY_ANY" ]; then
  die "$("$PY_ANY" -V 2>&1) is new enough but can't create virtualenvs (missing the 'venv'/ensurepip module).
    - Debian/Ubuntu:  sudo apt install python3-venv   (or python3.XX-venv to match your version)
    - macOS:          install the full Python from Homebrew (brew install python) or python.org, not the CLT stub"
fi
if [ -z "$PY" ]; then
  have="$(python3 -V 2>&1 || echo 'none')"
  die "Python 3.10+ is required (found: $have).
  The python3 that ships with macOS is 3.9, which is too old. Install a newer one, then re-run:
    - Homebrew:   brew install python
    - or grab it: https://www.python.org/downloads/"
fi
say "Using $("$PY" -V 2>&1) ($(command -v "$PY"))"

# --- clone or update ------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "Updating existing checkout in $DIR"
  git -C "$DIR" pull --ff-only || warn "git pull failed; continuing with what's already there."
else
  say "Cloning into $DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$DIR"
fi

# --- virtualenv + dependencies -------------------------------------------
VENV="$DIR/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating virtualenv in $VENV"
  "$PY" -m venv "$VENV" || \
    die "could not create a virtualenv. On macOS, make sure the full Python (not the CLT stub) is installed."
fi

# pip, but printing each package as it's handled so the install doesn't just
# hang silently. We keep pip's real exit status (not the filter's), and dump
# the full captured output only if it actually failed.
pip_install() {
  local log name rc
  log="$(mktemp)"
  "$VENV/bin/python" -m pip install "$@" 2>&1 | tee "$log" | while IFS= read -r line; do
    case "$line" in
      Collecting\ *)                      name="${line#Collecting }" ;;
      "Requirement already satisfied: "*) name="${line#Requirement already satisfied: }" ;;
      *) continue ;;
    esac
    printf '    \033[36m•\033[0m %s\n' "${name%% *}"
  done
  rc="${PIPESTATUS[0]}"
  [ "$rc" -eq 0 ] || { warn "pip install failed:"; cat "$log" >&2; }
  rm -f "$log"
  return "$rc"
}

say "Installing dependencies (first run can take a minute)"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
pip_install -e "$DIR"

say "Installed. To start it again later, run:  $DIR/start.sh"

# --- launch ---------------------------------------------------------------
if [ "${DYVA_NO_START:-}" = "1" ]; then
  say "DYVA_NO_START set -- not launching. Run $DIR/start.sh when you're ready."
  exit 0
fi
say "Starting dyva on http://localhost:11434  (Ctrl-C to stop)"
exec "$DIR/start.sh" "$@"
