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
# T3 Code*.exe in ~/AppData/Local/Programs/t3code); any other OS is never ready. Ready → a moved version runs a pass
# headless (`-p` right before the prompt, stdin from /dev/null, the same opus retry), which closes itself. Bare
# `fuse-dm` (OPX-2020): the quick check, "/deployment-manager:setup keep-current quick" with `--effort low`; its stdout
# and stderr go to ~/.fuse/dm-setup/last-pass.log (rewritten, one header line first), the window shows only its first
# three RESULT: lines (or one line when there is none, or when it stopped), held FUSE_DM_HOLD (10) with sleep, no key
# read; a stopped quick check records nothing and T3 Code opens anyway. `update`: the full keep-current pass, its output
# in the window; it failing → the terminal session on that prompt instead. Then the record, then the opener (`open --env
# T3CODE_TELEMETRY_ENABLED=false -a`, Git Bash `cmd //c start ""`); it failing → the terminal session. In macOS
# Terminal, right before exit 0, a background osascript closes the launcher's own window once its one tab has no
# process left, so a DM's own shell is never closed. FUSE_DM_SURFACE=term, no marker, or no app → the terminal session.
# A running T3 Code is never restarted or quit; nothing here installs it or writes its settings.
# `fuse-dm install-shim` is setup's row 14: the shim at ~/.fuse/bin/fuse-dm
# (which execs the installed plugin's copy of this file, so a plugin update needs no re-install) and the Desktop icon.
# Claude Code's folder (OPX-2024) is one rule, the claude-config-dir block below, carried byte for byte by the shim,
# bin/fuse-live and the sync launcher: CLAUDE_CONFIG_DIR, else the folder ~/.fuse/dm-setup/claude-config-dir names (the
# record install-shim writes when that variable is set), else ~/.claude; a folder the record decided is exported to
# every claude started here. `install-shim --check` (row 14's detection) exits 0 only when the shim, the icon and the
# record are current, and writes nothing.
# The same file is published as dm.sh in FuseFinance/dm-start (OPX-1332): the one line `curl -fsSL …/dm.sh | bash`
# reaches it with no argv and an EMPTY BASH_SOURCE — bash read it from stdin, not from a file — and that case is setup,
# with stdin re-opened as a dup of the launcher's OWN terminal fd (`exec 0<&1`, else `0<&2`) because claude is a TUI and
# stdin is curl's pipe; /dev/tty is the last resort only, never the first: on macOS /dev/tty is the controlling-terminal
# alias device and kqueue rejects it (EINVAL), and Claude Code runs on Bun, which registers stdin with kqueue as it is —
# Node's libuv re-opens a tty through ttyname, Bun does not — so `exec < /dev/tty` killed part one at its first stdin
# pull, before the theme picker (OPX-1373). Bare `fuse-dm` from a file stays the daily verb.
# bash 3.2 (macOS /bin/bash) and Git Bash run the same file — the bin/fuse-live shape: die/say, seams as FUSE_DM_*
# environment only (--help), externals kept to mkdir, tee, awk, chmod, rm; on the T3 Code path the opener (open on
# macOS, cmd under Git Bash) and sleep (the quick check's hold), and in macOS Terminal only tty and osascript (the
# AppleScript's own delay waits, never a shell loop). Never a settings file, never a default mode.
# Exit: 0 · 64 usage · 69 a prerequisite is missing · 70 a write failed · else claude's own code.
set -u -o pipefail

USAGE='usage: fuse-dm [setup | update | term | install-shim | --help]'
die() { echo "fuse-dm: $1" >&2; exit "$2"; }
say() { echo "fuse-dm: $1"; }

# >>> claude-config-dir — OPX-2024: the launchers carry this block byte for byte (tests pin the copies equal)
claude_config_dir() {   # CLAUDE_DIR: the env (~ expanded), else the folder install-shim recorded, else ~/.claude
  CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-}"; CLAUDE_DIR_REC=0
  if [ -z "$CLAUDE_DIR" ] && [ -f "$HOME/.fuse/dm-setup/claude-config-dir" ]; then
    read -r CLAUDE_DIR < "$HOME/.fuse/dm-setup/claude-config-dir"; CLAUDE_DIR_REC=1
  fi
  case "$CLAUDE_DIR" in "~"|"~/"*) CLAUDE_DIR="$HOME${CLAUDE_DIR#?}" ;; esac
  [ "$CLAUDE_DIR_REC" = 0 ] || [ -d "$CLAUDE_DIR" ] || CLAUDE_DIR=""
  [ -n "$CLAUDE_DIR" ] || { CLAUDE_DIR="$HOME/.claude"; CLAUDE_DIR_REC=0; }
}
# <<< claude-config-dir

claude_config_dir   # read by INSTALLED_PLUGINS below and by need_claude's export

MODEL="${FUSE_DM_MODEL:-fable}"
EFFORT="${FUSE_DM_EFFORT:-medium}"
BOOTSTRAP_URL="${FUSE_DM_BOOTSTRAP_URL:-https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip}"
OS="${FUSE_DM_OSTYPE:-${OSTYPE:-}}"
ROOT_DIR="$HOME/Fuse"
STATE_FILE="$HOME/.fuse/dm-setup/state"      # read only: the bootstrap plugin and setup write it
VERSION_FILE="$HOME/.fuse/dm-setup/plugin-version"   # the launcher's own: the plugin version it last ran a pass for
INSTALLED_PLUGINS="$CLAUDE_DIR/plugins/installed_plugins.json"
CONFIG_RECORD="$HOME/.fuse/dm-setup/claude-config-dir"   # install-shim's: the Claude folder, when CLAUDE_CONFIG_DIR is set
KEEP_CURRENT_PROMPT="/deployment-manager:setup keep-current"
SHIM="$HOME/.fuse/bin/fuse-dm"
RETRY_MODEL="opus"
T3_MARKER="$HOME/.fuse/dm-setup/t3-ready"     # read only: setup's row 15 (t3_code.py settings) writes it
QUICK_PROMPT="/deployment-manager:setup keep-current quick"   # OPX-2020: the icon's quick check, setup's quick case
PASS_LOG="$HOME/.fuse/dm-setup/last-pass.log"   # the quick check's whole output, rewritten on each one
HOLD="${FUSE_DM_HOLD:-10}"; case "$HOLD" in *[!0-9]*) HOLD=10 ;; esac   # the hold after it: digits only, else 10
QUICK=0                                       # 1 on the bare verb only (do_daily): its moved-version pass is the quick check

help() {
  printf '%s\n' "$USAGE" '' \
    '  setup         install or finish the Fuse environment: part one (the bootstrap plugin) when deployment-manager is' \
    '                not installed, part two (/deployment-manager:setup) when it is; another pass follows when' \
    '                ~/.fuse/dm-setup/state says so (bootstrap-done, needs-second-pass); at most three launches' \
    '  (none)        the Desktop icon: the keep-current check, then T3 Code when setup set it up (~/.fuse/dm-setup/t3-ready' \
    '                and the app found) — a moved version gets the quick check first (claude -p, setup keep-current quick,' \
    '                under a minute, nothing asked): its whole output in ~/.fuse/dm-setup/last-pass.log, its result lines' \
    '                held on screen (FUSE_DM_HOLD), then T3 Code opens, even when the check stopped, and in macOS Terminal' \
    '                this window closes; otherwise the term session' \
    '  term          the daily session in the terminal: claude on Fable, auto mode, in ~/Fuse — prompts on, no bypass; when' \
    '                the installed deployment-manager version differs from ~/.fuse/dm-setup/plugin-version (the one the last' \
    '                pass ran for), the session opens on /deployment-manager:setup keep-current — every row re-checked —' \
    '                and the record is rewritten after a clean exit; no record yet → the daily session, the version' \
    '                recorded' \
    '  update        claude plugin marketplace update fuse-internal, claude plugin update deployment-manager@fuse-internal,' \
    '                then what the bare verb opens, with the same keep-current check — on T3 Code a moved version gets' \
    '                the full keep-current pass headless, its output in this window, not the quick check' \
    '  install-shim  write ~/.fuse/bin/fuse-dm (execs the installed plugin'"'"'s bin/fuse-dm, installPath read from' \
    '                installed_plugins.json on every call), the Desktop icon Fuse Claude.command / Fuse Claude.cmd and,' \
    '                when CLAUDE_CONFIG_DIR is set, ~/.fuse/dm-setup/claude-config-dir naming that folder (removed when' \
    '                unset); install-shim --check: exit 0 when all three are current, else 1, writing nothing' \
    '' \
    'environment   FUSE_DM_MODEL (fable) · FUSE_DM_EFFORT (medium, the setup session only)' \
    '              FUSE_DM_BOOTSTRAP_URL (the fuse-start.zip release the --plugin-url of part one points at)' \
    '              FUSE_DM_INSTALLER (the command run when claude is missing; default: the official installer line)' \
    '              FUSE_DM_OSTYPE (the icon kind: darwin* → .command, msys*/cygwin* → .cmd)' \
    '              FUSE_DM_SURFACE (term → the bare verb and update take the terminal session, T3 Code ready or not)' \
    '              FUSE_DM_T3_APP (the T3 Code app'"'"'s path; default: the first ~/Applications/T3 Code*.app, then' \
    '              /Applications; Git Bash: ~/AppData/Local/Programs/t3code/T3 Code*.exe)' \
    '              FUSE_DM_OPENER (the command that opens it; default: open on macOS, cmd under Git Bash)' \
    '              FUSE_DM_HOLD (10: how long the quick check'"'"'s result lines stay before T3 Code opens; digits only)' \
    '              CLAUDE_CONFIG_DIR (Claude Code'"'"'s folder; unset: the one install-shim recorded, else ~/.claude)'
}

# ---- claude, by absolute path: ~/.local/bin first, then PATH; missing → the official installer runs here (one line
# said first), then the resolution again; 69 only when claude is still missing. Never a line for the DM to paste.
# FUSE_DM_INSTALLER replaces the installer command (the tests' stub; the real one is never run by a test). Every verb
# that starts claude comes through here first: a folder the record decided is exported now (OPX-2024), so each claude
# opens the DM's Fuse folder; CLAUDE_CONFIG_DIR set, or ~/.claude, leaves the environment as it is. ----
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
  [ "$CLAUDE_DIR_REC" = 0 ] || export CLAUDE_CONFIG_DIR="$CLAUDE_DIR"
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
# opens after: the quick check's on the bare verb, QUICK=1); the record is written only after a clean exit, and RECORD
# follows it so a second call writes nothing. ----
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
    if [ "$ON_T3" = 1 ] && [ "$QUICK" = 1 ]; then
      say "the plugin moved to $INSTALLED — a quick check first, under a minute; then T3 Code opens by itself (threads already open there keep the old version until you start a new one)"
    elif [ "$ON_T3" = 1 ]; then
      say "the plugin moved to $INSTALLED — re-checking the environment first, then T3 Code opens by itself (threads already open there keep the old version until you start a new one)"
    else
      say "the plugin moved to $INSTALLED — re-checking the environment first"
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
# `quick` (OPX-2020) is the same with `--effort low` and the quick prompt; its stdout goes to LOGF and its stderr to the
# refusal file and LOGF, never to the window.
RETRIED=0; LOGF=/dev/null
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
  elif [ "$kind" = quick ]; then
    set -- --model "$MODEL" --permission-mode auto --effort low -p "$QUICK_PROMPT"
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
  elif [ "$kind" = quick ]; then
    { "$CLAUDE" "$@" < /dev/null 2>&1 1>&3 | tee "$errf" >&3; RC=${PIPESTATUS[0]}; } 3>>"$LOGF"
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
# ---- the quick check (OPX-2020), the bare verb's moved-version pass on the T3 Code path: the log truncated and its
# header written once, before the first attempt (a refusal retry appends); a log that cannot be written → /dev/null, the
# pass runs all the same. Then its result lines, or the stopped line (PASSED=0: nothing recorded), then the hold. ----
quick_check() {
  LOGF="$PASS_LOG"
  { mkdir -p "${PASS_LOG%/*}" && printf '%s\n' "fuse-dm: quick check for deployment-manager $INSTALLED" > "$PASS_LOG"; } 2>/dev/null \
    || LOGF=/dev/null
  launch quick
  if [ "$RC" -ne 0 ]; then
    PASSED=0
    say "the quick check stopped (exit $RC) — fuse-dm setup runs the full check; details: ~/.fuse/dm-setup/last-pass.log"
  else
    show_result
  fi
  sleep "$HOLD"                 # the lines stay on screen; no key is read
}
# the log's first three RESULT: lines, their leading decoration (spaces, >, *, -, backticks: a fence or a list the model
# wrapped them in) and the prefix off, the last one naming the log; none → one line that says so
show_result() {
  res="$(awk -v sfx=" — details: ~/.fuse/dm-setup/last-pass.log" '/^[[:space:]>*`-]*RESULT:/ {
      sub(/^[[:space:]>*`-]*RESULT:[[:space:]]*/, ""); l[++n] = $0; if (n == 3) exit }
    END { for (i = 1; i <= n; i++) { s = l[i]; if (i == n) s = s sfx; print s } }' "$LOGF" 2>/dev/null)"
  if [ -z "$res" ]; then
    say "the quick check left no result — details: ~/.fuse/dm-setup/last-pass.log"
  else
    printf '%s\n' "$res" | while IFS= read -r l; do say "$l"; done
  fi
}
# ---- the surface, after need_claude (and update's two lines): FUSE_DM_SURFACE=term or not ready → the terminal
# session; ready → the keep-current check, a moved version's headless pass (the bare verb's quick check, update's full
# pass), the record after a clean one, then T3 Code ----
open_the_day() {
  [ "${FUSE_DM_SURFACE:-}" = term ] && term_session
  t3_ready || term_session
  ON_T3=1
  keep_current_check
  PASSED=1
  if [ "$KEEP_CURRENT" = 1 ] && [ "$QUICK" = 1 ]; then
    quick_check                 # decision D: stopped or not, T3 Code opens next
  elif [ "$KEEP_CURRENT" = 1 ]; then
    launch pass
    if [ "$RC" -ne 0 ]; then    # ruling G (update's alone): nothing recorded; the pass again, interactive, in the terminal
      say "the re-check stopped (exit $RC) — opening it in the terminal instead"
      launch daily
      [ "$RC" -eq 0 ] && record_version
      exit "$RC"
    fi
  fi
  [ "$PASSED" = 1 ] && record_version   # after the clean pass, or no record yet (ruling I) — always before the opener
  open_t3
  if [ "$ORC" -ne 0 ]; then
    say "T3 Code did not open (exit $ORC) — the terminal session instead"
    [ "$PASSED" = 1 ] && KEEP_CURRENT=0   # a pass that ran is recorded: never a second one (a stopped quick check is not)
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
  QUICK=1                       # the icon's moved-version pass is the quick check; update keeps the full one
  open_the_day
}
do_update() {
  need_claude
  "$CLAUDE" plugin marketplace update fuse-internal && "$CLAUDE" plugin update deployment-manager@fuse-internal \
    || say "the plugin update did not go through — the session opens on the copy you have; run fuse-dm update again later"
  open_the_day
}

# ---- install-shim: setup's row 14 — the shim, the Desktop icon and (OPX-2024) the record of the Claude folder: the
# CLAUDE_CONFIG_DIR folder (~ expanded) when the variable is set here, no record when it is not. `--check` is row 14's
# detection: 0 only when all three are what install-shim writes now — bytes compared with builtin read, never cmp —
# else 1; it writes nothing. ----
shim_body() {
  while IFS= read -r l; do printf '%s\n' "$l"; done <<'EOT'
#!/usr/bin/env bash
# fuse-dm — the shim. Execs the installed deployment-manager plugin's bin/fuse-dm, resolving the plugin's installPath
# from installed_plugins.json in Claude Code's folder on every call, so a plugin update changes nothing here.
# >>> claude-config-dir — OPX-2024: the launchers carry this block byte for byte (tests pin the copies equal)
claude_config_dir() {   # CLAUDE_DIR: the env (~ expanded), else the folder install-shim recorded, else ~/.claude
  CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-}"; CLAUDE_DIR_REC=0
  if [ -z "$CLAUDE_DIR" ] && [ -f "$HOME/.fuse/dm-setup/claude-config-dir" ]; then
    read -r CLAUDE_DIR < "$HOME/.fuse/dm-setup/claude-config-dir"; CLAUDE_DIR_REC=1
  fi
  case "$CLAUDE_DIR" in "~"|"~/"*) CLAUDE_DIR="$HOME${CLAUDE_DIR#?}" ;; esac
  [ "$CLAUDE_DIR_REC" = 0 ] || [ -d "$CLAUDE_DIR" ] || CLAUDE_DIR=""
  [ -n "$CLAUDE_DIR" ] || { CLAUDE_DIR="$HOME/.claude"; CLAUDE_DIR_REC=0; }
}
# <<< claude-config-dir
claude_config_dir
root="$(awk '/"deployment-manager@fuse-internal"/{f=1} f&&/"installPath"/{sub(/.*"installPath"[[:space:]]*:[[:space:]]*"/,""); sub(/".*/,""); print; exit}' "$CLAUDE_DIR/plugins/installed_plugins.json" 2>/dev/null)"
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
icon_path() {   # sets ICON: on ~/Desktop, or ~/OneDrive/Desktop where Known Folder Move took it; .cmd under Git Bash
  desk="$HOME/Desktop"
  [ -d "$desk" ] || { [ -d "$HOME/OneDrive/Desktop" ] && desk="$HOME/OneDrive/Desktop"; }
  case "$OS" in msys*|cygwin*) ICON="$desk/Fuse Claude.cmd" ;; *) ICON="$desk/Fuse Claude.command" ;; esac
}
icon_body() { case "$OS" in msys*|cygwin*) icon_cmd_body ;; *) icon_command_body ;; esac; }
record_body() { claude_config_dir; printf '%s\n' "$CLAUDE_DIR"; }   # only while CLAUDE_CONFIG_DIR is set
same() {   # same <file> <function>: the file holds exactly what the function prints, trailing newlines included
  [ -f "$1" ] || return 1
  have=""; want=""
  IFS= read -r -d '' have 2>/dev/null < "$1"
  IFS= read -r -d '' want < <("$2")
  [ "$have" = "$want" ]
}
check_shim() {
  icon_path
  [ -x "$SHIM" ] && same "$SHIM" shim_body && same "$ICON" icon_body || return 1
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then same "$CONFIG_RECORD" record_body; else [ ! -e "$CONFIG_RECORD" ]; fi
}
do_install_shim() {
  mkdir -p "${SHIM%/*}" || die "cannot create ${SHIM%/*}" 70
  shim_body > "$SHIM" || die "cannot write $SHIM" 70
  chmod +x "$SHIM"
  say "installed $SHIM"
  icon_path
  mkdir -p "${ICON%/*}" || die "cannot create ${ICON%/*}" 70
  icon_body > "$ICON" || die "cannot write $ICON" 70
  case "$OS" in msys*|cygwin*) ;; *) chmod +x "$ICON" ;; esac
  say "installed $ICON"
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    { mkdir -p "${CONFIG_RECORD%/*}" && record_body > "$CONFIG_RECORD"; } || die "cannot write $CONFIG_RECORD" 70
    say "the Claude folder $CLAUDE_DIR recorded in $CONFIG_RECORD"
  else
    rm -f "$CONFIG_RECORD"
  fi
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
  install-shim) case "${2:-}" in
      "") do_install_shim ;;
      --check) check_shim; exit $? ;;
      *) echo "$USAGE" >&2; die "install-shim takes only --check" 64 ;;
    esac ;;
  -h|--help) help; exit 0 ;;
  *) echo "$USAGE" >&2; die "unknown verb ${1}" 64 ;;
esac
