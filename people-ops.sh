#!/usr/bin/env bash
# people-ops.sh — the one line a Fuse People Ops teammate pastes into Terminal on a new Mac, once:
#
#   curl -fsSL https://raw.githubusercontent.com/FuseFinance/dm-start/main/people-ops.sh | bash
#
# It installs Claude Code when the Mac has none (the official installer, no administrator password), then opens one
# Claude session in ~/Fuse/studio with this repository's fuse-start plugin loaded for that session only, running
# /fuse-start:people-ops (git, the GitHub CLI and Node without a password, one GitHub sign-in, the access check, the
# people-ops plugin from the private marketplace). After that the person works in the Claude app (Code), where
# /people-ops:studio-setup finishes the Mac. bash 3.2. Test seams, environment only: FUSE_PO_CLAUDE (the claude to run),
# FUSE_PO_PLATFORM (instead of uname -s), FUSE_PO_BOOTSTRAP_URL (the fuse-start.zip), FUSE_PO_INSTALLER (the install line).
# Exit: 0 · 69 not a Mac, or no Claude Code · 70 a folder could not be made · else claude's own code.
set -u -o pipefail

BOOTSTRAP_URL="${FUSE_PO_BOOTSTRAP_URL:-https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip}"
say() { echo "people-ops: $1" >&2; }

find_claude() {
  CLAUDE="${FUSE_PO_CLAUDE:-}"
  [ -n "$CLAUDE" ] || CLAUDE="$(command -v claude 2>/dev/null || true)"
  if [ -z "$CLAUDE" ] && [ -x "$HOME/.local/bin/claude" ]; then CLAUDE="$HOME/.local/bin/claude"; fi
}

# One Claude session on the given model; stderr is tee'd through a file so a refused model can be told from any other exit.
session() {
  { "$CLAUDE" --model "$1" --effort medium --dangerously-skip-permissions --plugin-url "$BOOTSTRAP_URL" "/fuse-start:people-ops" 2>&1 1>&3 | tee "$2" >&2; return "${PIPESTATUS[0]}"; } 3>&1
}

main() {
  if [ "${FUSE_PO_PLATFORM:-$(uname -s)}" != "Darwin" ]; then say "People Ops Studio runs on a Mac; ask the person onboarding you to the studio"; return 69; fi
  find_claude
  if [ -z "$CLAUDE" ]; then
    say "installing Claude Code — one minute"
    bash -c "${FUSE_PO_INSTALLER:-curl -fsSL https://claude.ai/install.sh | bash}" || say "the installer did not finish cleanly"
    CLAUDE="$HOME/.local/bin/claude"
  fi
  [ -x "$CLAUDE" ] || { say "Claude Code did not install; ask the person onboarding you to the studio"; return 69; }
  { mkdir -p "$HOME/Fuse/studio" && cd "$HOME/Fuse/studio"; } || { say "cannot open ~/Fuse/studio"; return 70; }
  say "three things Claude asks the first time, then never again: a colour theme (press Enter), a sign-in (pick your @fusefinance.com Google account), a permissions warning (answer Yes)"
  errf="$(mktemp "${TMPDIR:-/tmp}/people-ops.XXXXXX")"
  session fable "$errf"; rc=$?
  if [ "$rc" -ne 0 ] && grep -qi 'fable' "$errf"; then
    say "this seat refused the model fable — starting again on opus"
    session opus "$errf"; rc=$?
  fi
  rm -f "$errf"
  if [ "$rc" -eq 0 ]; then
    say "next: open the Claude app (if it isn't on your Mac yet, download it from claude.ai/download and sign in), choose Code, start a new session in the folder Fuse -> studio in your home folder, and type /people-ops:studio-setup"
  else
    say "the session ended with code $rc — paste the same line again to pick up where it stopped; if it keeps failing, ask the person onboarding you to the studio"
  fi
  return "$rc"
}

# `curl … | bash` reads this file from stdin, and claude is a TUI: give it the terminal's own fd, in the DM launcher's
# order (fd 1, then fd 2, /dev/tty last — Bun's kqueue rejects /dev/tty on macOS). The exec and main stay in one
# compound, so bash never reads another line of this script from the keyboard.
if true; then
  if [ -t 1 ]; then exec 0<&1
  elif [ -t 2 ]; then exec 0<&2
  elif ( : < /dev/tty ) 2>/dev/null; then exec < /dev/tty
  fi
  main
  exit $?
fi
