"""Behaviour tests for bin/fuse-dm (OPX-1331): the real script under /bin/bash (3.2 on this Mac) with a fake `claude` in
a stub bin that is FIRST and ONLY on PATH (the five coreutils the script needs are linked in beside it, so the suite
also pins that dependency set), a temp HOME, nothing of the machine's. The fake records every invocation as
$FAKE_DIR/launch.<n> (argv0, one arg= line per argument, the cwd, the env it saw, and what stdin is — a terminal or
not, the /dev/tty alias device or not, OPX-1373; /dev/null or not, OPX-1890), answers `plugin list` from
$FAKE_PLUGIN_LIST, writes the state file when FAKE_CLAUDE_WRITE_STATE tells it to (the nth comma-separated value on the
nth session launch) and exits with FAKE_CLAUDE_EXIT's nth value, FAKE_CLAUDE_STDERR on stderr — so the relaunch loop,
the stage choice and the opus retry are all driven from the outside. OPX-2020: every session call also prints
FAKE_CLAUDE_STDOUT on stdout (the quick pass's reply), and the T3 Code classes swap `sleep` (the hold) for a fake that
records its argv and returns at once. The real `claude` is never executed.

Run: python3 deployment-manager/setup/tests/test_launcher.py
"""
from pathlib import Path
import json
import os
import pty
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

# OPX-1332 (D-b): this file also runs inside the public repo's tree (<checkout>/tests/test_launcher.py beside
# <checkout>/scripts/testlib/), so the root is the first parent that holds scripts/testlib/fake_exec.py, not a fixed depth.
REPO = next(p for p in Path(__file__).resolve().parents if (p / "scripts" / "testlib" / "fake_exec.py").is_file())
sys.path.insert(0, str(REPO / "scripts"))
from testlib.fake_exec import write_exec  # noqa: E402

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
PLUGIN_DIR = SKILL_DIR.parent
# OPX-1332 (D-b): FUSE_DM_SCRIPT points the same suite at another copy of the script — the public repo's CI runs it
# against dm.sh, which the publish script copies from bin/fuse-dm byte for byte.
SCRIPT = Path(os.environ.get("FUSE_DM_SCRIPT") or PLUGIN_DIR / "bin" / "fuse-dm").resolve()
# OPX-1339: the Windows entry point beside it — dm.ps1 beside dm.sh in the public tree, bin/fuse-dm.ps1 here. Its
# behaviour suite is Pester (fuse-dm.Tests.ps1); this file pins the two scripts' shared contract, text against text.
PS1 = SCRIPT.with_name("dm.ps1") if os.environ.get("FUSE_DM_SCRIPT") else PLUGIN_DIR / "bin" / "fuse-dm.ps1"
WINDOWS_MD = PLUGIN_DIR / "setup" / "references" / "windows.md"    # absent from the public tree: its pins skip there
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
# the installed fixture mirrors the checkout; the public tree (OPX-1332) has no manifest and the shim reads only installPath
PLUGIN_VERSION = json.loads(MANIFEST.read_text())["version"] if MANIFEST.is_file() else "unversioned"
BASH = "/bin/bash"
BOOTSTRAP_URL = "https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip"
BOOTSTRAP_DONE = "bootstrap-done"     # the state word the bootstrap plugin (fuse-start, OPX-1332) writes at the end of part one
SETUP_FLAGS = ["--model", "fable", "--effort", "medium", "--dangerously-skip-permissions"]
DAILY_ARGV = ["--model", "fable", "--permission-mode", "auto"]
PART_ONE_PROMPT = "/fuse-start:begin"
PART_TWO_PROMPT = "/deployment-manager:setup"
# OPX-1366 (S-6): the keep-current pass — setup's second pass, opened by the daily launch when the plugin version moved
KEEP_CURRENT_PROMPT = "/deployment-manager:setup keep-current"
# OPX-2020: the icon's quick pass — setup's quick case, the bare verb's moved-version pass on the T3 Code path (`update`
# keeps the full pass above): the daily argv plus `--effort low`, `-p` right before the prompt; its stdout and stderr go
# to the log, its RESULT: lines to the window, then the hold
QUICK_PROMPT = "/deployment-manager:setup keep-current quick"
QUICK_ARGV = DAILY_ARGV + ["--effort", "low", "-p", QUICK_PROMPT]
PASS_LOG = Path(".fuse") / "dm-setup" / "last-pass.log"
STATE = Path(".fuse") / "dm-setup" / "state"
RECORD = Path(".fuse") / "dm-setup" / "plugin-version"       # the launcher's own file: the version it last ran a pass for
INSTALLED_PLUGINS = Path(".claude") / "plugins" / "installed_plugins.json"
SHIM = Path(".fuse") / "bin" / "fuse-dm"
# OPX-2024: Claude Code's own folder is one rule in four copies — bin/fuse-dm's function, the shim body it writes,
# bin/fuse-live and the sync launcher — between these two marker lines, byte for byte: CLAUDE_CONFIG_DIR when set (a leading ~
# expanded), else the folder the record install-shim writes names (when it exists), else ~/.claude.
CONFIG_RECORD = Path(".fuse") / "dm-setup" / "claude-config-dir"
BLOCK = re.compile(r"(?ms)^# >>> claude-config-dir\b.*?^# <<< claude-config-dir$")
FUSE_LIVE = PLUGIN_DIR / "bin" / "fuse-live"     # absent from the public tree (dm-start): their pins skip there
FUSE_SYNC = PLUGIN_DIR / "bin" / ("fuse" + "-sync")   # built from two literals: the published tree never names it (Privacy scan)
FUSE_FOLDER = ".claude-fuse"                     # a DM's second Claude folder, beside a personal ~/.claude (Roberto's Mac)
# The externals the script may call (`bash` is what the shim execs the plugin's copy with). Anything else is a
# `command not found` under the bare PATH — the dependency set is part of the contract (Git Bash ships all of them).
TOOLS = ("bash", "mkdir", "tee", "awk", "chmod", "rm", "sleep")   # OPX-2020: + sleep, the hold after the quick pass

PLUGIN_LIST_ABSENT = "Installed plugins:\n\n  ❯ slack@claude-plugins-official\n    Version: 1.0.0\n    Scope: user\n    Status: ✔ enabled\n"
PLUGIN_LIST_PRESENT = PLUGIN_LIST_ABSENT + "\n  ❯ deployment-manager@fuse-internal\n    Version: 0.0.0\n    Scope: user\n    Status: ✔ enabled\n"

FAKE_CLAUDE = r'''#!/bin/sh
# fake claude (OPX-1331): see the module docstring. Only builtins and mkdir (linked into the stub bin).
n=0; [ -f "$FAKE_DIR/count" ] && read -r n < "$FAKE_DIR/count"; n=$((n+1)); echo "$n" > "$FAKE_DIR/count"
rec="$FAKE_DIR/launch.$n"
printf 'argv0=%s\n' "$0" > "$rec"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$rec"; done
printf 'cwd=%s\nlauncher=%s\neffort_env=%s\n' "$(pwd -P)" "${FUSE_DM_LAUNCHER:-unset}" "${CLAUDE_CODE_EFFORT_LEVEL:-unset}" >> "$rec"
# OPX-2024: the Claude folder it was started in — `unset` only when the variable is not in its environment at all
printf 'config_dir=%s\n' "${CLAUDE_CONFIG_DIR-unset}" >> "$rec"
# OPX-1373: what stdin is. `-ef` compares device+inode: an fd opened from /dev/tty matches /dev/tty, a dup of the
# terminal's own fd does not — and the alias device is the one Bun's kqueue refuses.
printf 'stdin_tty=%s\nstdin_is_dev_tty=%s\n' "$([ -t 0 ] && echo yes || echo no)" "$([ /dev/fd/0 -ef /dev/tty ] && echo yes || echo no)" >> "$rec"
printf 'stdin_is_dev_null=%s\n' "$([ /dev/fd/0 -ef /dev/null ] && echo yes || echo no)" >> "$rec"
case "${1:-} ${2:-}" in
  "plugin list") printf '%s\n' "${FAKE_PLUGIN_LIST:-}"; exit 0 ;;
  "plugin marketplace"|"plugin update") exit "${FAKE_UPDATE_RC:-0}" ;;
esac
s=0; [ -f "$FAKE_DIR/sessions" ] && read -r s < "$FAKE_DIR/sessions"; s=$((s+1)); echo "$s" > "$FAKE_DIR/sessions"
nth() { v="$1"; i="$2"; while [ "$i" -gt 1 ]; do case "$v" in *,*) v="${v#*,}" ;; *) v="" ;; esac; i=$((i-1)); done; printf '%s' "${v%%,*}"; }
st="$(nth "${FAKE_CLAUDE_WRITE_STATE:-}" "$s")"
if [ -n "$st" ]; then mkdir -p "$HOME/.fuse/dm-setup"; printf '%s\n' "$st" > "$HOME/.fuse/dm-setup/state"; fi
# OPX-2020: what the session prints on stdout (the quick pass's reply), on every session call
[ -n "${FAKE_CLAUDE_STDOUT:-}" ] && printf '%s\n' "$FAKE_CLAUDE_STDOUT"
rc="$(nth "${FAKE_CLAUDE_EXIT:-}" "$s")"; [ -n "$rc" ] || rc=0
[ "$rc" -ne 0 ] && printf '%s\n' "${FAKE_CLAUDE_STDERR:-claude: exit $rc}" >&2
exit "$rc"
'''
# The installer seam (review round 1): FUSE_DM_INSTALLER replaces the official `curl … | bash` line, so a test never
# reaches the network. Both stubs record that they ran; one writes the fake `claude` where the real installer puts it.
FAKE_INSTALLER_NOOP = r'''#!/bin/sh
echo ran >> "$FAKE_DIR/installer.txt"
exit 0
'''
FAKE_INSTALLER_INSTALLS = r'''#!/bin/sh
echo ran >> "$FAKE_DIR/installer.txt"
mkdir -p "$HOME/.local/bin" && printf '%s' "$FAKE_CLAUDE_BODY" > "$HOME/.local/bin/claude" && chmod +x "$HOME/.local/bin/claude"
'''
# A plugin's copy of bin/fuse-dm, as the shim would exec it: records its own path and argv, nothing else.
FAKE_PLUGIN_COPY = r'''#!/bin/sh
printf 'copy=%s\n' "$0" > "$FAKE_DIR/exec.txt"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$FAKE_DIR/exec.txt"; done
exit 0
'''
# OPX-1890: the icon opens T3 Code. The opener is an absolute path (FUSE_DM_OPENER) in place of `open` (macOS) or `cmd`
# (Git Bash); `osascript` and `tty` are written into the stub bin by T3Surface alone, so TOOLS stays the five plus bash
# and every other class runs with neither. Only builtins in all three.
T3_MARKER = Path(".fuse") / "dm-setup" / "t3-ready"       # setup's row 15 writes it (t3_code.py settings); the launcher only reads it
T3_APP = Path("Applications") / "T3 Code (Alpha).app"     # under $HOME: the cask's --appdir ~/Applications
USAGE = "usage: fuse-dm [setup | update | term | install-shim | --help]"
PS1_USAGE = "usage: fuse-dm.ps1 [setup | update | term | -Help]"
FAKE_OPENER = r'''#!/bin/sh
# fake opener (OPX-1890): records its argv, the T3CODE_TELEMETRY_ENABLED it saw, how many claude invocations came before
# it and the version record at that moment, as $FAKE_DIR/opener.<n>; writes FAKE_OPENER_STDERR to stderr when set (what
# macOS `open` says about an already-running app); exits FAKE_OPENER_EXIT (0 when unset).
n=0; [ -f "$FAKE_DIR/opener-count" ] && read -r n < "$FAKE_DIR/opener-count"; n=$((n+1)); echo "$n" > "$FAKE_DIR/opener-count"
rec="$FAKE_DIR/opener.$n"
: > "$rec"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$rec"; done
c=0; [ -f "$FAKE_DIR/count" ] && read -r c < "$FAKE_DIR/count"
v=none; [ -f "$HOME/.fuse/dm-setup/plugin-version" ] && read -r v < "$HOME/.fuse/dm-setup/plugin-version"
printf 'telemetry=%s\nclaude_calls=%s\nrecord=%s\n' "${T3CODE_TELEMETRY_ENABLED:-unset}" "$c" "$v" >> "$rec"
[ -n "${FAKE_OPENER_STDERR:-}" ] && printf '%s\n' "$FAKE_OPENER_STDERR" >&2
exit "${FAKE_OPENER_EXIT:-0}"
'''
FAKE_OSASCRIPT = r'''#!/bin/sh
# fake osascript (OPX-1890): records its argv as $FAKE_DIR/osascript.<n> and runs no AppleScript. `end=yes` is written
# last: the close runs in the background, so a reader polls for a complete record, never half of one.
n=0; [ -f "$FAKE_DIR/osascript-count" ] && read -r n < "$FAKE_DIR/osascript-count"; n=$((n+1)); echo "$n" > "$FAKE_DIR/osascript-count"
rec="$FAKE_DIR/osascript.$n"
: > "$rec"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$rec"; done
printf 'end=yes\n' >> "$rec"
exit 0
'''
FAKE_TTY = r'''#!/bin/sh
# fake tty (OPX-1890): the launcher's own terminal is /dev/ttys099; FAKE_NO_TTY set -> `not a tty` and exit 1, as tty does
if [ -n "${FAKE_NO_TTY:-}" ]; then echo "not a tty"; exit 1; fi
echo /dev/ttys099
'''
FAKE_SLEEP = r'''#!/bin/sh
# fake sleep (OPX-2020): the hold. Records its argv, the opener and claude calls made before it and the version record at
# that moment as $FAKE_DIR/sleep.<n>, and sleeps nothing. Only builtins.
n=0; [ -f "$FAKE_DIR/sleep-count" ] && read -r n < "$FAKE_DIR/sleep-count"; n=$((n+1)); echo "$n" > "$FAKE_DIR/sleep-count"
rec="$FAKE_DIR/sleep.$n"
: > "$rec"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$rec"; done
o=0; [ -f "$FAKE_DIR/opener-count" ] && read -r o < "$FAKE_DIR/opener-count"
c=0; [ -f "$FAKE_DIR/count" ] && read -r c < "$FAKE_DIR/count"
v=none; [ -f "$HOME/.fuse/dm-setup/plugin-version" ] && read -r v < "$HOME/.fuse/dm-setup/plugin-version"
printf 'opener_calls=%s\nclaude_calls=%s\nrecord=%s\n' "$o" "$c" "$v" >> "$rec"
exit 0
'''


class Drive(unittest.TestCase):
    """Every case drives the real script; the helpers build the stub bin and read the fake's records."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fuse-dm-")).resolve()
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        self.fake_dir = self.tmp / "fake"
        for d in (self.home, self.bin, self.fake_dir):
            d.mkdir(parents=True)
        write_exec(self.bin / "claude", FAKE_CLAUDE)
        self.installer_noop = self.tmp / "installer-noop.sh"
        self.installer_installs = self.tmp / "installer-installs.sh"
        write_exec(self.installer_noop, FAKE_INSTALLER_NOOP)
        write_exec(self.installer_installs, FAKE_INSTALLER_INSTALLS)
        for tool in TOOLS:
            real = shutil.which(tool, path="/usr/bin:/bin")
            self.assertIsNotNone(real, f"{tool} not under /usr/bin:/bin")
            (self.bin / tool).symlink_to(real)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        base = {"HOME": str(self.home), "PATH": str(self.bin), "FAKE_DIR": str(self.fake_dir), "TMPDIR": str(self.tmp),
                "FAKE_PLUGIN_LIST": PLUGIN_LIST_ABSENT, "FUSE_DM_OSTYPE": "darwin24", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                # never the real installer from a test: the no-op stub is the default, a test opts into the installing one
                "FUSE_DM_INSTALLER": str(self.installer_noop), "FAKE_CLAUDE_BODY": FAKE_CLAUDE,
                # OPX-2020: no hold unless a test asks for one (QuickResult drops it to read the default)
                "FUSE_DM_HOLD": "0"}
        for k, v in extra.items():
            if v is None:
                base.pop(k, None)
            else:
                base[k] = v
        return base

    def run_dm(self, *args, script=None, **extra):
        return subprocess.run([BASH, str(script or SCRIPT), *args], env=self.env(**extra), capture_output=True, text=True, timeout=60)

    def launches(self):
        """Every invocation of the fake, in order: {argv0, args, cwd, launcher, effort_env, config_dir, stdin_tty,
        stdin_is_dev_tty, stdin_is_dev_null}."""
        out = []
        for f in sorted(self.fake_dir.glob("launch.*"), key=lambda p: int(p.name.split(".")[1])):
            rec = {"args": []}
            for line in f.read_text().splitlines():
                k, v = line.split("=", 1)
                if k == "arg":
                    rec["args"].append(v)
                else:
                    rec[k] = v
            out.append(rec)
        return out

    def sessions(self):
        """The session launches only — the invocations carrying `--model` (never `plugin …`)."""
        return [l for l in self.launches() if "--model" in l["args"]]

    def state(self):
        f = self.home / STATE
        return f.read_text().strip() if f.exists() else None

    def installer_runs(self):
        f = self.fake_dir / "installer.txt"
        return len(f.read_text().splitlines()) if f.exists() else 0


class Launcher(Drive):
    """AC1: the stage from `claude plugin list`, the exact flag set per stage, FUSE_DM_LAUNCHER=1 and cwd ~/Fuse."""

    def test_stage_one_when_plugin_absent(self):
        r = self.run_dm("setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1, "one pass: the fake wrote no state, so the loop stops")
        self.assertEqual(s[0]["args"], SETUP_FLAGS + ["--plugin-url", BOOTSTRAP_URL, PART_ONE_PROMPT])
        self.assertEqual(self.launches()[0]["args"], ["plugin", "list"], "the stage is read from `claude plugin list` first")

    def test_stage_two_when_plugin_present(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["args"], SETUP_FLAGS + [PART_TWO_PROMPT])
        self.assertNotIn("--plugin-url", s[0]["args"])

    def test_setup_flag_set(self):
        """Exactly the three flags, in this order, on both stages — nothing a settings file would add."""
        for listing in (PLUGIN_LIST_ABSENT, PLUGIN_LIST_PRESENT):
            with self.subTest(plugin_present=listing is PLUGIN_LIST_PRESENT):
                shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
                self.run_dm("setup", FAKE_PLUGIN_LIST=listing)
                args = self.sessions()[0]["args"]
                self.assertEqual(args[:5], SETUP_FLAGS)
                self.assertEqual([a for a in args if a.startswith("--")], SETUP_FLAGS[0::2] + (["--plugin-url"] if listing is PLUGIN_LIST_ABSENT else []))

    def test_launcher_env_and_cwd(self):
        self.assertFalse((self.home / "Fuse").exists())
        r = self.run_dm("setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()[0]
        self.assertEqual(s["launcher"], "1", "the fake sees FUSE_DM_LAUNCHER=1: the silent case of setup's permissions check")
        self.assertEqual(s["effort_env"], "medium", "CLAUDE_CODE_EFFORT_LEVEL outranks --effort, as on fuse-live: both are set")
        self.assertEqual(Path(s["cwd"]).resolve(), (self.home / "Fuse").resolve(), "$PWD is ~/Fuse, created if absent")
        self.assertTrue((self.home / "Fuse").is_dir())

    def test_three_things_said_before_the_first_launch(self):
        r = self.run_dm("setup")
        out = r.stdout
        for needle in ("theme", "Enter", "@fusefinance.com", "Yes"):
            with self.subTest(needle=needle):
                self.assertIn(needle, out)

    def test_overrides_change_the_argv(self):
        r = self.run_dm("setup", FUSE_DM_MODEL="claude-opus-5", FUSE_DM_EFFORT="high", FUSE_DM_BOOTSTRAP_URL="https://example.test/x.zip")
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.sessions()[0]["args"]
        self.assertEqual(args, ["--model", "claude-opus-5", "--effort", "high", "--dangerously-skip-permissions",
                                "--plugin-url", "https://example.test/x.zip", PART_ONE_PROMPT])
        self.assertEqual(self.sessions()[0]["effort_env"], "high")

    def test_claude_resolved_by_absolute_path_home_local_bin_first(self):
        """The ticket's step 1: `~/.local/bin/claude` first, then PATH — never "open a new terminal"."""
        local = self.home / ".local" / "bin" / "claude"
        write_exec(local, FAKE_CLAUDE)
        r = self.run_dm("setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(self.sessions()[0]["argv0"]), local)
        local.unlink()
        shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
        self.run_dm("setup")
        self.assertEqual(Path(self.sessions()[0]["argv0"]), self.bin / "claude", "then the PATH copy, by its absolute path")

    def test_missing_claude_runs_the_installer_then_launches(self):
        """Ticket step 1 (review round 1, Important): missing → the official installer, resolve again — never a line for
        the DM to paste. The stub stands in for `curl … | bash` and lands the fake where the real installer puts it."""
        (self.bin / "claude").unlink()
        r = self.run_dm("setup", FUSE_DM_INSTALLER=str(self.installer_installs))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installer_runs(), 1)
        self.assertIn("installing Claude Code", r.stdout)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(Path(s[0]["argv0"]), self.home / ".local" / "bin" / "claude", "resolved again, by absolute path")
        self.assertEqual(s[0]["args"], SETUP_FLAGS + ["--plugin-url", BOOTSTRAP_URL, PART_ONE_PROMPT])

    def test_no_claude_after_the_installer_is_69(self):
        (self.bin / "claude").unlink()
        r = self.run_dm("setup")          # env()'s default: the no-op installer
        self.assertEqual(r.returncode, 69)
        self.assertEqual(self.installer_runs(), 1, "the installer ran once before giving up")
        self.assertIn("claude.ai/install", r.stderr)
        self.assertIn("still", r.stderr)
        self.assertEqual(self.launches(), [])

    def test_installer_never_runs_when_claude_is_present(self):
        for verb in ((), ("setup",), ("update",)):
            with self.subTest(verb=verb or ("daily",)):
                shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
                r = self.run_dm(*verb, FUSE_DM_INSTALLER=str(self.installer_installs))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.installer_runs(), 0)
                self.assertNotIn("installing Claude Code", r.stdout)

    def test_script_text_carries_the_official_installer_line_and_the_seam(self):
        text = SCRIPT.read_text()
        self.assertIn("curl -fsSL https://claude.ai/install.sh | bash", text)
        self.assertIn("FUSE_DM_INSTALLER", text)

    def test_no_bashisms_past_3_2(self):
        """AC6: macOS ships bash 3.2 and Git Bash runs the same file."""
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), text[:30])
        for pattern in (r"declare -A", r"\$\{[A-Za-z_]+,,\}", r"\$\{[A-Za-z_]+\^\^\}", r"\bmapfile\b", r"\breadarray\b",
                        r"\[\[[^\]]*=~[^\]]*\$[A-Za-z_{]", r"&>", r"\|&", r"local -n"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), pattern)
        r = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR, "bin/fuse-dm carries the executable bit")

    def test_help_on_the_bare_path_names_the_verbs_and_the_overrides(self):
        """AC6's bare PATH: --help needs no external at all (the stub bin holds claude and the linked tools, nothing else)."""
        r = self.run_dm("--help", PATH=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("setup", "update", "install-shim", "FUSE_DM_MODEL", "FUSE_DM_EFFORT", "FUSE_DM_BOOTSTRAP_URL", "FUSE_DM_INSTALLER", "~/Fuse"):
            with self.subTest(token=token):
                self.assertIn(token, r.stdout)

    def test_unknown_verb_is_64(self):
        r = self.run_dm("frobnicate")
        self.assertEqual(r.returncode, 64)
        self.assertIn("usage: fuse-dm", r.stderr)
        self.assertEqual(self.launches(), [])

    def test_piped_stub_with_no_argv_runs_setup(self):
        """OPX-1332 (D-a): the one-line stub `curl -fsSL …/dm.sh | bash` reaches this file with no argv and an empty
        BASH_SOURCE (bash read it from stdin). That is setup, part one — never the daily session."""
        with open(SCRIPT) as script:
            r = subprocess.run([BASH], stdin=script, env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["args"], SETUP_FLAGS + ["--plugin-url", BOOTSTRAP_URL, PART_ONE_PROMPT])
        self.assertEqual(s[0]["launcher"], "1")
        self.assertEqual(self.launches()[0]["args"], ["plugin", "list"], "the stage is read from `claude plugin list` first")
        self.assertIn("BASH_SOURCE", SCRIPT.read_text(), "the header says why: BASH_SOURCE is empty when bash reads the script from stdin")

    def test_piped_stub_hands_claude_the_terminal_not_dev_tty(self):
        """OPX-1373: `curl -fsSL …/dm.sh | bash` typed into Terminal.app — bash reads the script from a pipe (fd 0 is
        not a terminal) while the process keeps its controlling terminal on fd 1/fd 2. claude is a TUI, so the stub
        re-opens stdin; it must re-open it as a dup of the launcher's OWN terminal fd, never on /dev/tty. On macOS
        /dev/tty is the controlling-terminal alias device and kqueue rejects it (EINVAL); Claude Code runs on Bun,
        which registers stdin with kqueue as it is — Node's libuv re-opens a tty through ttyname, Bun does not — so
        `exec < /dev/tty` killed part one at its first stdin pull, before the theme picker (the first fresh-account run,
        2026-09-18). A pty gives the child a real controlling terminal; the script on fd 0 stands in for curl's pipe."""
        # Everything the child needs is built BEFORE the fork — under xdist this process has threads, and a forked
        # child that allocates can deadlock. The child only dup2/close/execve, and _exit rather than ever returning
        # into unittest (a child that returned would run the whole suite a second time).
        env, script_fd = self.env(), os.open(SCRIPT, os.O_RDONLY)
        try:
            pid, master = pty.fork()
            if pid == 0:
                try:
                    os.dup2(script_fd, 0)                  # bash reads the script from a non-tty fd 0, as from the pipe
                    os.close(script_fd)
                    os.execve(BASH, [BASH], env)
                except BaseException:
                    os._exit(127)
        finally:
            os.close(script_fd)                            # the parent's copy; the child kept its own across the fork
        chunks, finished, deadline = [], False, time.monotonic() + 60
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([master], [], [], left)[0]:
                break
            try:
                chunk = os.read(master, 4096)
            except OSError:                                # EIO on macOS: the slave side is gone
                finished = True
                break
            if not chunk:                                  # EOF
                finished = True
                break
            chunks.append(chunk)
        os.close(master)
        if not finished:
            os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        term = b"".join(chunks).decode(errors="replace")
        self.assertTrue(finished, f"the stub did not finish inside 60 s — the terminal said:\n{term}")
        self.assertEqual(status, 0, f"exit status {status} — the terminal said:\n{term}")
        s = self.sessions()
        self.assertEqual(len(s), 1, term)
        self.assertEqual(s[0]["args"], SETUP_FLAGS + ["--plugin-url", BOOTSTRAP_URL, PART_ONE_PROMPT], term)
        self.assertEqual(s[0]["launcher"], "1", term)
        self.assertEqual(s[0]["stdin_tty"], "yes", f"claude is a TUI: the stub puts a terminal back on stdin\n{term}")
        self.assertEqual(s[0]["stdin_is_dev_tty"], "no",
                         f"and it is a dup of the launcher's own terminal fd, not the /dev/tty alias device Bun's "
                         f"kqueue refuses with EINVAL\n{term}")

    def test_bare_verb_from_a_file_is_still_daily(self):
        """OPX-1332 (D-a): `fuse-dm` typed — bash reading the script from a file, no argv — stays the daily session."""
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["args"], DAILY_ARGV)
        self.assertNotIn("--plugin-url", s[0]["args"])
        self.assertEqual(s[0]["launcher"], "unset")


class RelaunchLoop(Drive):
    """AC2: after each exit the launcher reads ~/.fuse/dm-setup/state — `bootstrap-done` → part two,
    `needs-second-pass` → part two once more, `done` or anything else → stop; never a fourth launch."""

    def test_bootstrap_done_goes_to_part_two(self):
        r = self.run_dm("setup", FAKE_CLAUDE_WRITE_STATE="bootstrap-done,done")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["args"][-1], PART_ONE_PROMPT)
        self.assertIn("--plugin-url", s[0]["args"])
        self.assertEqual(s[1]["args"], SETUP_FLAGS + [PART_TWO_PROMPT], "part two: no --plugin-url, the setup prompt")
        self.assertEqual(self.state(), "done")

    def test_needs_second_pass_runs_part_two_once_more(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_WRITE_STATE="needs-second-pass,done")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [SETUP_FLAGS + [PART_TWO_PROMPT]] * 2)

    def test_done_stops(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_WRITE_STATE="done,done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sessions()), 1)

    def test_unknown_state_stops(self):
        for value in ("banana", "", None):
            with self.subTest(state=value):
                shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
                shutil.rmtree(self.home / ".fuse", ignore_errors=True)
                extra = {} if value is None else {"FAKE_CLAUDE_WRITE_STATE": value + ",done"}
                r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, **extra)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(self.sessions()), 1)

    def test_at_most_three_launches_then_closing_line(self):
        r = self.run_dm("setup", FAKE_CLAUDE_WRITE_STATE="bootstrap-done,needs-second-pass,needs-second-pass,needs-second-pass")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 3, "never a fourth launch")
        self.assertEqual([x["args"][-1] for x in s], [PART_ONE_PROMPT, PART_TWO_PROMPT, PART_TWO_PROMPT])
        closing = [l for l in r.stdout.splitlines() if l.startswith("fuse-dm:")][-1]
        self.assertIn("Fuse Claude", closing, "one plain closing line, and it names the icon for tomorrow")
        self.assertNotIn("--", closing, "plain: no flags in the closing line")
        self.assertEqual(r.stdout.count("Fuse Claude"), 1, "one closing line, not one per pass")

    def test_the_launcher_only_reads_the_state_file(self):
        """The bootstrap plugin writes `bootstrap-done`, setup writes `done` / `needs-second-pass`; the launcher writes nothing."""
        (self.home / STATE).parent.mkdir(parents=True)
        (self.home / STATE).write_text("done\n")
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state(), "done", "untouched by the launcher")
        text = SCRIPT.read_text()
        self.assertNotRegex(text, r"(>|>>)\s*\"?\$[A-Z_]*STATE", "no redirect into the state file")


class NoBypass(Drive):
    """AC3: the daily and `update` sessions never bypass and the script never touches a settings file — argv and text."""

    def test_daily_argv_is_model_and_auto_only(self):
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["args"], DAILY_ARGV)
        self.assertEqual(Path(s[0]["cwd"]).resolve(), (self.home / "Fuse").resolve())
        self.assertEqual(s[0]["launcher"], "unset", "a daily session is not a setup session: FUSE_DM_LAUNCHER stays unset")
        self.assertEqual(s[0]["effort_env"], "unset", "the seat's effort on a daily session")
        self.assertEqual(len(self.launches()), 1, "no plugin list, no update on the daily verb")

    def test_update_runs_the_two_update_lines_then_daily(self):
        r = self.run_dm("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()],
                         [["plugin", "marketplace", "update", "fuse-internal"],
                          ["plugin", "update", "deployment-manager@fuse-internal"],
                          DAILY_ARGV])

    def test_a_failed_update_still_opens_the_day(self):
        r = self.run_dm("update", FAKE_UPDATE_RC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.launches()[-1]["args"], DAILY_ARGV)
        self.assertIn("update", (r.stdout + r.stderr).lower())

    def test_no_bypass_token_on_daily_or_update(self):
        for verb in ((), ("update",)):
            with self.subTest(verb=verb or ("daily",)):
                shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
                self.run_dm(*verb)
                for l in self.launches():
                    for bad in ("--dangerously-skip-permissions", "bypassPermissions", "--plugin-url"):
                        self.assertNotIn(bad, l["args"])
                        self.assertNotIn(bad, " ".join(l["args"]))

    def test_script_text_never_names_a_settings_file(self):
        text = SCRIPT.read_text()
        for bad in ("settings.json", "settings.local.json", "defaultMode", "bypassPermissions"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text)

    def test_daily_model_follows_the_override(self):
        self.run_dm(FUSE_DM_MODEL="claude-sonnet-5")
        self.assertEqual(self.sessions()[0]["args"], ["--model", "claude-sonnet-5", "--permission-mode", "auto"])


class ModelRetry(Drive):
    """AC4: `--model fable` refused on the seat → one retry with `opus`, said in one line; any other exit propagates."""

    REFUSAL = "Error: the model 'fable' is not available on this account"

    def test_retry_once_with_opus_on_refusal(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_EXIT="1", FAKE_CLAUDE_STDERR=self.REFUSAL)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["args"][:2], ["--model", "fable"])
        self.assertEqual(s[1]["args"], ["--model", "opus", "--effort", "medium", "--dangerously-skip-permissions", PART_TWO_PROMPT])
        self.assertIn(self.REFUSAL, r.stderr, "claude's own stderr still reaches the terminal")

    def test_no_retry_on_other_nonzero_exit(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_EXIT="3", FAKE_CLAUDE_STDERR="Error: something else broke")
        self.assertEqual(r.returncode, 3, "claude's own exit code")
        self.assertEqual(len(self.sessions()), 1)
        self.assertNotIn("opus", r.stdout + r.stderr)

    def test_retry_is_said_in_one_line(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_EXIT="1", FAKE_CLAUDE_STDERR=self.REFUSAL)
        said = [l for l in r.stdout.splitlines() if "opus" in l]
        self.assertEqual(len(said), 1, r.stdout)
        self.assertIn("fable", said[0])

    def test_only_one_retry_even_when_opus_is_refused_too(self):
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_EXIT="1,1", FAKE_CLAUDE_STDERR="Error: the model fable opus is not available")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.sessions()), 2)

    def test_the_retry_model_carries_into_the_next_pass(self):
        """A seat that refused fable once refuses it again: the passes after the retry launch on opus."""
        r = self.run_dm("setup", FAKE_CLAUDE_EXIT="1", FAKE_CLAUDE_STDERR=self.REFUSAL, FAKE_CLAUDE_WRITE_STATE=",bootstrap-done,done")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"][1] for x in s], ["fable", "opus", "opus"])
        self.assertEqual([x["args"][-1] for x in s], [PART_ONE_PROMPT, PART_ONE_PROMPT, PART_TWO_PROMPT])


class Shim(Drive):
    """AC5: `install-shim` writes ~/.fuse/bin/fuse-dm, a shim that execs the installed plugin's copy, resolving
    `installPath` from installed_plugins.json on EVERY call (a plugin update needs no re-install — not fuse-live's
    copy), and the Desktop icon that runs the shim's plain daily verb."""

    def plugin_copy(self, name, folder=".claude"):
        """A plugin copy at $TMP/<name>, registered in <HOME>/<folder>/plugins/installed_plugins.json (OPX-2024: the
        Claude folder is a parameter — ~/.claude, or a DM's second folder)."""
        root = self.tmp / name
        write_exec(root / "bin" / "fuse-dm", FAKE_PLUGIN_COPY)
        (self.home / folder / "plugins").mkdir(parents=True, exist_ok=True)
        (self.home / folder / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"deployment-manager@fuse-internal": [{"scope": "user", "installPath": str(root), "version": PLUGIN_VERSION}]}}))
        return root

    def exec_record(self):
        f = self.fake_dir / "exec.txt"
        return f.read_text().splitlines() if f.exists() else []

    def test_install_shim_writes_the_shim_and_the_path_line(self):
        r = self.run_dm("install-shim")
        self.assertEqual(r.returncode, 0, r.stderr)
        shim = self.home / SHIM
        self.assertTrue(shim.is_file())
        self.assertTrue(shim.stat().st_mode & stat.S_IXUSR)
        body = shim.read_text()
        self.assertTrue(body.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("installed_plugins.json", body)
        self.assertIn("installPath", body)
        self.assertIn("exec", body)
        self.assertNotEqual(body, SCRIPT.read_text(), "a shim, not a copy of the script")
        self.assertLess(len(body), 1500)
        self.assertIn(f"fuse-dm: installed {shim}", r.stdout)
        self.assertIn('export PATH="$HOME/.fuse/bin:$PATH"', r.stdout)
        self.assertEqual(self.launches(), [], "install-shim starts no claude")

    def test_install_shim_is_silent_about_path_when_on_it(self):
        r = self.run_dm("install-shim", PATH=f"{self.home}/.fuse/bin:{self.bin}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("export PATH", r.stdout)

    def test_shim_execs_current_install_path(self):
        self.run_dm("install-shim")
        shim = self.home / SHIM
        a = self.plugin_copy("plugin-A")
        r = self.run_dm("setup", "--x", script=shim)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.exec_record(), [f"copy={a / 'bin' / 'fuse-dm'}", "arg=setup", "arg=--x"])
        b = self.plugin_copy("plugin-B")      # a plugin update: installed_plugins.json now names another folder
        r = self.run_dm(script=shim)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.exec_record(), [f"copy={b / 'bin' / 'fuse-dm'}"], "the second call execs the NEW installPath")

    def test_shim_without_the_plugin_is_69(self):
        self.run_dm("install-shim")
        r = self.run_dm(script=self.home / SHIM)
        self.assertEqual(r.returncode, 69)
        self.assertIn("deployment-manager", r.stderr)
        self.assertEqual(self.exec_record(), [])

    def test_desktop_icon_points_at_shim(self):
        r = self.run_dm("install-shim", FUSE_DM_OSTYPE="darwin24")
        self.assertEqual(r.returncode, 0, r.stderr)
        icon = self.home / "Desktop" / "Fuse Claude.command"
        self.assertTrue(icon.is_file(), "the macOS icon")
        self.assertTrue(icon.stat().st_mode & stat.S_IXUSR)
        body = icon.read_text()
        self.assertIn("$HOME/.fuse/bin/fuse-dm", body)
        for bad in ("plugins/cache", str(SCRIPT), str(PLUGIN_DIR), "setup", "--dangerously-skip-permissions", "--model"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, body, "the icon runs the shim's plain daily verb, never a plugin path")
        self.assertFalse((self.home / "Desktop" / "Fuse Claude.cmd").exists())
        self.assertIn("Fuse Claude.command", r.stdout)

    # ---- OPX-2024: the record of the Claude folder, the shim that follows it, and `install-shim --check` ----
    # The shim body as 1.116.67 wrote it: ~/.claude fixed. A machine that ran row 14 before OPX-2024 has this file.
    OLD_SHIM_BODY = (
        "#!/usr/bin/env bash\n"
        "# fuse-dm — the shim. Execs the installed deployment-manager plugin's bin/fuse-dm, resolving the plugin's installPath\n"
        "# from ~/.claude/plugins/installed_plugins.json on every call, so a plugin update changes nothing here.\n"
        "root=\"$(awk '/\"deployment-manager@fuse-internal\"/{f=1} f&&/\"installPath\"/{sub(/.*\"installPath\"[[:space:]]*:[[:space:]]*\"/,\"\"); "
        "sub(/\".*/,\"\"); print; exit}' \"$HOME/.claude/plugins/installed_plugins.json\" 2>/dev/null)\"\n"
        "root=\"${root//\\\\\\\\//}\"\n"
        "[ -n \"$root\" ] && [ -f \"$root/bin/fuse-dm\" ] || { echo \"fuse-dm: the deployment-manager plugin is not installed on this computer "
        "— fuse-dm setup needs it; run the bootstrap first\" >&2; exit 69; }\n"
        "exec bash \"$root/bin/fuse-dm\" \"$@\"\n")

    def tree(self):
        """Every path under HOME, with a file's bytes and mtime: what `install-shim --check` must leave as it found it."""
        return {str(p.relative_to(self.home)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
                for p in sorted(self.home.rglob("*"))}

    def check(self, **extra):
        """`install-shim --check`'s exit code, after asserting it wrote nothing, removed nothing and started no claude."""
        before = self.tree()
        r = self.run_dm("install-shim", "--check", **extra)
        self.assertEqual(self.tree(), before, f"--check writes nothing ({extra})")
        self.assertEqual(self.launches(), [], "--check starts no claude")
        self.assertEqual(r.stderr, "", "--check answers with its exit code")
        return r.returncode

    def test_install_shim_writes_the_record_when_the_env_is_set_and_removes_it_when_not(self):
        """AC2: one line, the absolute folder (`~` expanded), when CLAUDE_CONFIG_DIR is set and non-empty; no record when
        it is unset or empty; the shim and the icon as today either way."""
        record = self.home / CONFIG_RECORD
        r = self.run_dm("install-shim", CLAUDE_CONFIG_DIR="~/" + FUSE_FOLDER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(record.read_text(), f"{self.home / FUSE_FOLDER}\n", "one line, the folder with ~ expanded")
        self.assertTrue((self.home / SHIM).is_file())
        self.assertTrue((self.home / "Desktop" / "Fuse Claude.command").is_file())
        self.assertIn(str(self.home / FUSE_FOLDER), r.stdout, "the folder is said")
        r = self.run_dm("install-shim", CLAUDE_CONFIG_DIR=str(self.tmp / "abs"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(record.read_text(), f"{self.tmp / 'abs'}\n", "an absolute folder as it is")
        for unset in (None, ""):
            with self.subTest(env="unset" if unset is None else "empty"):
                record.parent.mkdir(parents=True, exist_ok=True)
                record.write_text(f"{self.home / FUSE_FOLDER}\n")
                r = self.run_dm("install-shim", CLAUDE_CONFIG_DIR=unset)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse(record.exists(), "no variable, no record")
                self.assertTrue((self.home / SHIM).is_file())
        self.assertEqual(self.launches(), [], "install-shim starts no claude")

    def test_check_is_0_on_a_fresh_install_and_writes_nothing(self):
        for ostype, icon in (("darwin24", "Fuse Claude.command"), ("msys", "Fuse Claude.cmd")):
            for env in (None, "~/" + FUSE_FOLDER):
                with self.subTest(ostype=ostype, env=env):
                    shutil.rmtree(self.home); self.home.mkdir()
                    r = self.run_dm("install-shim", FUSE_DM_OSTYPE=ostype, CLAUDE_CONFIG_DIR=env)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue((self.home / "Desktop" / icon).is_file())
                    self.assertEqual(self.check(FUSE_DM_OSTYPE=ostype, CLAUDE_CONFIG_DIR=env), 0)

    def test_check_is_1_when_the_shim_the_icon_or_the_record_differs(self):
        """AC2: 1 on a machine with nothing installed, an old-body shim (1.116.67's, ~/.claude fixed), a shim one byte
        off (the trailing newline: the comparison is byte for byte), a missing icon, and a record that differs from the
        calling environment — in each case nothing written."""
        env = "~/" + FUSE_FOLDER
        self.assertEqual(self.check(CLAUDE_CONFIG_DIR=env), 1, "nothing installed")
        cases = {
            "an old-body shim": lambda: (self.home / SHIM).write_text(self.OLD_SHIM_BODY),
            "a shim without its last newline": lambda: (self.home / SHIM).write_text((self.home / SHIM).read_text()[:-1]),
            "a missing icon": lambda: (self.home / "Desktop" / "Fuse Claude.command").unlink(),
            "a record naming another folder": lambda: (self.home / CONFIG_RECORD).write_text(f"{self.home / '.claude-other'}\n"),
            "no record while the env is set": lambda: (self.home / CONFIG_RECORD).unlink(),
        }
        for what, spoil in cases.items():
            with self.subTest(what=what):
                shutil.rmtree(self.home); self.home.mkdir()
                r = self.run_dm("install-shim", CLAUDE_CONFIG_DIR=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.check(CLAUDE_CONFIG_DIR=env), 0, "fresh")
                spoil()
                self.assertEqual(self.check(CLAUDE_CONFIG_DIR=env), 1, what)
        with self.subTest(what="a record while the env is unset"):
            shutil.rmtree(self.home); self.home.mkdir()
            self.run_dm("install-shim", CLAUDE_CONFIG_DIR=env)
            self.assertEqual(self.check(CLAUDE_CONFIG_DIR=None), 1)
            self.assertEqual(self.check(CLAUDE_CONFIG_DIR=""), 1, "empty is unset")

    def test_install_shim_takes_only_check(self):
        r = self.run_dm("install-shim", "--frobnicate")
        self.assertEqual(r.returncode, 64)
        self.assertIn("usage: fuse-dm", r.stderr)
        self.assertFalse((self.home / SHIM).exists(), "nothing installed on a usage error")

    def test_help_says_check_and_the_record(self):
        r = self.run_dm("--help", PATH=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("--check", "~/" + CONFIG_RECORD.as_posix(), "CLAUDE_CONFIG_DIR"):
            with self.subTest(token=token):
                self.assertIn(token, r.stdout)
        self.assertIn("--check", SCRIPT.read_text().split("set -u", 1)[0], "the header comment says it")

    def test_shim_follows_the_claude_folder_from_the_env_and_from_the_record(self):
        """AC (Shim): the plugin registered under ~/.claude-fuse only. The shim execs it with CLAUDE_CONFIG_DIR set, and
        from the record alone (a stray older ~/.claude install never wins); with neither it exits 69, today's message."""
        self.run_dm("install-shim")
        shim = self.home / SHIM
        fuse = self.plugin_copy("plugin-fuse", folder=FUSE_FOLDER)
        r = self.run_dm("term", script=shim, CLAUDE_CONFIG_DIR="~/" + FUSE_FOLDER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.exec_record(), [f"copy={fuse / 'bin' / 'fuse-dm'}", "arg=term"], "from the env")
        (self.fake_dir / "exec.txt").unlink()
        r = self.run_dm(script=shim)
        self.assertEqual(r.returncode, 69, "neither the env nor a record, and no ~/.claude install")
        self.assertIn("the deployment-manager plugin is not installed on this computer", r.stderr)
        self.assertEqual(self.exec_record(), [])
        r = self.run_dm("install-shim", CLAUDE_CONFIG_DIR="~/" + FUSE_FOLDER)    # row 14, in the DM's Fuse session
        self.assertEqual(r.returncode, 0, r.stderr)
        self.plugin_copy("plugin-stray")                                          # an older copy under ~/.claude
        r = self.run_dm(script=shim)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.exec_record(), [f"copy={fuse / 'bin' / 'fuse-dm'}"], "from the record, not the stray ~/.claude copy")

    def test_windows_icon_opens_git_bash_on_the_shim(self):
        r = self.run_dm("install-shim", FUSE_DM_OSTYPE="msys")
        self.assertEqual(r.returncode, 0, r.stderr)
        icon = self.home / "Desktop" / "Fuse Claude.cmd"
        self.assertTrue(icon.is_file(), "the Windows icon")
        body = icon.read_text()
        self.assertIn("bash.exe", body)
        self.assertIn("/.fuse/bin/fuse-dm", body)
        self.assertNotIn("plugins/cache", body)
        self.assertNotIn("setup", body)
        self.assertFalse((self.home / "Desktop" / "Fuse Claude.command").exists())
        self.assertIn('export PATH="$HOME/.fuse/bin:$PATH"', r.stdout)


class ClaudeConfigDir(Drive):
    """OPX-2024 AC1: one shell rule for Claude Code's own folder — CLAUDE_CONFIG_DIR when set and non-empty (a leading ~
    expanded), else the folder the record ~/.fuse/dm-setup/claude-config-dir names (its first line, non-empty, an
    existing folder), else ~/.claude. bin/fuse-dm holds it as a marked function; the shim body it writes, bin/fuse-live
    and the sync launcher carry the block byte for byte (the PythonResolution idiom: the copies pinned equal, AND each one
    run). Each copy runs under /bin/bash with a scratch HOME and an EMPTY PATH — the rule is builtins only — and must
    give the same folder in every case. The block sets CLAUDE_DIR, and CLAUDE_DIR_REC=1 when the record decided it."""

    def copies(self):
        """{where: block}: fuse-dm's function, the shim install-shim writes, and fuse-live's and the sync launcher's where this
        tree has them (the public tree, dm-start, has neither)."""
        own = BLOCK.findall(SCRIPT.read_text())
        self.assertEqual(len(own), 2, f"{SCRIPT.name}: the function, and its copy in the shim heredoc")
        r = self.run_dm("install-shim")
        self.assertEqual(r.returncode, 0, r.stderr)
        shim = BLOCK.findall((self.home / SHIM).read_text())
        self.assertEqual(len(shim), 1, "the shim install-shim wrote carries the block once")
        out = {f"{SCRIPT.name} (the function)": own[0], f"{SCRIPT.name} (the shim heredoc)": own[1], "the installed shim": shim[0]}
        for f in (FUSE_LIVE, FUSE_SYNC):
            if f.is_file():
                found = BLOCK.findall(f.read_text())
                self.assertEqual(len(found), 1, f"bin/{f.name} carries the block once")
                out[f"bin/{f.name}"] = found[0]
        return out

    def resolve(self, block, env, record):
        """`block`, then `claude_config_dir`, under /bin/bash: HOME a fresh scratch home holding ~/.claude-fuse, PATH an
        empty folder. `env` None = CLAUDE_CONFIG_DIR not in the environment; `record` None = no record file."""
        home, empty = self.tmp / "rhome", self.tmp / "empty-path"
        shutil.rmtree(home, ignore_errors=True)
        (home / FUSE_FOLDER).mkdir(parents=True)
        empty.mkdir(exist_ok=True)
        if record is not None:
            (home / CONFIG_RECORD).parent.mkdir(parents=True)
            (home / CONFIG_RECORD).write_text(record)
        environ = {"HOME": str(home), "PATH": str(empty)}
        if env is not None:
            environ["CLAUDE_CONFIG_DIR"] = env
        r = subprocess.run([BASH, "-c", block + '\nclaude_config_dir\nprintf "%s|%s" "$CLAUDE_DIR" "$CLAUDE_DIR_REC"\n'],
                           env=environ, capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""), "the rule runs on builtins alone")
        return tuple(r.stdout.split("|"))

    def cases(self):
        """(case, CLAUDE_CONFIG_DIR, the record's text, the folder, from the record) — the ticket's seven, then two more:
        a set variable outranks the record, and an empty one is unset."""
        h, elsewhere = self.tmp / "rhome", str(self.tmp / "elsewhere")
        fuse, claude = str(h / FUSE_FOLDER), str(h / ".claude")
        return (("env unset, no record", None, None, claude, "0"),
                ("env empty", "", None, claude, "0"),
                ("env set", elsewhere, None, elsewhere, "0"),
                ("env with a leading ~", "~/" + FUSE_FOLDER, None, fuse, "0"),
                ("env unset, the record naming a folder that exists", None, fuse + "\n", fuse, "1"),
                ("env unset, the record naming a folder that does not", None, str(h / ".claude-gone") + "\n", claude, "0"),
                ("an empty record", None, "", claude, "0"),
                ("env set, a record too", elsewhere, fuse + "\n", elsewhere, "0"),
                ("env empty, the record naming a folder that exists", "", fuse + "\n", fuse, "1"))

    def test_every_copy_gives_the_same_folder_in_every_case(self):
        for where, block in self.copies().items():
            for case, env, record, folder, from_record in self.cases():
                with self.subTest(copy=where, case=case):
                    self.assertEqual(self.resolve(block, env, record), (folder, from_record))

    def test_the_copies_are_byte_identical(self):
        copies = self.copies()
        reference = BLOCK.findall(SCRIPT.read_text())[0]
        for where, block in copies.items():
            with self.subTest(copy=where):
                self.assertEqual(block, reference, f"{where} drifted from bin/fuse-dm's claude-config-dir block")
        code = "\n".join(l for l in reference.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotRegex(code, r"\b(awk|sed|cat|python3?|cut|head)\b", "no external in the rule")

    @unittest.skipUnless(FUSE_LIVE.is_file() and FUSE_SYNC.is_file(), "no bin/fuse-live or sync launcher beside this copy (the public tree)")
    def test_fuse_live_and_the_sync_launcher_carry_the_block(self):
        copies = self.copies()
        self.assertIn("bin/fuse-live", copies)
        self.assertIn(f"bin/{FUSE_SYNC.name}", copies)


class ConfigDirLaunch(Drive):
    """OPX-2024 AC3: fuse-dm with a stubbed claude, the plugin registered under ~/.claude-fuse and no ~/.claude install.
    With CLAUDE_CONFIG_DIR=~/.claude-fuse, `fuse-dm update` reaches the keep-current check and reads the version there.
    With only the record, every claude it starts — plugin list, the two update lines, setup, the daily session — sees
    CLAUDE_CONFIG_DIR set to the record's folder. With neither, none sees the variable at all."""

    OLD, NEW = "0.0.1", "0.0.2"
    UPDATE_LINES = [["plugin", "marketplace", "update", "fuse-internal"], ["plugin", "update", "deployment-manager@fuse-internal"]]

    def installed(self, version, folder):
        f = self.home / folder / "plugins" / "installed_plugins.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": 2, "plugins": {"deployment-manager@fuse-internal": [
            {"scope": "user", "installPath": str(self.tmp / "cache" / version), "version": version, "isLocal": False}]}}, indent=2) + "\n")

    def version_record(self, version):
        f = self.home / RECORD
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(version + "\n")

    def config_record(self, folder):
        f = self.home / CONFIG_RECORD
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{folder}\n")

    def recorded(self):
        f = self.home / RECORD
        return f.read_text().strip() if f.exists() else None

    def every_verb(self, **extra):
        """{verb: (rc, launches)} for update, setup (part two), term and the bare verb, each from a fresh fake."""
        out = {}
        for verb in (("update",), ("setup",), ("term",), ()):
            shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
            self.version_record(self.OLD)
            r = self.run_dm(*verb, FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, **extra)
            out[verb[0] if verb else "daily"] = (r.returncode, r.stderr, self.launches())
        return out

    def test_update_reads_the_version_from_the_env_folder(self):
        self.installed(self.NEW, FUSE_FOLDER); self.version_record(self.OLD)
        r = self.run_dm("update", CLAUDE_CONFIG_DIR="~/" + FUSE_FOLDER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()], self.UPDATE_LINES + [DAILY_ARGV + [KEEP_CURRENT_PROMPT]],
                         "the keep-current check read 0.0.2 from ~/.claude-fuse")
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual({l["config_dir"] for l in self.launches()}, {"~/" + FUSE_FOLDER}, "a set variable is left as it is")

    def test_the_record_alone_puts_every_claude_in_its_folder(self):
        self.installed(self.NEW, FUSE_FOLDER)
        self.config_record(self.home / FUSE_FOLDER)
        for verb, (rc, err, launches) in self.every_verb().items():
            with self.subTest(verb=verb):
                self.assertEqual(rc, 0, err)
                self.assertTrue(launches)
                self.assertEqual({l["config_dir"] for l in launches}, {str(self.home / FUSE_FOLDER)})
                if verb in ("update", "term", "daily"):
                    self.assertEqual(launches[-1]["args"], DAILY_ARGV + [KEEP_CURRENT_PROMPT], "the version read from the record's folder")

    def test_neither_leaves_the_environment_alone(self):
        """No variable and no usable record (none; one naming a missing folder): ~/.claude, and no claude sees
        CLAUDE_CONFIG_DIR."""
        self.installed(self.NEW, ".claude")
        for record in (None, self.home / ".claude-gone"):
            with self.subTest(record=None if record is None else record.name):   # xdist ships subTest kwargs: plain values only
                if record is None:
                    (self.home / CONFIG_RECORD).unlink(missing_ok=True)
                else:
                    self.config_record(record)
                for verb, (rc, err, launches) in self.every_verb(CLAUDE_CONFIG_DIR=None).items():
                    with self.subTest(verb=verb):
                        self.assertEqual(rc, 0, err)
                        self.assertEqual({l["config_dir"] for l in launches}, {"unset"})
                        if verb in ("update", "term", "daily"):
                            self.assertEqual(launches[-1]["args"], DAILY_ARGV + [KEEP_CURRENT_PROMPT], "the version read from ~/.claude")


class KeepCurrent(Drive):
    """OPX-1366 (S-6, AC1): the daily launch and `update` compare the installed deployment-manager version (from
    installed_plugins.json — the shim's awk, no claude process) with the one recorded at ~/.fuse/dm-setup/plugin-version,
    the launcher's own file beside the state word (setup rewrites `state` whole, so the record is not a second line of
    it). Moved → the session opens on setup's keep-current pass: the daily argv with the prompt as its last token, auto
    mode, no bypass, one line said first, and the record rewritten after a clean exit. Equal → today's daily, nothing
    written. No record (a machine set up before S-6) → today's daily, no pass forced, the version recorded after a clean
    exit so the next release is caught. Unknown installed version → nothing. A non-zero exit records nothing."""

    # fixture versions, never the plugin's own (scripts/tests/test_version_single_source.py: no test carries the current version literal)
    OLD, NEW = "0.0.1", "0.0.2"

    def installed(self, version):
        """installed_plugins.json as Claude Code writes it: pretty-printed, the entry an array of one object."""
        f = self.home / INSTALLED_PLUGINS
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": 2, "plugins": {"deployment-manager@fuse-internal": [
            {"scope": "user", "installPath": str(self.tmp / "cache" / version), "version": version, "isLocal": False}]}}, indent=2) + "\n")

    def record(self, version):
        f = self.home / RECORD
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(version + "\n")

    def recorded(self):
        f = self.home / RECORD
        return f.read_text().strip() if f.exists() else None

    def said(self, r):
        return [l for l in r.stdout.splitlines() if "re-checking" in l]

    def test_a_moved_version_opens_the_keep_current_pass_and_rewrites_the_record(self):
        self.installed(self.NEW); self.record(self.OLD)
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["args"], DAILY_ARGV + [KEEP_CURRENT_PROMPT], "the daily argv, the pass's prompt as the last token")
        self.assertEqual(Path(s[0]["cwd"]).resolve(), (self.home / "Fuse").resolve())
        self.assertEqual(s[0]["launcher"], "unset", "a daily session: no FUSE_DM_LAUNCHER")
        self.assertEqual(s[0]["effort_env"], "unset", "the seat's effort")
        self.assertEqual(len(self.launches()), 1, "no plugin list: the version is read from installed_plugins.json")
        self.assertEqual(self.recorded(), self.NEW, "the record is rewritten after a clean exit")
        said = self.said(r)
        self.assertEqual(len(said), 1, r.stdout)
        self.assertIn(self.NEW, said[0])
        self.assertTrue(said[0].startswith("fuse-dm: "))

    def test_the_same_version_is_todays_daily_and_writes_nothing(self):
        self.installed(self.NEW); self.record(self.NEW)
        before = (self.home / RECORD).stat().st_mtime_ns
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sessions()[0]["args"], DAILY_ARGV)
        self.assertEqual(self.said(r), [])
        self.assertEqual((self.home / RECORD).stat().st_mtime_ns, before, "not rewritten")
        self.assertEqual(self.recorded(), self.NEW)

    def test_no_record_is_todays_daily_and_records_the_installed_version(self):
        """A machine that ran setup before S-6: no pass forced today, and the baseline written so the next release is."""
        self.installed(self.NEW)
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sessions()[0]["args"], DAILY_ARGV, "no pass forced")
        self.assertEqual(self.said(r), [])
        self.assertEqual(self.recorded(), self.NEW, "recorded after the clean exit")
        self.assertIsNone(self.state(), "the state file is not the launcher's to write")

    def test_an_unknown_installed_version_changes_nothing(self):
        """No installed_plugins.json, or one awk cannot read: today's daily, no record written, a record kept."""
        for shape, keep in (("missing", False), ("malformed", False), ("missing", True)):
            with self.subTest(shape=shape, record=keep):
                shutil.rmtree(self.fake_dir); self.fake_dir.mkdir()
                shutil.rmtree(self.home / ".fuse", ignore_errors=True)
                shutil.rmtree(self.home / ".claude", ignore_errors=True)
                if shape == "malformed":
                    (self.home / INSTALLED_PLUGINS).parent.mkdir(parents=True)
                    (self.home / INSTALLED_PLUGINS).write_text("{not json")
                if keep:
                    self.record(self.OLD)
                r = self.run_dm()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.sessions()[0]["args"], DAILY_ARGV)
                self.assertEqual(self.said(r), [])
                self.assertEqual(self.recorded(), self.OLD if keep else None)

    def test_a_non_zero_exit_records_nothing(self):
        self.installed(self.NEW); self.record(self.OLD)
        r = self.run_dm(FAKE_CLAUDE_EXIT="3", FAKE_CLAUDE_STDERR="Error: something else broke")
        self.assertEqual(r.returncode, 3, "claude's own code")
        self.assertEqual(self.sessions()[0]["args"], DAILY_ARGV + [KEEP_CURRENT_PROMPT])
        self.assertEqual(self.recorded(), self.OLD, "the pass did not complete: the record stays, so the next launch runs it again")

    def test_update_runs_the_same_check_after_the_two_update_lines(self):
        """`fuse-dm update` is where the version moves: the check runs after the two update lines, on the new file."""
        self.installed(self.NEW); self.record(self.OLD)
        r = self.run_dm("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()],
                         [["plugin", "marketplace", "update", "fuse-internal"],
                          ["plugin", "update", "deployment-manager@fuse-internal"],
                          DAILY_ARGV + [KEEP_CURRENT_PROMPT]])
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual(len(self.said(r)), 1)
        for bad in ("--dangerously-skip-permissions", "bypassPermissions", "--plugin-url"):
            self.assertNotIn(bad, " ".join(self.launches()[-1]["args"]))

    def test_setup_records_the_version_when_the_loop_ends_done(self):
        """`fuse-dm setup` that ends with `done` writes the baseline too — read after the passes, since part one is
        what installs the plugin."""
        self.installed(self.NEW)
        r = self.run_dm("setup", FAKE_PLUGIN_LIST=PLUGIN_LIST_PRESENT, FAKE_CLAUDE_WRITE_STATE="done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual(self.state(), "done", "the state word is setup's, untouched")

    def test_help_and_header_say_what_the_daily_verb_does_now(self):
        r = self.run_dm("--help", PATH=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("keep-current", "~/" + RECORD.as_posix()):
            with self.subTest(token=token):
                self.assertIn(token, r.stdout)
        self.assertIn("keep-current", SCRIPT.read_text().split("set -u", 1)[0], "the header comment says it")


class T3Machine(Drive):
    """The T3 Code machine (OPX-1890): its fixtures, the fakes' records and the shared strings. No test of its own, so
    T3Surface and OPX-2020's QuickPass and QuickResult each run only their own cases. Stdin is a pipe here (`input=""`,
    as under CI), never /dev/null: `stdin_is_dev_null=yes` on a session is the launcher's own redirect."""

    OLD, NEW = "0.0.1", "0.0.2"
    REFUSAL = "Error: the model 'fable' is not available on this account"
    UPDATE_LINES = [["plugin", "marketplace", "update", "fuse-internal"], ["plugin", "update", "deployment-manager@fuse-internal"]]
    # the plan's "Shared names and strings", bash spelling, each said through `say`
    MOVED_T3 = ("the plugin moved to {v} — re-checking the environment first, then T3 Code opens by itself (threads already "
                "open there keep the old version until you start a new one)")
    MOVED = "the plugin moved to {v} — re-checking the environment first"   # OPX-2020: `term`'s line drops `, seconds`
    NO_APP = "T3 Code is not where setup put it — the terminal session instead; fuse-dm setup puts it back"
    OPEN_FAILED = "T3 Code did not open (exit {rc}) — the terminal session instead"
    PASS_FAILED = "the re-check stopped (exit {rc}) — opening it in the terminal instead"
    OPENING = "opening T3 Code"
    # OPX-2020 (docs/plans/2026-10-06-opx-2020.md, "Shared names and strings"): the quick pass's lines, bash spelling
    MOVED_QUICK = ("the plugin moved to {v} — a quick check first, under a minute; then T3 Code opens by itself (threads "
                   "already open there keep the old version until you start a new one)")
    DETAILS = " — details: ~/.fuse/dm-setup/last-pass.log"          # the last RESULT line shown carries it
    NO_RESULT = "the quick check left no result — details: ~/.fuse/dm-setup/last-pass.log"
    QUICK_FAILED = "the quick check stopped (exit {rc}) — fuse-dm setup runs the full check; details: ~/.fuse/dm-setup/last-pass.log"
    LOG_HEADER = "fuse-dm: quick check for deployment-manager {v}"   # the log's first line, the installed version

    def setUp(self):
        super().setUp()
        self.opener = write_exec(self.tmp / "opener", FAKE_OPENER)
        write_exec(self.bin / "osascript", FAKE_OSASCRIPT)
        write_exec(self.bin / "tty", FAKE_TTY)
        self.runs = 0

    def env(self, **extra):
        return super().env(**{"FUSE_DM_OPENER": str(self.opener), "TERM_PROGRAM": "Apple_Terminal", **extra})

    def run_dm(self, *args, **extra):
        # stdin is a pipe (CI, `… | fuse-dm`), never /dev/null: the headless pass's /dev/null must be the launcher's doing
        return subprocess.run([BASH, str(SCRIPT), *args], env=self.env(**extra), input="", capture_output=True, text=True, timeout=60)

    def reset(self):
        """A fresh FAKE_DIR for the next run: a background close from the run before lands in the old one, never here."""
        self.runs += 1
        self.fake_dir = self.tmp / f"fake-{self.runs}"
        self.fake_dir.mkdir()

    # ---- fixtures: KeepCurrent's shapes ----
    def installed(self, version):
        f = self.home / INSTALLED_PLUGINS
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": 2, "plugins": {"deployment-manager@fuse-internal": [
            {"scope": "user", "installPath": str(self.tmp / "cache" / version), "version": version, "isLocal": False}]}}, indent=2) + "\n")

    def record(self, version):
        f = self.home / RECORD
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(version + "\n")

    def recorded(self):
        f = self.home / RECORD
        return f.read_text().strip() if f.exists() else None

    def ready(self, app=True, marker=True, record=NEW, installed=NEW):
        """A machine row 15 finished on: the app at ~/Applications/T3 Code (Alpha).app (a directory, as the cask lands it),
        the marker, the installed version and the record (None → none)."""
        if app:
            (self.home / T3_APP).mkdir(parents=True, exist_ok=True)
        if marker:
            (self.home / T3_MARKER).parent.mkdir(parents=True, exist_ok=True)
            (self.home / T3_MARKER).write_text("2026-10-08T18:20:00Z\n")
        if installed:
            self.installed(installed)
        if record:
            self.record(record)

    # ---- the fakes' records ----
    def records(self, prefix):
        out = []
        for f in sorted(self.fake_dir.glob(prefix + ".*"), key=lambda p: int(p.name.split(".")[1])):
            rec = {"args": []}
            for line in f.read_text().splitlines():
                k, v = line.split("=", 1)
                if k == "arg":
                    rec["args"].append(v)
                else:
                    rec[k] = v
            out.append(rec)
        return out

    def openers(self):
        return self.records("opener")

    def wait_for(self, prefix="osascript", timeout=3.0):
        """The close runs in the background: poll until a complete record (`end=yes`) is there, or the timeout."""
        deadline = time.monotonic() + timeout
        while True:
            done = [r for r in self.records(prefix) if r.get("end") == "yes"]
            if done or time.monotonic() >= deadline:
                return done
            time.sleep(0.05)

    def no_close(self):
        """No `osascript` at all: a short beat first — a wrong close would be started right before the launcher exits."""
        time.sleep(0.5)
        return self.records("osascript")

    @staticmethod
    def applescript(args):
        """osascript's argv split into the `-e` lines and what follows them (the script's own argv)."""
        lines, i = [], 0
        while i + 1 < len(args) and args[i] == "-e":
            lines.append(args[i + 1])
            i += 2
        return lines, args[i:]

    def lines(self, r):
        return r.stdout.splitlines()

    def said(self, r):
        return [l for l in r.stdout.splitlines() if "re-checking" in l]

    def assert_opens(self, r, why="the control: this machine opens T3 Code"):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((len(self.openers()), self.sessions()), (1, []), f"{why}\n{r.stdout}{r.stderr}")

    def assert_terminal(self, r, argv=DAILY_ARGV):
        """Today's terminal session, exactly one, interactive (the DM's own stdin), and T3 Code never tried."""
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [argv], r.stdout + r.stderr)
        self.assertEqual(s[0]["stdin_is_dev_null"], "no", "the terminal session keeps the DM's stdin")
        self.assertEqual(self.openers(), [], "T3 Code is not opened")

    # ---- OPX-2020: the hold's fake and the quick pass's log ----
    def fake_sleep(self):
        """`sleep` in the stub bin becomes FAKE_SLEEP (write_exec unlinks the link to the real one first: the real binary
        is never written through), so a hold costs nothing and says what it was asked for."""
        write_exec(self.bin / "sleep", FAKE_SLEEP)

    def sleeps(self):
        return self.records("sleep")

    def pass_log(self):
        """~/.fuse/dm-setup/last-pass.log's lines, or None when there is no such file."""
        f = self.home / PASS_LOG
        return f.read_text().splitlines() if f.is_file() else None


class T3Surface(T3Machine):
    """OPX-1890 (docs/spec/2026-10-02-fuse-dm-t3-icon-design.md, rulings A-K): bare `fuse-dm` (the Desktop icon) and
    `update` open T3 Code when setup's row 15 configured it — ~/.fuse/dm-setup/t3-ready AND the app found — after the
    keep-current check. Unchanged → no claude process at all, then the opener; moved → a headless keep-current pass (the
    daily argv with `-p` right before the prompt, stdin from /dev/null, the same refusal retry), the record, then the
    opener; a failed pass → today's terminal session on the prompt. In macOS Terminal a background `osascript` closes the
    launcher's own window once its shell has ended (one tab, no process left in it). `term` and FUSE_DM_SURFACE=term are
    today's session. Each "terminal" case first proves its fixture would open T3 Code (the control, run outside Terminal
    so no close is in flight), then flips one condition, so no negative assertion is vacuous.
    OPX-2020 (docs/spec/2026-10-06-opx-2020-quick-icon-pass-design.md, decisions D, F): the bare verb's moved-version
    pass is now the quick check (QuickPass, QuickResult) and a failed one opens T3 Code anyway; `update` keeps the full
    headless pass, its refusal retry and ruling G, so those cases here run on `update`."""

    # ---- AC1-AC11 ----
    def test_ready_unchanged_opens_t3_starts_no_claude(self):
        """AC1: ready and the version unchanged → no claude process at all (no session, no `plugin list`), the opener
        once with the exact argv, one line said, exit 0, nothing written."""
        self.ready()
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.launches(), [], "no claude process on an unchanged version")
        o = self.openers()
        self.assertEqual(len(o), 1, r.stdout + r.stderr)
        self.assertEqual(o[0]["args"], ["--env", "T3CODE_TELEMETRY_ENABLED=false", "-a", str(self.home / T3_APP)])
        self.assertIn("fuse-dm: " + self.OPENING, self.lines(r))
        self.assertEqual(self.said(r), [], "nothing moved: no re-checking line")
        self.assertEqual(self.recorded(), self.NEW)

    def test_closes_own_window_only_in_apple_terminal(self):
        """AC2 + Review Focus 1-2: in macOS Terminal, right before exit 0, one background `osascript` gets the launcher's
        tty as argv (never interpolated) and closes a window only when its ONE tab has that tty AND no process left —
        a DM's own shell (alive) or a window with other tabs is never closed. Terminal scripting Terminal: no `System
        Events`, no `quit`. Three checks, one second apart."""
        self.ready()
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        closes = self.wait_for()
        self.assertEqual(len(closes), 1, f"one osascript, in the background\n{r.stdout}{r.stderr}")
        lines, rest = self.applescript(closes[0]["args"])
        self.assertEqual(rest, ["/dev/ttys099"], "the launcher's tty is the script's argv")
        script = "\n".join(lines)
        for token in ("on run argv", "item 1 of argv", "repeat 3 times", "delay 1", 'tell application "Terminal"',
                      "(count of tabs of w) is 1", "tty of tab 1 of w is ttyName", "(count of (processes of tab 1 of w)) is 0",
                      "close w"):
            with self.subTest(token=token):
                self.assertIn(token, script)
        self.assertLess(script.index("(count of tabs of w) is 1"), script.index("close w"), "one tab, checked before the close")
        self.assertLess(script.index("(count of (processes of tab 1 of w)) is 0"), script.index("close w"), "no process left, checked before the close")
        for bad in ("quit", "System Events", "do script", "keystroke"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, script)
        for line in lines:
            self.assertNotIn("/dev/ttys099", line, "the tty only as argv, never inside the script text")

    def test_other_terminal_app_closes_nothing(self):
        """Ruling C: another terminal app (`TERM_PROGRAM` anything else, or none) → T3 Code opens, nothing is closed."""
        self.ready()
        for term in ("iTerm.app", "ghostty", None):
            with self.subTest(TERM_PROGRAM=term):
                self.reset()
                r = self.run_dm(TERM_PROGRAM=term)
                self.assert_opens(r, "T3 Code still opens")
                self.assertEqual(self.no_close(), [], "no osascript outside Apple Terminal")

    def test_no_tty_closes_nothing(self):
        """Ruling C: `tty` does not answer → nothing is closed; T3 Code still opens."""
        self.ready()
        r = self.run_dm(FAKE_NO_TTY="1")
        self.assert_opens(r, "T3 Code still opens")
        self.assertEqual(self.no_close(), [])

    def test_moved_runs_headless_pass_records_then_opens(self):
        """AC3 + Review Focus 3, OPX-2020: moved → the headless pass, now the quick one (the daily argv, `--effort low`,
        `-p` right before the quick prompt, the prompt last, stdin from /dev/null so a non-tty stdin is never waited on),
        the record rewritten, THEN the opener; one line, the quick check's, said once."""
        self.ready(record=self.OLD)
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1, r.stdout + r.stderr)
        self.assertEqual(s[0]["args"], QUICK_ARGV, "the daily argv, --effort low, -p, the quick prompt last")
        self.assertEqual(s[0]["stdin_is_dev_null"], "yes", "the headless pass reads /dev/null, never the DM's stdin")
        self.assertEqual(Path(s[0]["cwd"]).resolve(), (self.home / "Fuse").resolve())
        self.assertEqual(s[0]["launcher"], "unset", "a daily session: no FUSE_DM_LAUNCHER")
        self.assertEqual(len(self.launches()), 1, "the version is read from installed_plugins.json, no plugin list")
        self.assertEqual(self.recorded(), self.NEW, "the record after the clean pass")
        o = self.openers()
        self.assertEqual(len(o), 1)
        self.assertEqual(o[0]["claude_calls"], "1", "the opener runs after the pass")
        self.assertEqual(o[0]["record"], self.NEW, "and after the record")
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.MOVED_QUICK.format(v=self.NEW)), 1, r.stdout)
        for other in (self.MOVED_T3, self.MOVED):
            with self.subTest(line=other[24:60]):
                self.assertNotIn("fuse-dm: " + other.format(v=self.NEW), self.lines(r), "the full pass's lines are update's and term's")
        self.assertIn("fuse-dm: " + self.OPENING, self.lines(r))

    def test_failed_pass_falls_back_to_terminal(self):
        """Ruling G, `update`'s alone since OPX-2020 (decision D: a failed quick pass opens T3 Code, QuickResult): the full
        headless pass fails → nothing recorded by it, one line, then today's terminal session on the keep-current prompt
        (no `-p`, interactive), its record after a clean exit, its exit code; T3 Code not opened."""
        for exits, code, record in (("3,0", 0, self.NEW), ("3,5", 5, self.OLD)):
            with self.subTest(FAKE_CLAUDE_EXIT=exits):
                self.reset()
                self.ready(record=self.OLD)
                r = self.run_dm("update", FAKE_CLAUDE_EXIT=exits)
                self.assertEqual(r.returncode, code, r.stderr)
                s = self.sessions()
                self.assertEqual([x["args"] for x in s], [DAILY_ARGV + ["-p", KEEP_CURRENT_PROMPT], DAILY_ARGV + [KEEP_CURRENT_PROMPT]],
                                 r.stdout + r.stderr)
                self.assertEqual(s[1]["stdin_is_dev_null"], "no", "the terminal session is interactive")
                self.assertIn("fuse-dm: " + self.PASS_FAILED.format(rc=3), self.lines(r))
                self.assertEqual(self.openers(), [], "T3 Code is not opened after a failed pass")
                self.assertEqual(self.recorded(), record, "recorded only after the terminal session's clean exit")
                self.assertEqual(self.no_close(), [])

    def test_headless_refusal_retry_keeps_p(self):
        """Review Focus 4, on `update`'s full pass (OPX-2020: the quick pass's retry is QuickPass's): the seat refuses
        fable on the headless pass → the one opus retry keeps `-p` and the prompt last, stdin still /dev/null; then the
        record and the opener."""
        self.ready(record=self.OLD)
        r = self.run_dm("update", FAKE_CLAUDE_EXIT="1,0", FAKE_CLAUDE_STDERR=self.REFUSAL)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [DAILY_ARGV + ["-p", KEEP_CURRENT_PROMPT],
                                                   ["--model", "opus", "--permission-mode", "auto", "-p", KEEP_CURRENT_PROMPT]])
        self.assertEqual([x["stdin_is_dev_null"] for x in s], ["yes", "yes"])
        self.assertEqual(self.recorded(), self.NEW)
        o = self.openers()
        self.assertEqual(len(o), 1)
        self.assertEqual(o[0]["claude_calls"], "4", "after the two update lines and the retry")
        self.assertNotIn("the re-check stopped", r.stdout)

    def test_app_without_marker_is_terminal(self):
        """AC4: T3 Code installed but row 15 never wrote the marker → today's session, nothing said about T3 Code."""
        self.ready()
        self.assert_opens(self.run_dm(TERM_PROGRAM=None))
        (self.home / T3_MARKER).unlink()
        self.reset()
        r = self.run_dm()
        self.assert_terminal(r)
        self.assertNotIn("T3 Code", r.stdout, "setup has not set it up: nothing to say about it")
        self.assertEqual(self.no_close(), [])

    def test_marker_without_app_is_terminal(self):
        """AC5 + ruling F: the marker without the app (dragged to the Bin) → one line, then today's session; the marker is
        setup's and stays. FUSE_DM_T3_APP set is the only place looked at: a missing path is no app, even with one in
        ~/Applications. Both runs name a missing FUSE_DM_T3_APP, so the machine's own /Applications is never read."""
        self.ready()
        self.assert_opens(self.run_dm(TERM_PROGRAM=None))
        shutil.rmtree(self.home / "Applications")
        missing = {"FUSE_DM_T3_APP": str(self.tmp / "missing" / "T3 Code.app")}
        for how, extra, app_in_home in (("removed", missing, False), ("FUSE_DM_T3_APP missing", missing, True)):
            with self.subTest(how=how):
                if app_in_home:
                    (self.home / T3_APP).mkdir(parents=True, exist_ok=True)
                self.reset()
                r = self.run_dm(**extra)
                self.assertEqual(self.lines(r).count("fuse-dm: " + self.NO_APP), 1, r.stdout + r.stderr)
                self.assert_terminal(r)
                self.assertTrue((self.home / T3_MARKER).is_file(), "the marker is setup's: never removed by the launcher")

    def test_term_verb_is_todays_session(self):
        """AC6: `fuse-dm term` is today's daily session, ready or not: the same keep-current check in the terminal (no
        `-p`, the old line), never the opener, never a close."""
        self.ready()
        r = self.run_dm("term")
        self.assert_terminal(r)
        self.assertEqual(self.no_close(), [])
        self.reset()
        self.record(self.OLD)
        r = self.run_dm("term")
        self.assert_terminal(r, DAILY_ARGV + [KEEP_CURRENT_PROMPT])
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.MOVED.format(v=self.NEW)), 1, r.stdout)
        self.assertNotIn("T3 Code opens by itself", r.stdout)
        self.assertEqual(self.recorded(), self.NEW)
        (self.home / T3_MARKER).unlink()
        self.reset()
        self.assert_terminal(self.run_dm("term"))

    def test_update_then_hand_over(self):
        """AC7 + ruling H: `update` = its two lines, then exactly bare fuse-dm's path: unchanged → the opener after them
        (no session) and the close in Terminal; moved → the headless pass, the record, the opener."""
        self.ready()
        r = self.run_dm("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()], self.UPDATE_LINES, r.stdout + r.stderr)
        o = self.openers()
        self.assertEqual(len(o), 1, r.stdout + r.stderr)
        self.assertEqual(o[0]["claude_calls"], "2", "after the two update lines")
        self.assertIn("fuse-dm: " + self.OPENING, self.lines(r))
        self.assertEqual(len(self.wait_for()), 1, "the same close step as the bare verb")
        self.reset()
        self.record(self.OLD)
        r = self.run_dm("update", TERM_PROGRAM=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()], self.UPDATE_LINES + [DAILY_ARGV + ["-p", KEEP_CURRENT_PROMPT]])
        self.assertEqual(self.launches()[-1]["stdin_is_dev_null"], "yes")
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual([x["claude_calls"] for x in self.openers()], ["3"])

    def test_opener_argv_and_env(self):
        """AC8 + ruling E: macOS `open --env T3CODE_TELEMETRY_ENABLED=false -a <app>` (open hands the variable to the
        app's own environment); FUSE_DM_T3_APP names the app when set; Git Bash puts the variable in the opener's own
        environment (cmd's `start` passes it to the app)."""
        self.ready()
        self.run_dm(TERM_PROGRAM=None)
        self.assertEqual([o["args"] for o in self.openers()], [["--env", "T3CODE_TELEMETRY_ENABLED=false", "-a", str(self.home / T3_APP)]])
        elsewhere = self.tmp / "Elsewhere" / "T3 Code.app"
        elsewhere.mkdir(parents=True)
        self.reset()
        self.run_dm(FUSE_DM_T3_APP=str(elsewhere), TERM_PROGRAM=None)
        self.assertEqual([o["args"] for o in self.openers()], [["--env", "T3CODE_TELEMETRY_ENABLED=false", "-a", str(elsewhere)]])
        exe = self.tmp / "Programs" / "t3code" / "T3 Code (Alpha).exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        self.reset()
        self.run_dm(FUSE_DM_OSTYPE="msys", FUSE_DM_T3_APP=str(exe))
        self.assertEqual([o["telemetry"] for o in self.openers()], ["false"], "Git Bash: the opener's own environment")

    def test_app_is_found_by_its_glob_never_its_name(self):
        """Ruling E: the launcher never writes the product name — the first `T3 Code*.app` in ~/Applications, so the
        successor of "T3 Code (Alpha)" is found the same way; neither script spells "(Alpha)"."""
        self.ready(app=False)
        successor = self.home / "Applications" / "T3 Code.app"
        successor.mkdir(parents=True)
        r = self.run_dm(TERM_PROGRAM=None)
        self.assert_opens(r, "the successor's name is found by the glob")
        self.assertEqual(self.openers()[0]["args"][-1], str(successor))
        for f in (SCRIPT, PS1):
            with self.subTest(file=f.name):
                self.assertNotIn("(Alpha)", f.read_text() if f.is_file() else "")

    def test_surface_term_override(self):
        """AC9: FUSE_DM_SURFACE=term → bare and `update` take today's session on a ready machine; any other value (or
        none) → the surface rule."""
        self.ready()
        self.reset()
        self.assert_opens(self.run_dm(FUSE_DM_SURFACE="auto", TERM_PROGRAM=None), "FUSE_DM_SURFACE other than term: the surface rule")
        self.reset()
        r = self.run_dm(FUSE_DM_SURFACE="term")
        self.assert_terminal(r)
        self.assertEqual(self.no_close(), [])
        self.reset()
        r = self.run_dm("update", FUSE_DM_SURFACE="term")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["args"] for l in self.launches()], self.UPDATE_LINES + [DAILY_ARGV])
        self.assertEqual(self.openers(), [])

    def test_opener_failure_is_terminal(self):
        """Ruling F: the opener exits non-zero → one line, then today's session; no `opening` line, no close."""
        self.ready()
        r = self.run_dm(FAKE_OPENER_EXIT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.openers()), 1, "the opener was tried")
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.OPEN_FAILED.format(rc=1)), 1, r.stdout + r.stderr)
        self.assertNotIn("fuse-dm: " + self.OPENING, self.lines(r))
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [DAILY_ARGV])
        self.assertEqual(s[0]["stdin_is_dev_null"], "no")
        self.assertEqual(self.no_close(), [])

    # the rehearsal finding: macOS `open --env … -a` on a T3 Code already running exits 0 and says this on stderr
    ALREADY_RUNNING = ("Application ~/Applications/T3 Code (Alpha).app was already running and so the additional "
                       "environment variables could not be set.")

    def open_errs(self):
        """The opener's kept stderr files left in TMPDIR (the harness sets it to self.tmp)."""
        return sorted(p.name for p in self.tmp.glob("fuse-dm.*.open.err"))

    def test_opener_stderr_hidden_on_success(self):
        """Rehearsal finding: the opener exits 0 after writing to stderr (an already-running T3 Code) → the DM sees the
        one `opening` line and nothing else; the kept stderr is removed. Control: the fake does write the text."""
        control = subprocess.run([str(self.opener)], env=self.env(FAKE_DIR=str(self.tmp), FAKE_OPENER_STDERR=self.ALREADY_RUNNING),
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual((control.returncode, control.stderr), (0, self.ALREADY_RUNNING + "\n"), "the fake writes the text")
        self.ready()
        r = self.run_dm(TERM_PROGRAM=None, FAKE_OPENER_STDERR=self.ALREADY_RUNNING)
        self.assert_opens(r)
        self.assertNotIn("already running", r.stdout + r.stderr)
        self.assertEqual(self.lines(r), ["fuse-dm: " + self.OPENING], r.stdout + r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(self.open_errs(), [], "no kept stderr left in TMPDIR")

    def test_opener_stderr_shown_on_failure(self):
        """The opener exits non-zero → what it wrote to stderr is shown on stderr, right before the unchanged failure
        line, then today's session; the kept stderr is removed."""
        self.ready()
        r = self.run_dm(FAKE_OPENER_EXIT="1", FAKE_OPENER_STDERR=self.ALREADY_RUNNING)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.splitlines().count(self.ALREADY_RUNNING), 1, r.stdout + r.stderr)
        self.assertNotIn("already running", r.stdout)
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.OPEN_FAILED.format(rc=1)), 1, r.stdout + r.stderr)
        self.assertEqual([x["args"] for x in self.sessions()], [DAILY_ARGV])
        self.assertEqual(self.open_errs(), [], "no kept stderr left in TMPDIR")
        self.reset()                # the order, in the one stream the DM's window shows
        both = subprocess.run([BASH, str(SCRIPT)], env=self.env(FAKE_OPENER_EXIT="1", FAKE_OPENER_STDERR=self.ALREADY_RUNNING),
                              input="", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        seen = both.stdout.splitlines()
        failed = "fuse-dm: " + self.OPEN_FAILED.format(rc=1)
        self.assertIn(failed, seen, both.stdout)
        self.assertEqual(seen[seen.index(failed) - 1], self.ALREADY_RUNNING, both.stdout)
        self.assertEqual(self.open_errs(), [])
        self.assertEqual(self.no_close(), [])

    def test_no_record_ready_records_and_opens(self):
        """Ruling I: no record yet and ready → the installed version recorded, then T3 Code opens; no pass forced."""
        self.ready(record=None)
        r = self.run_dm(TERM_PROGRAM=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.launches(), [], "no pass forced, no claude process")
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual([o["record"] for o in self.openers()], [self.NEW], "recorded before the opener runs")
        self.assertEqual(self.said(r), [])

    def test_git_bash_opener(self):
        """Ruling E (Axel's Windows status, 10-05: winget's per-user install is `%LOCALAPPDATA%\\Programs\\t3code\\`,
        not a `T3 Code*` folder): under Git Bash the app is FUSE_DM_T3_APP, else the first
        `~/AppData/Local/Programs/t3code/T3 Code*.exe` (never its `Uninstall T3 Code*.exe`);
        `T3CODE_TELEMETRY_ENABLED=false cmd //c start "" <exe>` (Unverified); no close step there even when TERM_PROGRAM
        says Apple_Terminal (the console closes by itself when bash ends)."""
        programs = self.home / "AppData" / "Local" / "Programs"
        seam = self.tmp / "t3code" / "T3 Code (Alpha).exe"
        default = programs / "t3code" / "T3 Code (Alpha).exe"
        for exe in (seam, default, programs / "t3code" / "Uninstall T3 Code (Alpha).exe"):
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("")
        self.ready(app=False)
        for how, extra, exe in (("FUSE_DM_T3_APP", {"FUSE_DM_T3_APP": str(seam)}, seam), ("the default folder", {}, default)):
            with self.subTest(how=how):
                self.reset()
                r = self.run_dm(FUSE_DM_OSTYPE="msys", **extra)
                self.assert_opens(r, "Git Bash opens T3 Code")
                o = self.openers()[0]
                self.assertEqual(o["args"], ["//c", "start", "", str(exe)])
                self.assertEqual(o["telemetry"], "false")
                self.assertEqual(self.no_close(), [], "no osascript under Git Bash")
        with self.subTest(how="only the old guess, a `T3 Code*` folder"):
            shutil.rmtree(programs / "t3code")
            guess = programs / "T3 Code (Alpha)" / "T3 Code (Alpha).exe"
            guess.parent.mkdir(parents=True)
            guess.write_text("")
            self.reset()
            r = self.run_dm(FUSE_DM_OSTYPE="msys")
            self.assertEqual(self.lines(r).count("fuse-dm: " + self.NO_APP), 1, r.stdout + r.stderr)
            self.assert_terminal(r)

    def test_linux_with_marker_is_terminal(self):
        """Review Focus 5: FUSE_DM_OSTYPE=linux-gnu (WSL, CI) with a marker → today's session, no opener call."""
        self.ready()
        self.assert_opens(self.run_dm(TERM_PROGRAM=None))
        self.reset()
        self.assert_terminal(self.run_dm(FUSE_DM_OSTYPE="linux-gnu"))
        self.assertEqual(self.no_close(), [])

    def test_help_names_term_and_override(self):
        """AC11: the usage line is the shared one; the verbs gain `term`, the bare line says what it opens, and the
        environment names the three seams — on the bare PATH, no external needed."""
        r = self.run_dm("--help", PATH=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lines(r)[0], USAGE)
        self.assertEqual(bash_line(r"^USAGE='([^']*)'$"), USAGE)
        self.assertRegex(r.stdout, r"(?m)^  term\s", "a `term` verb line, in the help's own layout")
        for token in ("T3 Code", "FUSE_DM_SURFACE", "FUSE_DM_T3_APP", "FUSE_DM_OPENER"):
            with self.subTest(token=token):
                self.assertIn(token, r.stdout)
        self.assertEqual(self.launches(), [])

    # ---- the launcher's own limits on this path ----
    def test_t3_path_reads_the_marker_only(self):
        """Spec § The launcher, Never: no write to the marker or the state file, no T3 Code install, no permission mode
        for T3 Code, no quit of any app — behaviour and text."""
        self.ready()
        m = self.home / T3_MARKER
        before = (m.read_text(), m.stat().st_mtime_ns)
        r = self.run_dm(TERM_PROGRAM=None)
        self.assert_opens(r)
        self.assertEqual((m.read_text(), m.stat().st_mtime_ns), before, "the marker is untouched")
        self.assertIsNone(self.state(), "the state file is not the launcher's")
        text = SCRIPT.read_text()
        self.assertIn('T3_MARKER="$HOME/.fuse/dm-setup/t3-ready"', text)
        self.assertNotRegex(text, r'(>|>>)\s*"?\$\{?T3_MARKER', "no redirect into the marker")
        self.assertNotRegex(text, r"(?m)\brm\b[^\n]*T3_MARKER", "no removal of the marker")
        for bad in ("brew ", "t3.codes", "winget", "full-access", "killall", "System Events"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text)

    def test_t3_path_externals(self):
        """Spec § The launcher, Externals: the header names `osascript` (macOS Terminal only), T3 Code and (OPX-2020)
        `sleep`, the hold after the quick pass; the close is HUP-proof; no `ps`, `pgrep` or `killall` — the
        AppleScript's own `delay` waits for the close."""
        text = SCRIPT.read_text()
        head = text.split("set -u", 1)[0]
        for needle in ("osascript", "T3 Code", "fuse-dm term", "sleep"):
            with self.subTest(needle=needle):
                self.assertIn(needle, head)
        self.assertIn("trap '' HUP", text)
        self.assertNotRegex(text, r"\b(pgrep|killall)\b")
        self.assertNotRegex(text, r"(?m)(^|[\s;&|(])ps\s+-", "no ps")


class QuickPass(T3Machine):
    """OPX-2020 (docs/spec/2026-10-06-opx-2020-quick-icon-pass-design.md, decisions A, E, F; AC2, AC3): a moved version
    on the icon's path — bare `fuse-dm`, T3 Code ready — runs setup's quick case headless: the daily argv plus
    `--effort low`, `-p` right before `/deployment-manager:setup keep-current quick`, stdin from /dev/null, the one opus
    retry keeping every flag. `fuse-dm update` keeps today's full pass (its output in the DM's window, no log, no hold);
    `fuse-dm term` stays today's interactive session. `sleep` is FAKE_SLEEP; no window close is in flight."""

    def setUp(self):
        super().setUp()
        self.fake_sleep()

    def env(self, **extra):
        return super().env(**{"TERM_PROGRAM": None, **extra})

    def test_bare_verb_sends_the_quick_prompt(self):
        """AC2 + AC3: exactly one session, the quick argv, stdin /dev/null; the record, then T3 Code."""
        self.ready(record=self.OLD)
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual(len(s), 1, r.stdout + r.stderr)
        self.assertEqual(s[0]["args"], ["--model", "fable", "--permission-mode", "auto", "--effort", "low", "-p", QUICK_PROMPT])
        self.assertEqual(s[0]["stdin_is_dev_null"], "yes", "the quick pass reads /dev/null, never the DM's stdin")
        self.assertEqual(Path(s[0]["cwd"]).resolve(), (self.home / "Fuse").resolve())
        self.assertEqual(s[0]["launcher"], "unset", "a daily session: no FUSE_DM_LAUNCHER")
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual([o["claude_calls"] for o in self.openers()], ["1"], "T3 Code opens after the pass")

    def test_update_keeps_the_full_pass(self):
        """Decision F: `update` on the same machine → today's full headless pass (no `quick`, no `--effort`), MOVED_T3 said,
        its output in the DM's own window (they typed it, the window stays), no log written, no hold."""
        self.ready(record=self.OLD)
        report = "RESULT: OK — the full pass's own report."
        r = self.run_dm("update", FAKE_CLAUDE_STDOUT=report)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [DAILY_ARGV + ["-p", KEEP_CURRENT_PROMPT]], r.stdout + r.stderr)
        self.assertNotIn("--effort", s[0]["args"])
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.MOVED_T3.format(v=self.NEW)), 1, r.stdout)
        self.assertIn(report, self.lines(r), "the full pass's output stays in the DM's window")
        self.assertIsNone(self.pass_log(), "last-pass.log is the quick pass's")
        self.assertEqual(self.sleeps(), [], "no hold on update")
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual(len(self.openers()), 1)

    def test_term_is_today(self):
        """Decision F: `term` with a moved version → today's interactive session on the keep-current prompt, its moved
        line without a time claim; no quick pass, no log, no hold, no T3 Code."""
        self.ready(record=self.OLD)
        r = self.run_dm("term")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s], [DAILY_ARGV + [KEEP_CURRENT_PROMPT]], r.stdout + r.stderr)
        self.assertEqual(s[0]["stdin_is_dev_null"], "no", "interactive: the DM's own stdin")
        self.assertEqual(self.lines(r).count("fuse-dm: " + self.MOVED.format(v=self.NEW)), 1, r.stdout)
        self.assertEqual(self.openers(), [])
        self.assertEqual(self.sleeps(), [])
        self.assertIsNone(self.pass_log())

    def test_refusal_retry_keeps_the_quick_argv(self):
        """Review Focus 4: the seat refuses fable on the quick pass → the one opus retry keeps `--effort low`, `-p` and the
        quick prompt last, stdin /dev/null; the log is truncated and headed once, before the first attempt, and keeps the
        refusal; the window never shows the pass's stderr."""
        self.ready(record=self.OLD)
        r = self.run_dm(FAKE_CLAUDE_EXIT="1,0", FAKE_CLAUDE_STDERR=self.REFUSAL)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.sessions()
        self.assertEqual([x["args"] for x in s],
                         [QUICK_ARGV, ["--model", "opus", "--permission-mode", "auto", "--effort", "low", "-p", QUICK_PROMPT]])
        self.assertEqual([x["stdin_is_dev_null"] for x in s], ["yes", "yes"])
        self.assertEqual(self.recorded(), self.NEW)
        self.assertEqual([o["claude_calls"] for o in self.openers()], ["2"], "after the retry")
        self.assertEqual(len([l for l in self.lines(r) if "opus" in l]), 1, "the retry said in one line")
        log = self.pass_log()
        self.assertIsNotNone(log, "no ~/.fuse/dm-setup/last-pass.log")
        self.assertEqual(log[0], self.LOG_HEADER.format(v=self.NEW))
        self.assertEqual(log.count(self.LOG_HEADER.format(v=self.NEW)), 1, "one header: the retry appends")
        self.assertIn(self.REFUSAL, log, "the pass's stderr lands in the log")
        self.assertNotIn(self.REFUSAL, r.stdout + r.stderr, "and never in the window")


class QuickResult(T3Machine):
    """OPX-2020 (decisions C, D; AC5): the quick pass's stdout and stderr go to ~/.fuse/dm-setup/last-pass.log (truncated
    each pass, its header first); the window then shows the log's first three `RESULT:` lines — leading decoration and
    the prefix swapped for `fuse-dm: `, the last naming the log — or the no-result line, or after a failed pass the
    stopped line; then the hold (`sleep` FUSE_DM_HOLD seconds, 10 by default, anything but digits → 10, no key read), the
    record (clean exit only), the opener, `opening T3 Code`. A failed quick pass opens T3 Code anyway (ruling G stays
    `update`'s). `sleep` is FAKE_SLEEP; no window close is in flight."""

    STDOUT = ("Version: deployment-manager 0.0.2 · setup", "ROW 2 ok — git, gh",
              "RESULT: OK — nothing missing for deployment-manager 0.0.2.")

    def setUp(self):
        super().setUp()
        self.fake_sleep()

    def env(self, **extra):
        return super().env(**{"TERM_PROGRAM": None, **extra})

    def moved(self, *args, **extra):
        """A ready machine one version behind, then one run of the launcher."""
        self.ready(record=self.OLD)
        return self.run_dm(*args, **extra)

    def between(self, r):
        """The window's lines after MOVED_QUICK and before `opening T3 Code` — the result the DM is left with."""
        lines = self.lines(r)
        first, last = "fuse-dm: " + self.MOVED_QUICK.format(v=self.NEW), "fuse-dm: " + self.OPENING
        self.assertIn(first, lines, r.stdout + r.stderr)
        self.assertIn(last, lines, r.stdout + r.stderr)
        return lines[lines.index(first) + 1:lines.index(last)]

    def test_log_holds_header_and_stdout(self):
        """The log: its header line (the installed version), then the pass's stdout as printed; the window shows none of
        it (stderr lands in the log too: test_failed_pass_opens_t3_anyway)."""
        r = self.moved(FAKE_CLAUDE_STDOUT="\n".join(self.STDOUT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.pass_log(), [self.LOG_HEADER.format(v=self.NEW), *self.STDOUT])
        for leak in ("ROW 2", "Version:"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, r.stdout + r.stderr)

    def test_window_shows_result_lines_and_names_the_log(self):
        """Two RESULT lines → the window is MOVED_QUICK, those two with `fuse-dm: ` for `RESULT: `, the last naming the
        log, then `opening T3 Code` — nothing else."""
        out = ("Version: deployment-manager 0.0.2 · setup", "ROW 2 missing — gh is not signed in", "ROW 14 fixed — install-shim",
               "RESULT: still missing: GitHub sign-in — fuse-dm setup fixes it.",
               "RESULT: fixed here: the launcher and the Desktop icon.")
        r = self.moved(FAKE_CLAUDE_STDOUT="\n".join(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lines(r), ["fuse-dm: " + self.MOVED_QUICK.format(v=self.NEW),
                                         "fuse-dm: still missing: GitHub sign-in — fuse-dm setup fixes it.",
                                         "fuse-dm: fixed here: the launcher and the Desktop icon." + self.DETAILS,
                                         "fuse-dm: " + self.OPENING], r.stdout + r.stderr)

    def test_at_most_three_lines(self):
        """Five RESULT lines → the first three, the third naming the log."""
        r = self.moved(FAKE_CLAUDE_STDOUT="\n".join(f"RESULT: {w}." for w in ("one", "two", "three", "four", "five")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.between(r), ["fuse-dm: one.", "fuse-dm: two.", "fuse-dm: three." + self.DETAILS])

    def test_decorated_result_lines(self):
        """Review Focus 1: the model wraps its reply in a code fence or a list → the launcher still finds the lines and
        strips the leading decoration (spaces, `>`, `*`, `-`, backticks)."""
        r = self.moved(FAKE_CLAUDE_STDOUT="```\nRESULT: OK — a.\n```\n- RESULT: fixed here: b.")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.between(r), ["fuse-dm: OK — a.", "fuse-dm: fixed here: b." + self.DETAILS])
        for deco in ("  ", "> ", "* ", "- ", "```", "`", "> - "):
            with self.subTest(decoration=deco):
                self.reset()
                r = self.moved(FAKE_CLAUDE_STDOUT=deco + "RESULT: fixed here: c.")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.between(r), ["fuse-dm: fixed here: c." + self.DETAILS])

    def test_no_result_line(self):
        """No RESULT line (or no output at all) → the one fallback line; T3 Code opens; the clean pass is recorded."""
        for stdout in ("Version: deployment-manager 0.0.2 · setup\nROW 2 ok — git, gh", None):
            with self.subTest(stdout="some" if stdout else "none"):
                self.reset()
                r = self.moved(FAKE_CLAUDE_STDOUT=stdout)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.between(r), ["fuse-dm: " + self.NO_RESULT])
                self.assertEqual(len(self.openers()), 1)
                self.assertEqual(self.recorded(), self.NEW)

    def test_failed_pass_opens_t3_anyway(self):
        """Decision D: the quick pass exits non-zero (no retry: not a refusal) → the stopped line with its code, nothing
        recorded (the next launch runs the quick check again), the hold, then T3 Code — exactly one claude session, never
        the terminal one; its stderr in the log, never in the window."""
        broke = "Error: something else broke"
        r = self.moved(FAKE_CLAUDE_EXIT="3", FAKE_CLAUDE_STDERR=broke)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x["args"] for x in self.sessions()], [QUICK_ARGV], "one claude session: no terminal session after it")
        self.assertEqual(self.lines(r), ["fuse-dm: " + self.MOVED_QUICK.format(v=self.NEW),
                                         "fuse-dm: " + self.QUICK_FAILED.format(rc=3),
                                         "fuse-dm: " + self.OPENING], r.stdout + r.stderr)
        self.assertIn(broke, self.pass_log() or [], "the pass's stderr lands in the log")
        self.assertNotIn(broke, r.stdout + r.stderr)
        self.assertEqual(self.recorded(), self.OLD, "not recorded: the next launch runs the quick check again")
        self.assertEqual([o["record"] for o in self.openers()], [self.OLD], "the opener called once, nothing recorded")
        self.assertEqual(len(self.sleeps()), 1, "the hold still runs")

    def test_hold_before_the_opener(self):
        """FUSE_DM_HOLD unset → `sleep 10`, once: after the pass, before the record and the opener."""
        r = self.moved(FUSE_DM_HOLD=None, FAKE_CLAUDE_STDOUT="RESULT: OK — nothing missing for deployment-manager 0.0.2.")
        self.assertEqual(r.returncode, 0, r.stderr)
        held = self.sleeps()
        self.assertEqual([h["args"] for h in held], [["10"]], "one hold, 10 s by default")
        self.assertEqual(held[0]["claude_calls"], "1", "after the pass")
        self.assertEqual(held[0]["record"], self.OLD, "before the record")
        self.assertEqual(held[0]["opener_calls"], "0", "before the opener")
        self.assertEqual([o["record"] for o in self.openers()], [self.NEW], "then the record, then T3 Code")

    def test_hold_seam_and_garbage(self):
        """Review Focus 2: FUSE_DM_HOLD digits → that many seconds; anything else (letters, empty, a sign, a point) → 10,
        never a `sleep` error."""
        for value, seconds in (("3", "3"), ("abc", "10"), ("", "10"), ("-1", "10"), ("1.5", "10")):
            with self.subTest(FUSE_DM_HOLD=value):
                self.reset()
                r = self.moved(FUSE_DM_HOLD=value)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([h["args"] for h in self.sleeps()], [[seconds]])
                self.assertEqual(len(self.openers()), 1)

    def test_no_hold_without_a_pass(self):
        """An unchanged version is today's path: no pass, no log, no hold, T3 Code at once."""
        self.ready()
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sessions(), [])
        self.assertEqual(self.sleeps(), [])
        self.assertIsNone(self.pass_log())
        self.assertEqual(len(self.openers()), 1)

    def test_unwritable_log_still_opens(self):
        """Review Focus 3: the log cannot be written (its path is a directory) → the pass still runs, the no-result or the
        stopped line is said, T3 Code still opens, exit 0."""
        self.ready(record=self.OLD)
        (self.home / PASS_LOG).mkdir(parents=True)
        r = self.run_dm(FAKE_CLAUDE_STDOUT="RESULT: OK — nothing missing for deployment-manager 0.0.2.")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.sessions()), 1, "the pass still runs")
        stopped = re.compile(re.escape("fuse-dm: " + self.QUICK_FAILED.format(rc="RC")).replace("RC", r"\d+"))
        said = self.lines(r)
        self.assertTrue(("fuse-dm: " + self.NO_RESULT) in said or any(stopped.fullmatch(l) for l in said), r.stdout + r.stderr)
        self.assertEqual(len(self.openers()), 1, "T3 Code still opens")
        self.assertIn("fuse-dm: " + self.OPENING, said)

    def test_hold_reads_no_key(self):
        """The hold reads no key: stdin a pipe closed at once (`input=""`), then a pipe kept open and never written — the
        run completes both times, and the hold is `sleep`."""
        self.ready(record=self.OLD)
        r = self.run_dm()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sleeps()), 1, "the hold ran")
        self.reset()
        self.ready(record=self.OLD)
        p = subprocess.Popen([BASH, str(SCRIPT)], env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        try:
            code = p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            self.fail("the launcher waited on its stdin (a pipe kept open, never written)")
        finally:
            p.stdin.close()
        out, err = p.stdout.read(), p.stderr.read()
        p.stdout.close()
        p.stderr.close()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.sleeps()), 1, "the hold ran")
        self.assertEqual(len(self.openers()), 1)


class NoSecondsClaim(Drive):
    """OPX-2020 AC6, the bash half (the text half is test_skill_contracts.py's NoSecondsClaim): today's pass took 12 min,
    so the launcher's help claims no seconds for a pass; it names the quick check, its log and the hold's seam — on the
    bare PATH, no external needed."""

    def test_help_claims_no_seconds_and_names_the_quick_check(self):
        r = self.run_dm("--help", PATH=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("seconds", r.stdout)
        for token in ("last-pass.log", "quick check", "FUSE_DM_HOLD"):
            with self.subTest(token=token):
                self.assertIn(token, r.stdout)


def bash_line(pattern, where="bin/fuse-dm"):
    """One contract literal read out of the bash script at test time — never a copy kept here (the Drift idiom of
    bootstrap/tests/test_fuse_start_contracts.py): whichever file changes, the other's pin fails."""
    match = re.search(pattern, SCRIPT.read_text(), flags=re.M)
    if not match:
        raise AssertionError(f"{where}: no line matches {pattern!r}")
    return match.group(1) if match.groups() else match.group(0)


def ps1_text():
    if not PS1.is_file():
        raise AssertionError(f"missing: {PS1.name} beside {SCRIPT.name} — the Windows entry point (OPX-1339)")
    return PS1.read_text(encoding="ascii")


def ascii_dash(text):
    """The bash messages use an em dash; the ps1 is 7-bit ASCII (Windows PowerShell 5.1 reads a BOM-less file as
    cp1252), so its messages carry ` - ` where bash has ` — `. Everything else is byte-identical."""
    return text.replace(" — ", " - ")


def ps1_code(line):
    """One ps1 line with its string literals emptied and its comment cut: what PowerShell runs, not what it says
    (OPX-1890: `'the re-check stopped (exit '` is a message, not an exit)."""
    line = re.sub(r"'(?:[^']|'')*'", "''", line)
    line = re.sub(r'"(?:[^"`]|`.)*"', '""', line)
    return line.split("#", 1)[0]


class Parity(unittest.TestCase):
    """OPX-1339 AC5: bin/fuse-dm.ps1 mirrors bin/fuse-dm verb for verb, flag for flag, message for message. Every contract
    literal is read FROM the bash script by regex at test time and asserted in the ps1 — in the ps1's spelling where the
    syntax differs (the table in `test_paths_map_to_the_ps1_spelling`), byte-identical where it must be — and the ps1's
    own literals are asserted back where bash or windows.md must carry them. Text only: runs everywhere, including the
    public tree (dm.sh beside dm.ps1)."""

    def test_defaults_and_seams_match_the_bash_script(self):
        """bash `MODEL="${FUSE_DM_MODEL:-fable}"` ↔ ps1 `Get-FuseDmSetting 'FUSE_DM_MODEL' 'fable'`: the env name and the
        default value travel together."""
        text = ps1_text()
        for var in ("MODEL", "EFFORT", "BOOTSTRAP_URL"):
            env, default = re.match(r"\$\{(FUSE_DM_\w+):-([^}]*)\}", bash_line(rf'^{var}="(\$\{{[^"]*\}})"$')).groups()
            with self.subTest(var=var):
                self.assertIn(f"Get-FuseDmSetting '{env}' '{default}'", text)
        retry = bash_line(r'^RETRY_MODEL="([a-z-]+)"$')
        self.assertEqual(retry, "opus")
        self.assertIn(f"$RetryModel = '{retry}'", text)

    def test_prompts_flags_and_state_words_are_identical(self):
        text = ps1_text()
        for literal in (bash_line(r'"(/fuse-start:begin)"'), bash_line(r'"(/deployment-manager:setup)"'),
                        bash_line(r'"(/deployment-manager:setup keep-current)"'),      # OPX-1366: the keep-current pass's prompt
                        "--model", "--effort", "--dangerously-skip-permissions", "--plugin-url",
                        "bootstrap-done", "needs-second-pass", "done"):
            with self.subTest(literal=literal):
                self.assertIn(literal, SCRIPT.read_text())
                self.assertIn(literal, text)
        # the daily argv: bash `--model "$MODEL" --permission-mode auto` ↔ ps1 `'--model', $script:Model, '--permission-mode', 'auto'`
        self.assertIn("--model \"$MODEL\" --permission-mode auto", SCRIPT.read_text())
        self.assertIn("'--model', $script:Model, '--permission-mode', 'auto'", text)
        for word in ("bootstrap-done", "needs-second-pass"):
            self.assertIn(f"-eq '{word}'", text, f"the loop tests the state word {word} exactly")

    def test_paths_map_to_the_ps1_spelling(self):
        """The mapping table — bash spelling ↔ ps1 spelling ($HomeDir is USERPROFILE, then HOME):
             $HOME/Fuse                    ↔ [IO.Path]::Combine($script:HomeDir, 'Fuse')
             $HOME/.fuse/dm-setup/state    ↔ [IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'state')
             $HOME/.fuse/dm-setup/plugin-version ↔ [IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'plugin-version')   (OPX-1366)
             $HOME/.local/bin/claude       ↔ [IO.Path]::Combine($script:HomeDir, '.local', 'bin', 'claude')
             $HOME/.fuse/dm-setup/claude-config-dir ↔ [IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'claude-config-dir')   (OPX-2024)
             --permission-mode auto        ↔ '--permission-mode', 'auto'
             ` — ` in a message            ↔ ` - `"""
        bash, text = SCRIPT.read_text(), ps1_text()
        table = {
            '"$HOME/Fuse"': "[IO.Path]::Combine($script:HomeDir, 'Fuse')",
            '"$HOME/.fuse/dm-setup/state"': "[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'state')",
            '"$HOME/.fuse/dm-setup/plugin-version"': "[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'plugin-version')",
            '"$HOME/.local/bin/claude"': "[IO.Path]::Combine($script:HomeDir, '.local', 'bin', 'claude')",
            '"$HOME/.fuse/dm-setup/claude-config-dir"': "[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'claude-config-dir')",
        }
        for bash_spelling, ps1_spelling in table.items():
            with self.subTest(path=bash_spelling):
                self.assertIn(bash_spelling, bash)
                self.assertIn(ps1_spelling, text)
        self.assertIn("~/" + STATE.as_posix(), text, "the help text names the state file as the bash help does")
        self.assertIn("~/" + RECORD.as_posix(), text, "and the version record (OPX-1366)")

    def test_messages_match_with_ascii_dashes(self):
        """The DM-facing lines: the three-things sentence (identical), the retry line, the two closing lines, the
        update-failed line and the installer line — read from bash, asserted in the ps1 with ` — ` → ` - `."""
        text = ps1_text()
        three = bash_line(r'^\s*say "(three things Claude asks the first time[^"]*)"$')
        self.assertIn(three, text)
        self.assertNotIn("—", three, "the sentence itself is ASCII: byte-identical in both")
        for pattern in (r'^\s*say "(this seat refused the model )\$MODEL',
                        r'^\s*say "(setup finished — from now on double-click Fuse Claude on your Desktop, or type fuse-dm)"$',
                        r'^\s*say "setup closed \(state: \$\{STATE:-none\}\)( — run fuse-dm setup again to finish; day to day, double-click Fuse Claude on your Desktop, or type fuse-dm)"$',
                        r'\|\| say "(the plugin update did not go through — the session opens on the copy you have; run fuse-dm update again later)"$',
                        r'^\s*say "(installing Claude Code — one minute)"$',
                        r'\|\| say "(the installer did not finish cleanly)"$',
                        r'^\s*say "the plugin moved to \$INSTALLED( — re-checking the environment first)"$'):   # OPX-1366; OPX-2020: `, seconds` dropped
            fragment = bash_line(pattern)
            with self.subTest(fragment=fragment[:40]):
                self.assertIn(ascii_dash(fragment), text)
        self.assertIn("- starting again on", text, "the retry line's second half, ASCII")
        self.assertIn("'the plugin moved to '", text, "the keep-current line's first half (OPX-1366)")

    def test_env_names_update_lines_and_the_launch_bound(self):
        text = ps1_text()
        for env in ("FUSE_DM_MODEL", "FUSE_DM_EFFORT", "FUSE_DM_BOOTSTRAP_URL", "FUSE_DM_INSTALLER", "FUSE_DM_LAUNCHER", "CLAUDE_CODE_EFFORT_LEVEL"):
            with self.subTest(env=env):
                self.assertIn(env, SCRIPT.read_text())
                self.assertIn(env, text)
        for line in (bash_line(r'"\$CLAUDE" (plugin marketplace update fuse-internal)'), bash_line(r'"\$CLAUDE" (plugin update deployment-manager@fuse-internal)')):
            with self.subTest(line=line):
                self.assertIn(line, text)
        bound = bash_line(r'while \[ "\$n" -lt (\d+) \]')
        self.assertEqual(bound, "3")
        self.assertIn(f"while ($n -lt {bound})", text)

    def test_windows_installer_line_is_the_bash_msys_line(self):
        """bash's msys branch names the official Windows line; the ps1 runs it and names it in the 69 message. The
        FUSE_DM_INSTALLER seam is a PowerShell expression, run in a CHILD shell (review round 1: the official
        install.ps1 ends every failure path with an `exit`, which in the launcher's own process would close the DM's
        console under iex, or end a file run with code 1 instead of 69) — the running host by absolute path, nothing
        from PATH, the shape of bash's `bash -c "$installer"`."""
        line = bash_line(r"powershell -command \"(irm https://claude\.ai/install\.ps1 \| iex)\"")
        text = ps1_text()
        self.assertIn(f"'{line}'", text)
        self.assertIn('bash -c "${FUSE_DM_INSTALLER:-', SCRIPT.read_text(), "bash runs the installer in a child process")
        self.assertIn("[Diagnostics.Process]::GetCurrentProcess().MainModule.FileName", text, "the running host, by absolute path")
        self.assertIn("-NoProfile -ExecutionPolicy Bypass -Command $installer", text, "the expression runs in that child")
        self.assertNotIn("Invoke-Expression", text, "never in this process: an `exit` inside the installer would be the launcher's")

    def test_verbs_mirror_and_install_shim_stays_bash(self):
        """`setup` | (none) | `update` on both; `install-shim` is bash's (the shim and the icon are written under Git
        Bash): the ps1 names it only to say so, and carries none of the shim's mechanics. OPX-1366: both scripts read
        the installed version from installed_plugins.json now, so that name leaves the shim-only tuple; `installPath`
        stays shim-only (the ps1 reads `version`, never the path)."""
        bash, text = SCRIPT.read_text(), ps1_text()
        for verb in ("setup", "update", "install-shim"):
            self.assertIn(f"  {verb}) ", bash)
        self.assertIn("'setup'", text)
        self.assertIn("'update'", text)
        self.assertIn("'install-shim'", text)
        self.assertIn("installed_plugins.json", text)
        for shim_only in ("installPath", "Fuse Claude.cmd", "shim_body"):
            with self.subTest(shim_only=shim_only):
                self.assertNotIn(shim_only, text)
        self.assertIn("usage: fuse-dm", text)

    def test_the_claude_folder_rule_is_the_bash_rule(self):
        """OPX-2024: the ps1 computes $InstalledPlugins from bin/fuse-dm's three steps — CLAUDE_CONFIG_DIR, the record
        install-shim writes, ~/.claude — and sets $env:CLAUDE_CONFIG_DIR for its claude calls only when the record decided
        it, as bash exports it (the Pester suite runs the cases); a value of the DM's own console is put back after."""
        bash, text = SCRIPT.read_text(), ps1_text()
        self.assertIn('INSTALLED_PLUGINS="$CLAUDE_DIR/plugins/installed_plugins.json"', bash)
        self.assertIn("$InstalledPlugins = [IO.Path]::Combine($script:ClaudeConfigDir, 'plugins', 'installed_plugins.json')", text)
        self.assertIn("[IO.Path]::Combine($script:HomeDir, '.claude')", text)
        self.assertIn('export CLAUDE_CONFIG_DIR="$CLAUDE_DIR"', bash)
        self.assertIn("$env:CLAUDE_CONFIG_DIR = $script:ClaudeConfigDir", text)
        self.assertIn("CLAUDE_CONFIG_DIR", text.split("param(", 1)[0], "the header says it")

    def test_the_ps1_only_seams_are_named_where_they_belong(self):
        """The Git-for-Windows branch is the ps1's alone: CLAUDE_CODE_GIT_BASH_PATH and the default bash.exe path are
        what the bash icon already names; FUSE_DM_GIT_BASH is the tests' seam, in the ps1's help and nowhere in bash.
        Runs everywhere (review round 1: the windows.md half is its own test, so nothing that ran reports skipped)."""
        text = ps1_text()
        self.assertIn("FUSE_DM_GIT_BASH", text)
        self.assertNotIn("FUSE_DM_GIT_BASH", SCRIPT.read_text())
        self.assertIn("CLAUDE_CODE_GIT_BASH_PATH", text)
        self.assertIn("Git\\bin\\bash.exe", text)
        self.assertIn("Git\\bin\\bash.exe", SCRIPT.read_text(), "the bash icon opens the same bash.exe")
        self.assertIn("https://git-scm.com/downloads/win", text)

    @unittest.skipUnless(WINDOWS_MD.is_file(), "no references/windows.md beside this copy (the public tree)")
    def test_winget_git_line_is_windows_md_row_two(self):
        """The ps1's winget line is windows.md row 2's, byte-identical (the Drift idiom), invoked as `& winget …` so
        the tests' fake winget on PATH is what runs; the variable and the fallback URL are what windows.md names."""
        text = ps1_text()
        windows = WINDOWS_MD.read_text()
        winget = re.search(r"^winget install -e --id Git\.Git$", windows, flags=re.M)
        self.assertIsNotNone(winget, "windows.md row 2's winget line")
        self.assertIn("& " + winget.group(0), text)
        self.assertIn("CLAUDE_CODE_GIT_BASH_PATH", windows)
        self.assertIn("https://git-scm.com/downloads/win", windows)

    def test_ps1_is_seven_bit_ascii_and_parses_for_5_1(self):
        """Windows PowerShell 5.1 reads a BOM-less file as cp1252 (raw.githubusercontent serves utf-8): pure ASCII, and
        none of the PowerShell-7-only syntax the plan rules out."""
        raw = PS1.read_bytes() if PS1.is_file() else None
        self.assertIsNotNone(raw, f"missing: {PS1}")
        self.assertTrue(raw.isascii(), sorted({b for b in raw if b > 127}))
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM")
        text = raw.decode("ascii")
        for pattern, why in ((r"\?\?", "the null-coalescing operator is PowerShell 7"), (r"\s\?\s.*\s:\s", "the ternary is PowerShell 7"),
                             (r"\s&&\s", "the && chain is PowerShell 7"), (r"\s\|\|\s", "the || chain is PowerShell 7"),
                             (r"\$IsWindows", "$IsWindows is PowerShell 6+"), (r"-AsHashtable", "PowerShell 6+"),
                             (r"\r\n", "LF only: the publish copies bytes")):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), why)

    def test_ps1_text_never_names_a_settings_file_or_bypass_permissions(self):
        """AC2 on the text: no settings file, no bypassPermissions, the bypass flag once (the setup argv), no write to
        the state file, no `2>&1` on the session (it would pipe stdout and kill the TUI), one `exit` and it is guarded."""
        text = ps1_text()
        for bad in ("settings.json", "settings.local.json", "defaultMode", "bypassPermissions", ".claude/settings", ".claude\\settings", "2>&1"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text)
        self.assertEqual(text.count("--dangerously-skip-permissions"), 1)
        self.assertNotRegex(text, r"(?m)^.*StateFile.*(Set-Content|Out-File|Add-Content|WriteAllText|WriteAllLines|\s>\s).*$", "the launcher only reads the state file")
        # OPX-1890: an `exit` statement, not the word inside a message — the T3 Code lines say `(exit N)` in a string
        exits = [l for l in text.splitlines() if re.search(r"(?<![\w$.-])exit\b", ps1_code(l), flags=re.I)]
        self.assertEqual(len(exits), 1, exits)
        self.assertIn("$PSCommandPath", exits[0], "under iex the body returns; only a file run exits")

    def test_t3_surface_literals_match(self):
        """OPX-1890 AC10: the T3 Code surface, literal for literal — the headless argv (`-p` right before the prompt),
        the `term` verb, the marker in the ps1's path spelling, the usage lines, the telemetry variable (bash: open's
        `--env` and the Git Bash prefix; ps1: set in its own process around the opener), and Windows closes nothing."""
        bash, text = SCRIPT.read_text(), ps1_text()
        for env in ("FUSE_DM_SURFACE", "FUSE_DM_T3_APP", "FUSE_DM_OPENER", "T3CODE_TELEMETRY_ENABLED"):
            with self.subTest(env=env):
                self.assertIn(env, bash)
                self.assertIn(env, text)
        self.assertIn('--model "$MODEL" --permission-mode auto -p "$KEEP_CURRENT_PROMPT"', bash)
        self.assertIn("'--model', $script:Model, '--permission-mode', 'auto', '-p', $script:KeepCurrentPrompt", text)
        self.assertIn("  term) ", bash)
        self.assertIn("'term'", text)
        self.assertIn('"$HOME/.fuse/dm-setup/t3-ready"', bash)
        self.assertIn("[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 't3-ready')", text)
        self.assertEqual(bash_line(r"^USAGE='([^']*)'$"), USAGE)
        self.assertIn(f"$Usage = '{PS1_USAGE}'", text)
        self.assertIn("--env T3CODE_TELEMETRY_ENABLED=false -a", bash)
        self.assertIn("T3CODE_TELEMETRY_ENABLED=false \"${FUSE_DM_OPENER:-cmd}\" //c start \"\"", bash)
        self.assertIn("\"${FUSE_DM_OPENER:-open}\"", bash)
        self.assertIn("$env:T3CODE_TELEMETRY_ENABLED = 'false'", text)
        self.assertNotIn("osascript", text, "Windows closes no window")
        # Axel's Windows status (10-05): the app's folder is `t3code` under %LOCALAPPDATA%\Programs, the .exe one level down
        self.assertIn('"$HOME"/AppData/Local/Programs/t3code/T3\\ Code*.exe', bash)
        self.assertIn("[IO.Path]::Combine($env:LOCALAPPDATA, 'Programs', 't3code')", text)
        self.assertIn("-Filter 'T3 Code*.exe'", text)

    def test_t3_messages_match_with_ascii_dashes(self):
        """OPX-1890: the T3 Code lines, read from bash, asserted in the ps1 with ` — ` → ` - `; the two `(exit N)` lines
        by their fixed halves, whatever the variable is called."""
        bash, text = SCRIPT.read_text(), ps1_text()
        for pattern in (r'say "the plugin moved to \$INSTALLED( — re-checking the environment first, then T3 Code opens by itself '
                        r'\(threads already open there keep the old version until you start a new one\))"',
                        r'say "(T3 Code is not where setup put it — the terminal session instead; fuse-dm setup puts it back)"',
                        r'say "(opening T3 Code)"',
                        r'say "(T3 Code did not open \(exit )\$\{?[A-Za-z_]+\}?(\) — the terminal session instead)"',
                        r'say "(the re-check stopped \(exit )\$\{?[A-Za-z_]+\}?(\) — opening it in the terminal instead)"'):
            match = re.search(pattern, bash, flags=re.M)
            with self.subTest(pattern=pattern[:48]):
                self.assertIsNotNone(match, f"bin/fuse-dm: no line matches {pattern!r}")
                for fragment in match.groups():
                    self.assertIn(ascii_dash(fragment), text)

    def test_quick_pass_literals_match(self):
        """OPX-2020 (decisions C, E, H): the quick pass, literal for literal — its prompt (byte-identical), its argv
        (`--effort low` between the permission mode and `-p`), the log in each script's path spelling, and the hold's seam:
        bash `sleep "$HOLD"`, the ps1 Start-Sleep, FUSE_DM_HOLD taken only when it matches `^\\d+$` (else 10). The ps1's
        garbage-hold case is this text pin: Pester cannot fake a cmdlet in the child, and the default would cost 10 s."""
        bash, text = SCRIPT.read_text(), ps1_text()
        self.assertIn(f'"{QUICK_PROMPT}"', bash)
        self.assertIn(f"'{QUICK_PROMPT}'", text)
        self.assertIn('--model "$MODEL" --permission-mode auto --effort low -p "$QUICK_PROMPT"', bash)
        self.assertIn("'--model', $script:Model, '--permission-mode', 'auto', '--effort', 'low', '-p', $script:QuickPrompt", text)
        self.assertIn('"$HOME/.fuse/dm-setup/last-pass.log"', bash)
        self.assertIn("[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'last-pass.log')", text)
        self.assertIn("FUSE_DM_HOLD", bash)
        self.assertIn("FUSE_DM_HOLD", text)
        self.assertIn('sleep "$HOLD"', bash)
        self.assertIn("Start-Sleep -Seconds", text)
        self.assertIn(r"'^\d+$'", text, "the ps1 takes FUSE_DM_HOLD only when it is all digits")

    def test_quick_pass_messages_match_with_ascii_dashes(self):
        """OPX-2020: the quick pass's lines in both scripts — bash with ` — `, the ps1 with ` - ` — by their fixed halves
        (the version and the exit code are variables): MOVED_QUICK, the log's header, the details suffix, NO_RESULT and
        QUICK_FAILED; and `term`'s moved line has dropped its `, seconds` in both."""
        bash, text = SCRIPT.read_text(), ps1_text()
        fragments = (T3Machine.MOVED_QUICK.split("{v}", 1)[1], T3Machine.LOG_HEADER.split("{v}", 1)[0][len("fuse-dm: "):],
                     T3Machine.DETAILS, T3Machine.NO_RESULT, *T3Machine.QUICK_FAILED.split("{rc}"))
        for fragment in fragments:
            with self.subTest(fragment=fragment[:40]):
                self.assertIn(fragment, bash)
                self.assertIn(ascii_dash(fragment), text)
        for where, body in (("bin/fuse-dm", bash), (PS1.name, text)):
            with self.subTest(script=where):
                self.assertNotIn("re-checking the environment first, seconds", body)

    def test_ps1_help_says_the_quick_check(self):
        """OPX-2020 AC6, the ps1 half of NoSecondsClaim: Show-Help names the quick check, its log and FUSE_DM_HOLD, and
        claims no seconds."""
        text = ps1_text()
        start = text.index("function Show-Help {")
        body = text[start:text.index("\n}\n", start)]
        self.assertNotIn("seconds", body)
        for token in ("last-pass.log", "quick check", "FUSE_DM_HOLD"):
            with self.subTest(token=token):
                self.assertIn(token, body)


class Hygiene(unittest.TestCase):
    def test_no_machine_paths_or_secrets(self):
        for f in (SCRIPT, PS1, Path(__file__)):
            with self.subTest(file=f.name):
                text = f.read_text() if f.is_file() else ""
                self.assertTrue(text, f"missing: {f}")
                self.assertNotIn("/" + "Users/", text)
                self.assertNotIn("\\" + "Users\\", text)
                self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", text))


if __name__ == "__main__":
    unittest.main()
