#!/usr/bin/env bash
# fuse-dm — the one command a deployment manager types (OPX-1331, S-2 of the setup launcher series). `fuse-dm setup`
# runs the setup loop: resolves claude by absolute path (the official installer runs when there is none), says the three
# things Claude asks once, picks the stage from
# `claude plugin list` (the bootstrap plugin's part one, or setup's part two), starts the session with the setup flags,
# and after each exit reads ~/.fuse/dm-setup/state to decide whether another pass follows — at most three launches.
# A bare `fuse-dm` is the daily session: Fable, auto mode, ~/Fuse, no bypass flag, ever. `fuse-dm update` runs the
# plugin update ritual, then the daily session. `fuse-dm install-shim` is setup's row 14: the shim at ~/.fuse/bin/fuse-dm
# (which execs the installed plugin's copy of this file, so a plugin update needs no re-install) and the Desktop icon.
# The same file is published as dm.sh in FuseFinance/dm-start (OPX-1332): the one line `curl -fsSL …/dm.sh | bash`
# reaches it with no argv and an EMPTY BASH_SOURCE — bash read it from stdin, not from a file — and that case is setup,
# with stdin re-opened as a dup of the launcher's OWN terminal fd (`exec 0<&1`, else `0<&2`) because claude is a TUI and
# stdin is curl's pipe; /dev/tty is the last resort only, never the first: on macOS /dev/tty is the controlling-terminal
# alias device and kqueue rejects it (EINVAL), and Claude Code runs on Bun, which registers stdin with kqueue as it is —
# Node's libuv re-opens a tty through ttyname, Bun does not — so `exec < /dev/tty` killed part one at its first stdin
# pull, before the theme picker (OPX-1373). Bare `fuse-dm` from a file stays the daily session.
# bash 3.2 (macOS /bin/bash) and Git Bash run the same file — the bin/fuse-live shape: die/say, seams as FUSE_DM_*
# environment only (--help), externals kept to mkdir, tee, awk, chmod, rm. Never a settings file, never a default mode.
# Exit: 0 · 64 usage · 69 a prerequisite is missing · 70 a write failed · else claude's own code.
set -u -o pipefail

USAGE='usage: fuse-dm [setup | update | install-shim | --help]'
die() { echo "fuse-dm: $1" >&2; exit "$2"; }
say() { echo "fuse-dm: $1"; }

MODEL="${FUSE_DM_MODEL:-fable}"
EFFORT="${FUSE_DM_EFFORT:-medium}"
BOOTSTRAP_URL="${FUSE_DM_BOOTSTRAP_URL:-https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip}"
OS="${FUSE_DM_OSTYPE:-${OSTYPE:-}}"
ROOT_DIR="$HOME/Fuse"
STATE_FILE="$HOME/.fuse/dm-setup/state"      # read only: the bootstrap plugin and setup write it
SHIM="$HOME/.fuse/bin/fuse-dm"
RETRY_MODEL="opus"

help() {
  printf '%s\n' "$USAGE" '' \
    '  setup         install or finish the Fuse environment: part one (the bootstrap plugin) when deployment-manager is' \
    '                not installed, part two (/deployment-manager:setup) when it is; another pass follows when' \
    '                ~/.fuse/dm-setup/state says so (bootstrap-done, needs-second-pass); at most three launches' \
    '  (none)        the daily session: claude on Fable, auto mode, in ~/Fuse — prompts on, no bypass' \
    '  update        claude plugin marketplace update fuse-internal, claude plugin update deployment-manager@fuse-internal,' \
    '                then the daily session' \
    '  install-shim  write ~/.fuse/bin/fuse-dm (execs the installed plugin'"'"'s bin/fuse-dm, installPath read from' \
    '                installed_plugins.json on every call) and the Desktop icon Fuse Claude.command / Fuse Claude.cmd' \
    '' \
    'environment   FUSE_DM_MODEL (fable) · FUSE_DM_EFFORT (medium, the setup session only)' \
    '              FUSE_DM_BOOTSTRAP_URL (the fuse-start.zip release the --plugin-url of part one points at)' \
    '              FUSE_DM_INSTALLER (the command run when claude is missing; default: the official installer line)' \
    '              FUSE_DM_OSTYPE (the icon kind: darwin* → .command, msys*/cygwin* → .cmd)'
}

# ---- claude, by absolute path: ~/.local/bin first, then PATH; missing → the official installer runs here (one line
# said first), then the resolution again; 69 only when claude is still missing. Never a line for the DM to paste.
# FUSE_DM_INSTALLER replaces the installer command (the tests' stub; the real one is never run by a test). ----
resolve_claude() {
  CLAUDE=""
  if [ -x "$HOME/.local/bin/claude" ]; then CLAUDE="$HOME/.local/bin/claude"; return 0; fi
  CLAUDE="$(command -v claude 2>/dev/null || true)"
  [ -n "$CLAUDE" ]
}
installer_line() {
  case "$OS" in
    msys*|cygwin*) echo 'powershell -command "irm https://claude.ai/install.ps1 | iex"' ;;
    *) echo 'curl -fsSL https://claude.ai/install.sh | bash' ;;
  esac
}
need_claude() {
  resolve_claude && return 0
  say "installing Claude Code — one minute"
  bash -c "${FUSE_DM_INSTALLER:-$(installer_line)}" || say "the installer did not finish cleanly"
  resolve_claude || die "claude is still missing after the installer ran ($(installer_line)) — ask your FDE" 69
}

# ---- the plugin: installed or not, from `claude plugin list` (the stage of part one / part two) ----
plugin_installed() {
  case "$("$CLAUDE" plugin list 2>/dev/null || true)" in *deployment-manager@fuse-internal*) return 0 ;; esac
  return 1
}

# ---- the state file (read after every setup exit; never written here) ----
read_state() {
  STATE=""
  [ -f "$STATE_FILE" ] && read -r STATE < "$STATE_FILE" 2>/dev/null
  STATE="${STATE:-}"
}

# ---- one session: claude in ~/Fuse, the argv rebuilt from the current MODEL; stderr tee'd through a file so a model
# refusal (non-zero, and stderr names the model) can be told from any other exit. Sets RC. A refusal retries once with
# opus and keeps it for the passes that follow; anything else propagates as claude's own code. The setup session alone
# carries FUSE_DM_LAUNCHER=1 (setup's silent case) and the effort pin; the daily session runs on the seat's effort.
RETRIED=0
launch() {
  kind="$1"; stage="${2:-}"
  if [ "$kind" = setup ]; then
    if [ "$stage" = one ]; then
      set -- --model "$MODEL" --effort "$EFFORT" --dangerously-skip-permissions --plugin-url "$BOOTSTRAP_URL" "/fuse-start:begin"
    else
      set -- --model "$MODEL" --effort "$EFFORT" --dangerously-skip-permissions "/deployment-manager:setup"
    fi
  else
    set -- --model "$MODEL" --permission-mode auto
  fi
  cd "$ROOT_DIR" 2>/dev/null || { mkdir -p "$ROOT_DIR" && cd "$ROOT_DIR"; } || die "cannot enter $ROOT_DIR" 70
  errf="${TMPDIR:-/tmp}/fuse-dm.$$.err"
  : > "$errf"
  if [ "$kind" = setup ]; then
    { FUSE_DM_LAUNCHER=1 CLAUDE_CODE_EFFORT_LEVEL="$EFFORT" "$CLAUDE" "$@" 2>&1 1>&3 | tee "$errf" >&2; RC=${PIPESTATUS[0]}; } 3>&1
  else
    { "$CLAUDE" "$@" 2>&1 1>&3 | tee "$errf" >&2; RC=${PIPESTATUS[0]}; } 3>&1
  fi
  err=""; [ -f "$errf" ] && err="$(< "$errf")"
  rm -f "$errf"
  if [ "$RC" -ne 0 ] && [ "$RETRIED" = 0 ] && [ "$MODEL" != "$RETRY_MODEL" ]; then
    case "$err" in
      *"$MODEL"*)
        RETRIED=1
        say "this seat refused the model $MODEL — starting again on $RETRY_MODEL"
        MODEL="$RETRY_MODEL"
        launch "$kind" "$stage"
        ;;
    esac
  fi
}

# ---- setup: the loop ----
do_setup() {
  need_claude
  say "three things Claude asks the first time, then never again: a colour theme (press Enter), a sign-in (pick your @fusefinance.com Google account), a permissions warning (answer Yes)"
  if plugin_installed; then stage=two; else stage=one; fi
  n=0
  while [ "$n" -lt 3 ]; do
    launch setup "$stage"; n=$((n+1))
    [ "$RC" -eq 0 ] || exit "$RC"
    read_state
    case "$STATE" in
      bootstrap-done|needs-second-pass) stage=two ;;
      *) break ;;
    esac
  done
  if [ "$STATE" = done ]; then
    say "setup finished — from now on double-click Fuse Claude on your Desktop, or type fuse-dm"
  else
    say "setup closed (state: ${STATE:-none}) — run fuse-dm setup again to finish; day to day, double-click Fuse Claude on your Desktop, or type fuse-dm"
  fi
  exit 0
}

# ---- daily, and update-then-daily ----
do_daily() {
  need_claude
  launch daily
  exit "$RC"
}
do_update() {
  need_claude
  "$CLAUDE" plugin marketplace update fuse-internal && "$CLAUDE" plugin update deployment-manager@fuse-internal \
    || say "the plugin update did not go through — the session opens on the copy you have; run fuse-dm update again later"
  launch daily
  exit "$RC"
}

# ---- install-shim: setup's row 14 ----
shim_body() {
  while IFS= read -r l; do printf '%s\n' "$l"; done <<'EOT'
#!/usr/bin/env bash
# fuse-dm — the shim. Execs the installed deployment-manager plugin's bin/fuse-dm, resolving the plugin's installPath
# from ~/.claude/plugins/installed_plugins.json on every call, so a plugin update changes nothing here.
root="$(awk '/"deployment-manager@fuse-internal"/{f=1} f&&/"installPath"/{sub(/.*"installPath"[[:space:]]*:[[:space:]]*"/,""); sub(/".*/,""); print; exit}' "$HOME/.claude/plugins/installed_plugins.json" 2>/dev/null)"
root="${root//\\\\//}"
[ -n "$root" ] && [ -f "$root/bin/fuse-dm" ] || { echo "fuse-dm: the deployment-manager plugin is not installed on this computer — fuse-dm setup needs it; run the bootstrap first" >&2; exit 69; }
exec bash "$root/bin/fuse-dm" "$@"
EOT
}
icon_command_body() {
  printf '%s\n' '#!/bin/bash' '# Fuse Claude — opens the day'"'"'s Claude session through the fuse-dm shim.' 'exec "$HOME/.fuse/bin/fuse-dm"'
}
icon_cmd_body() {
  printf '%s\r\n' '@echo off' 'rem Fuse Claude - opens the day'"'"'s Claude session through the fuse-dm shim, in Git Bash.' \
    '"%ProgramFiles%\Git\bin\bash.exe" -l -c "~/.fuse/bin/fuse-dm"'
}
do_install_shim() {
  mkdir -p "${SHIM%/*}" || die "cannot create ${SHIM%/*}" 70
  shim_body > "$SHIM" || die "cannot write $SHIM" 70
  chmod +x "$SHIM"
  say "installed $SHIM"
  desk="$HOME/Desktop"
  [ -d "$desk" ] || { [ -d "$HOME/OneDrive/Desktop" ] && desk="$HOME/OneDrive/Desktop"; }
  mkdir -p "$desk" || die "cannot create $desk" 70
  case "$OS" in
    msys*|cygwin*) icon="$desk/Fuse Claude.cmd"; icon_cmd_body > "$icon" || die "cannot write $icon" 70 ;;
    *) icon="$desk/Fuse Claude.command"; icon_command_body > "$icon" || die "cannot write $icon" 70; chmod +x "$icon" ;;
  esac
  say "installed $icon"
  case ":$PATH:" in
    *":$HOME/.fuse/bin:"*) ;;
    *) say 'to type fuse-dm in a terminal, add ~/.fuse/bin to your PATH: export PATH="$HOME/.fuse/bin:$PATH" in ~/.zshrc (macOS) or ~/.bashrc (Git Bash)' ;;
  esac
  exit 0
}

# ---- arguments ----
# No argv and no BASH_SOURCE: the one-line stub (`curl … | bash`) — setup, with the keyboard back for the TUI. The
# terminal's own fd first (a dup Bun's kqueue accepts), /dev/tty only when neither 1 nor 2 is one — see the header.
# `exec` and `do_setup` stay inside this one compound: bash is still reading the script from the pipe.
if [ $# -eq 0 ] && [ -z "${BASH_SOURCE[0]:-}" ]; then
  if [ -t 1 ]; then exec 0<&1
  elif [ -t 2 ]; then exec 0<&2
  elif ( : < /dev/tty ) 2>/dev/null; then exec < /dev/tty
  fi
  do_setup
fi
case "${1:-}" in
  "") do_daily ;;
  setup) do_setup ;;
  update) do_update ;;
  install-shim) do_install_shim ;;
  -h|--help) help; exit 0 ;;
  *) echo "$USAGE" >&2; die "unknown verb ${1}" 64 ;;
esac
