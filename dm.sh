#!/usr/bin/env bash
# fuse-dm — the one command a deployment manager types (OPX-1331, S-2 of the setup launcher series). Four verbs:
# `fuse-dm setup`, bare `fuse-dm`, `fuse-dm update`, `fuse-dm term` (and `install-shim`, setup's row 14). `fuse-dm setup`
# runs the setup loop: resolves claude by absolute path (the official installer runs when there is none), says the three
# things Claude asks once, picks the stage from
# `claude plugin list` (the bootstrap plugin's part one, or setup's part two), starts the session with the setup flags,
# and after each exit reads ~/.fuse/dm-setup/state to decide whether another pass follows — at most three launches.
# `fuse-dm term` is the daily session in the terminal: Fable, auto mode, ~/Fuse, no bypass flag, ever. A bare `fuse-dm`
# (the Desktop icon) and `fuse-dm update` (the plugin update ritual first) run the same keep-current check, then open
# T3 Code when setup set it up, else that same terminal session. Keep current (OPX-1366, S-6): the launcher records
# the deployment-manager version it last ran a pass for in ~/.fuse/dm-setup/plugin-version (its own file — setup
# rewrites `state` whole) and reads the installed one from installed_plugins.json (awk, no claude process); moved →
# the daily session opens on setup's keep-current pass, the prompt "/deployment-manager:setup keep-current" as the
# last token of the same daily argv, one line said first, and the record is rewritten after a clean exit; equal →
# nothing; no record → today's daily and the version recorded after a clean exit; unknown → nothing.
# T3 Code (OPX-1890): ready = ~/.fuse/dm-setup/t3-ready (setup's row 15 writes it; only read here) AND the app found —
# FUSE_DM_T3_APP when set, else the first T3 Code*.app in ~/Applications, then /Applications (Git Bash: the first
# T3 Code*.exe in ~/AppData/Local/Programs/t3code); any other OS is never ready. Ready → a moved version runs the
# keep-current pass headless (`-p` right before the prompt, stdin from /dev/null, the same opus retry), which closes
# itself; it failing → the terminal session on that prompt instead. Then the record, then the opener (`open --env
# T3CODE_TELEMETRY_ENABLED=false -a`, Git Bash `cmd //c start ""`); it failing → the terminal session. In macOS
# Terminal, right before exit 0, a background osascript closes the launcher's own window once its one tab has no
# process left, so a DM's own shell is never closed. FUSE_DM_SURFACE=term, no marker, or no app → the terminal session.
# A running T3 Code is never restarted or quit; nothing here installs it or writes its settings.
# `fuse-dm install-shim` is setup's row 14: the shim at ~/.fuse/bin/fuse-dm
# (which execs the installed plugin's copy of this file, so a plugin update needs no re-install) and the Desktop icon.
# The same file is published as dm.sh in FuseFinance/dm-start (OPX-1332): the one line `curl -fsSL …/dm.sh | bash`
# reaches it with no argv and an EMPTY BASH_SOURCE — bash read it from stdin, not from a file — and that case is setup,
# with stdin re-opened as a dup of the launcher's OWN terminal fd (`exec 0<&1`, else `0<&2`) because claude is a TUI and
# stdin is curl's pipe; /dev/tty is the last resort only, never the first: on macOS /dev/tty is the controlling-terminal
# alias device and kqueue rejects it (EINVAL), and Claude Code runs on Bun, which registers stdin with kqueue as it is —
# Node's libuv re-opens a tty through ttyname, Bun does not — so `exec < /dev/tty` killed part one at its first stdin
# pull, before the theme picker (OPX-1373). Bare `fuse-dm` from a file stays the daily verb.
# bash 3.2 (macOS /bin/bash) and Git Bash run the same file — the bin/fuse-live shape: die/say, seams as FUSE_DM_*
# environment only (--help), externals kept to mkdir, tee, awk, chmod, rm; on the T3 Code path the opener (open on
# macOS, cmd under Git Bash), and in macOS Terminal only tty and osascript (the AppleScript's own delay waits, never a
# shell loop). Never a settings file, never a default mode.
# Exit: 0 · 64 usage · 69 a prerequisite is missing · 70 a write failed · else claude's own code.
set -u -o pipefail

USAGE='usage: fuse-dm [setup | update | term | install-shim | --help]'
die() { echo "fuse-dm: $1" >&2; exit "$2"; }
say() { echo "fuse-dm: $1"; }

MODEL="${FUSE_DM_MODEL:-fable}"
EFFORT="${FUSE_DM_EFFORT:-medium}"
BOOTSTRAP_URL="${FUSE_DM_BOOTSTRAP_URL:-https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip}"
OS="${FUSE_DM_OSTYPE:-${OSTYPE:-}}"
ROOT_DIR="$HOME/Fuse"
STATE_FILE="$HOME/.fuse/dm-setup/state"      # read only: the bootstrap plugin and setup write it
VERSION_FILE="$HOME/.fuse/dm-setup/plugin-version"   # the launcher's own: the plugin version it last ran a pass for
INSTALLED_PLUGINS="$HOME/.claude/plugins/installed_plugins.json"
KEEP_CURRENT_PROMPT="/deployment-manager:setup keep-current"
SHIM="$HOME/.fuse/bin/fuse-dm"
RETRY_MODEL="opus"
T3_MARKER="$HOME/.fuse/dm-setup/t3-ready"     # read only: setup's row 15 (t3_code.py settings) writes it

help() {
  printf '%s\n' "$USAGE" '' \
    '  setup         install or finish the Fuse environment: part one (the bootstrap plugin) when deployment-manager is' \
    '                not installed, part two (/deployment-manager:setup) when it is; another pass follows when' \
    '                ~/.fuse/dm-setup/state says so (bootstrap-done, needs-second-pass); at most three launches' \
    '  (none)        the Desktop icon: the keep-current check, then T3 Code when setup set it up (~/.fuse/dm-setup/t3-ready' \
    '                and the app found) — a moved version is re-checked first in a headless pass (claude -p) that closes' \
    '                by itself, then T3 Code opens and, in macOS Terminal, this window closes; otherwise the term session' \
    '  term          the daily session in the terminal: claude on Fable, auto mode, in ~/Fuse — prompts on, no bypass; when' \
    '                the installed deployment-manager version differs from ~/.fuse/dm-setup/plugin-version (the one the last' \
    '                pass ran for), the session opens on /deployment-manager:setup keep-current — every row re-checked,' \
    '                seconds — and the record is rewritten after a clean exit; no record yet → the daily session, the' \
    '                version recorded' \
    '  update        claude plugin marketplace update fuse-internal, claude plugin update deployment-manager@fuse-internal,' \
    '                then what the bare verb opens, with the same keep-current check' \
    '  install-shim  write ~/.fuse/bin/fuse-dm (execs the installed plugin'"'"'s bin/fuse-dm, installPath read from' \
    '                installed_plugins.json on every call) and the Desktop icon Fuse Claude.command / Fuse Claude.cmd' \
    '' \
    'environment   FUSE_DM_MODEL (fable) · FUSE_DM_EFFORT (medium, the setup session only)' \
    '              FUSE_DM_BOOTSTRAP_URL (the fuse-start.zip release the --plugin-url of part one points at)' \
    '              FUSE_DM_INSTALLER (the command run when claude is missing; default: the official installer line)' \
    '              FUSE_DM_OSTYPE (the icon kind: darwin* → .command, msys*/cygwin* → .cmd)' \
    '              FUSE_DM_SURFACE (term → the bare verb and update take the terminal session, T3 Code ready or not)' \
    '              FUSE_DM_T3_APP (the T3 Code app'"'"'s path; default: the first ~/Applications/T3 Code*.app, then' \
    '              /Applications; Git Bash: ~/AppData/Local/Programs/t3code/T3 Code*.exe)' \
    '              FUSE_DM_OPENER (the command that opens it; default: open on macOS, cmd under Git Bash)'
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

# ---- keep current (OPX-1366): the installed deployment-manager version, from installed_plugins.json with the shim's
# awk idiom on "version" (the entry's own key; the file's top-level "version" is an integer and never matches) — no
# claude process, so the daily verb still launches once. INSTALLED empty = unknown = nothing. RECORD is what
# ~/.fuse/dm-setup/plugin-version holds, the launcher's own file: setup rewrites `state` whole, so this is not a second
# line of it. Different → KEEP_CURRENT=1 and one line said (on the T3 Code path, ON_T3=1, the line that says T3 Code
# opens after); the record is written only after a clean exit, and RECORD follows it so a second call writes nothing. ----
INSTALLED=""; RECORD=""; KEEP_CURRENT=0; ON_T3=0
installed_version() {
  INSTALLED="$(awk '/"deployment-manager@fuse-internal"/{f=1} f&&/"version"[[:space:]]*:[[:space:]]*"/{sub(/.*"version"[[:space:]]*:[[:space:]]*"/,""); sub(/".*/,""); print; exit}' "$INSTALLED_PLUGINS" 2>/dev/null)"
}
keep_current_check() {
  KEEP_CURRENT=0; RECORD=""
  installed_version
  [ -n "$INSTALLED" ] || return 0
  [ -f "$VERSION_FILE" ] && read -r RECORD < "$VERSION_FILE" 2>/dev/null
  RECORD="${RECORD:-}"
  if [ -n "$RECORD" ] && [ "$RECORD" != "$INSTALLED" ]; then
    KEEP_CURRENT=1
    if [ "$ON_T3" = 1 ]; then
      say "the plugin moved to $INSTALLED — re-checking the environment first, then T3 Code opens by itself (threads already open there keep the old version until you start a new one)"
    else
      say "the plugin moved to $INSTALLED — re-checking the environment first, seconds"
    fi
  fi
}
record_version() {   # after a clean exit only; nothing to write when the version is unknown or already recorded
  [ -n "$INSTALLED" ] && [ "$RECORD" != "$INSTALLED" ] || return 0
  { mkdir -p "${VERSION_FILE%/*}" && printf '%s\n' "$INSTALLED" > "$VERSION_FILE"; } 2>/dev/null && RECORD="$INSTALLED" \
    || say "could not record the plugin version at $VERSION_FILE — the next launch re-checks the environment again"
}

# ---- one session: claude in ~/Fuse, the argv rebuilt from the current MODEL; stderr tee'd through a file so a model
# refusal (non-zero, and stderr names the model) can be told from any other exit. Sets RC. A refusal retries once with
# opus and keeps it for the passes that follow; anything else propagates as claude's own code. The setup session alone
# carries FUSE_DM_LAUNCHER=1 (setup's silent case) and the effort pin; the daily session runs on the seat's effort.
# `pass` (OPX-1890) is the keep-current pass headless: the daily argv with `-p` right before the prompt, stdin from
# /dev/null so nothing waits on a stdin that is not a terminal; it ends by itself, and a refusal retry keeps `-p`.
RETRIED=0
launch() {
  kind="$1"; stage="${2:-}"
  if [ "$kind" = setup ]; then
    if [ "$stage" = one ]; then
      set -- --model "$MODEL" --effort "$EFFORT" --dangerously-skip-permissions --plugin-url "$BOOTSTRAP_URL" "/fuse-start:begin"
    else
      set -- --model "$MODEL" --effort "$EFFORT" --dangerously-skip-permissions "/deployment-manager:setup"
    fi
  elif [ "$kind" = pass ]; then
    set -- --model "$MODEL" --permission-mode auto -p "$KEEP_CURRENT_PROMPT"
  elif [ "$KEEP_CURRENT" = 1 ]; then
    set -- --model "$MODEL" --permission-mode auto "$KEEP_CURRENT_PROMPT"
  else
    set -- --model "$MODEL" --permission-mode auto
  fi
  cd "$ROOT_DIR" 2>/dev/null || { mkdir -p "$ROOT_DIR" && cd "$ROOT_DIR"; } || die "cannot enter $ROOT_DIR" 70
  errf="${TMPDIR:-/tmp}/fuse-dm.$$.err"
  : > "$errf"
  if [ "$kind" = setup ]; then
    { FUSE_DM_LAUNCHER=1 CLAUDE_CODE_EFFORT_LEVEL="$EFFORT" "$CLAUDE" "$@" 2>&1 1>&3 | tee "$errf" >&2; RC=${PIPESTATUS[0]}; } 3>&1
  elif [ "$kind" = pass ]; then
    { "$CLAUDE" "$@" < /dev/null 2>&1 1>&3 | tee "$errf" >&2; RC=${PIPESTATUS[0]}; } 3>&1
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
    installed_version; RECORD=""; record_version     # read after the passes: part one is what installs the plugin
    say "setup finished — from now on double-click Fuse Claude on your Desktop, or type fuse-dm"
  else
    say "setup closed (state: ${STATE:-none}) — run fuse-dm setup again to finish; day to day, double-click Fuse Claude on your Desktop, or type fuse-dm"
  fi
  exit 0
}

# ---- T3 Code (OPX-1890): ready = the marker setup's row 15 writes AND the app found; macOS and Git Bash only ----
T3_APP=""; ORC=0
find_t3_app() {   # sets T3_APP; FUSE_DM_T3_APP set → the only place looked at; the glob, never the product's name
  T3_APP=""
  if [ -n "${FUSE_DM_T3_APP:-}" ]; then
    case "$OS" in
      darwin*) [ -d "$FUSE_DM_T3_APP" ] && T3_APP="$FUSE_DM_T3_APP" ;;
      msys*|cygwin*) [ -f "$FUSE_DM_T3_APP" ] && T3_APP="$FUSE_DM_T3_APP" ;;
    esac
    [ -n "$T3_APP" ]
    return
  fi
  case "$OS" in
    darwin*) for a in "$HOME"/Applications/T3\ Code*.app /Applications/T3\ Code*.app; do [ -d "$a" ] && { T3_APP="$a"; return 0; }; done ;;
    msys*|cygwin*) for a in "$HOME"/AppData/Local/Programs/t3code/T3\ Code*.exe; do [ -f "$a" ] && { T3_APP="$a"; return 0; }; done ;;
  esac
  return 1
}
t3_ready() {   # nothing said without the marker or on an OS with no T3 Code surface; the marker without the app → one line
  case "$OS" in darwin*|msys*|cygwin*) ;; *) return 1 ;; esac
  [ -f "$T3_MARKER" ] || return 1
  find_t3_app && return 0
  say "T3 Code is not where setup put it — the terminal session instead; fuse-dm setup puts it back"
  return 1
}
# the opener's stderr is kept and shown only when it fails (an already-running T3 Code: open warns, exits 0)
open_t3() {   # sets ORC; the variable reaches the app (open's --env; cmd's start inherits it)
  oerr="${TMPDIR:-/tmp}/fuse-dm.$$.open.err"
  case "$OS" in
    msys*|cygwin*) T3CODE_TELEMETRY_ENABLED=false "${FUSE_DM_OPENER:-cmd}" //c start "" "$T3_APP" 2>"$oerr"; ORC=$? ;;
    *) "${FUSE_DM_OPENER:-open}" --env T3CODE_TELEMETRY_ENABLED=false -a "$T3_APP" 2>"$oerr"; ORC=$? ;;
  esac
  if [ "$ORC" -ne 0 ] && [ -s "$oerr" ]; then printf '%s\n' "$(< "$oerr")" >&2; fi
  rm -f "$oerr"
}
# The window close (ruling C): macOS, TERM_PROGRAM=Apple_Terminal, and `tty` answers. osascript in the background (HUP
# ignored, stdio on /dev/null) gets the launcher's tty as argv — never inside the script text — and checks up to three
# times, one second apart: the window whose ONE tab has that tty and no process left is closed. A window with other
# tabs, or a tab whose shell lives on (the DM typed fuse-dm), is left open. Terminal scripting Terminal asks nothing.
close_terminal_window() {
  case "$OS" in darwin*) ;; *) return 0 ;; esac
  [ "${TERM_PROGRAM:-}" = Apple_Terminal ] || return 0
  t="$(tty 2>/dev/null)" || return 0
  case "$t" in /dev/*) ;; *) return 0 ;; esac
  ( trap '' HUP
    osascript -e 'on run argv' -e 'set ttyName to item 1 of argv' -e 'repeat 3 times' -e 'delay 1' \
      -e 'tell application "Terminal"' -e 'repeat with w in windows' \
      -e 'if (count of tabs of w) is 1 and tty of tab 1 of w is ttyName then' \
      -e 'if (count of (processes of tab 1 of w)) is 0 then' -e 'close w' -e 'return' -e 'end if' \
      -e 'end if' -e 'end repeat' -e 'end tell' -e 'end repeat' -e 'end run' "$t"
  ) </dev/null >/dev/null 2>&1 &
}

# ---- the terminal session (`term`, and every fallback): the keep-current check before the launch, the record after a
# clean exit ----
term_session() {
  keep_current_check
  launch daily
  [ "$RC" -eq 0 ] && record_version
  exit "$RC"
}
# ---- the surface, after need_claude (and update's two lines): FUSE_DM_SURFACE=term or not ready → the terminal
# session; ready → the keep-current check, a moved version's headless pass, the record, then T3 Code ----
open_the_day() {
  [ "${FUSE_DM_SURFACE:-}" = term ] && term_session
  t3_ready || term_session
  ON_T3=1
  keep_current_check
  if [ "$KEEP_CURRENT" = 1 ]; then
    launch pass
    if [ "$RC" -ne 0 ]; then    # ruling G: nothing recorded; the pass again, interactive, in the terminal
      say "the re-check stopped (exit $RC) — opening it in the terminal instead"
      launch daily
      [ "$RC" -eq 0 ] && record_version
      exit "$RC"
    fi
  fi
  record_version                # after the clean pass, or no record yet (ruling I) — always before the opener
  open_t3
  if [ "$ORC" -ne 0 ]; then
    say "T3 Code did not open (exit $ORC) — the terminal session instead"
    KEEP_CURRENT=0              # a pass that ran is recorded: never a second one
    launch daily
    [ "$RC" -eq 0 ] && record_version
    exit "$RC"
  fi
  say "opening T3 Code"
  close_terminal_window
  exit 0
}
do_term() {
  need_claude
  term_session
}
do_daily() {
  need_claude
  open_the_day
}
do_update() {
  need_claude
  "$CLAUDE" plugin marketplace update fuse-internal && "$CLAUDE" plugin update deployment-manager@fuse-internal \
    || say "the plugin update did not go through — the session opens on the copy you have; run fuse-dm update again later"
  open_the_day
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
  term) do_term ;;
  install-shim) do_install_shim ;;
  -h|--help) help; exit 0 ;;
  *) echo "$USAGE" >&2; die "unknown verb ${1}" 64 ;;
esac
