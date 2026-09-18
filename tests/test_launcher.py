"""Behaviour tests for bin/fuse-dm (OPX-1331): the real script under /bin/bash (3.2 on this Mac) with a fake `claude` in
a stub bin that is FIRST and ONLY on PATH (the five coreutils the script needs are linked in beside it, so the suite
also pins that dependency set), a temp HOME, nothing of the machine's. The fake records every invocation as
$FAKE_DIR/launch.<n> (argv0, one arg= line per argument, the cwd, the env it saw, and what stdin is — a terminal or
not, the /dev/tty alias device or not, OPX-1373), answers `plugin list` from
$FAKE_PLUGIN_LIST, writes the state file when FAKE_CLAUDE_WRITE_STATE tells it to (the nth comma-separated value on the
nth session launch) and exits with FAKE_CLAUDE_EXIT's nth value, FAKE_CLAUDE_STDERR on stderr — so the relaunch loop,
the stage choice and the opus retry are all driven from the outside. The real `claude` is never executed.

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
STATE = Path(".fuse") / "dm-setup" / "state"
SHIM = Path(".fuse") / "bin" / "fuse-dm"
# The externals the script may call (`bash` is what the shim execs the plugin's copy with). Anything else is a
# `command not found` under the bare PATH — the dependency set is part of the contract (Git Bash ships all of them).
TOOLS = ("bash", "mkdir", "tee", "awk", "chmod", "rm")

PLUGIN_LIST_ABSENT = "Installed plugins:\n\n  ❯ slack@claude-plugins-official\n    Version: 1.0.0\n    Scope: user\n    Status: ✔ enabled\n"
PLUGIN_LIST_PRESENT = PLUGIN_LIST_ABSENT + "\n  ❯ deployment-manager@fuse-internal\n    Version: 0.0.0\n    Scope: user\n    Status: ✔ enabled\n"

FAKE_CLAUDE = r'''#!/bin/sh
# fake claude (OPX-1331): see the module docstring. Only builtins and mkdir (linked into the stub bin).
n=0; [ -f "$FAKE_DIR/count" ] && read -r n < "$FAKE_DIR/count"; n=$((n+1)); echo "$n" > "$FAKE_DIR/count"
rec="$FAKE_DIR/launch.$n"
printf 'argv0=%s\n' "$0" > "$rec"
for a in "$@"; do printf 'arg=%s\n' "$a" >> "$rec"; done
printf 'cwd=%s\nlauncher=%s\neffort_env=%s\n' "$(pwd -P)" "${FUSE_DM_LAUNCHER:-unset}" "${CLAUDE_CODE_EFFORT_LEVEL:-unset}" >> "$rec"
# OPX-1373: what stdin is. `-ef` compares device+inode: an fd opened from /dev/tty matches /dev/tty, a dup of the
# terminal's own fd does not — and the alias device is the one Bun's kqueue refuses.
printf 'stdin_tty=%s\nstdin_is_dev_tty=%s\n' "$([ -t 0 ] && echo yes || echo no)" "$([ /dev/fd/0 -ef /dev/tty ] && echo yes || echo no)" >> "$rec"
case "${1:-} ${2:-}" in
  "plugin list") printf '%s\n' "${FAKE_PLUGIN_LIST:-}"; exit 0 ;;
  "plugin marketplace"|"plugin update") exit "${FAKE_UPDATE_RC:-0}" ;;
esac
s=0; [ -f "$FAKE_DIR/sessions" ] && read -r s < "$FAKE_DIR/sessions"; s=$((s+1)); echo "$s" > "$FAKE_DIR/sessions"
nth() { v="$1"; i="$2"; while [ "$i" -gt 1 ]; do case "$v" in *,*) v="${v#*,}" ;; *) v="" ;; esac; i=$((i-1)); done; printf '%s' "${v%%,*}"; }
st="$(nth "${FAKE_CLAUDE_WRITE_STATE:-}" "$s")"
if [ -n "$st" ]; then mkdir -p "$HOME/.fuse/dm-setup"; printf '%s\n' "$st" > "$HOME/.fuse/dm-setup/state"; fi
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
                "FUSE_DM_INSTALLER": str(self.installer_noop), "FAKE_CLAUDE_BODY": FAKE_CLAUDE}
        for k, v in extra.items():
            if v is None:
                base.pop(k, None)
            else:
                base[k] = v
        return base

    def run_dm(self, *args, script=None, **extra):
        return subprocess.run([BASH, str(script or SCRIPT), *args], env=self.env(**extra), capture_output=True, text=True, timeout=60)

    def launches(self):
        """Every invocation of the fake, in order: {argv0, args, cwd, launcher, effort_env, stdin_tty, stdin_is_dev_tty}."""
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

    def plugin_copy(self, name):
        root = self.tmp / name
        write_exec(root / "bin" / "fuse-dm", FAKE_PLUGIN_COPY)
        (self.home / ".claude" / "plugins").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
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
             $HOME/.local/bin/claude       ↔ [IO.Path]::Combine($script:HomeDir, '.local', 'bin', 'claude')
             --permission-mode auto        ↔ '--permission-mode', 'auto'
             ` — ` in a message            ↔ ` - `"""
        bash, text = SCRIPT.read_text(), ps1_text()
        table = {
            '"$HOME/Fuse"': "[IO.Path]::Combine($script:HomeDir, 'Fuse')",
            '"$HOME/.fuse/dm-setup/state"': "[IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'state')",
            '"$HOME/.local/bin/claude"': "[IO.Path]::Combine($script:HomeDir, '.local', 'bin', 'claude')",
        }
        for bash_spelling, ps1_spelling in table.items():
            with self.subTest(path=bash_spelling):
                self.assertIn(bash_spelling, bash)
                self.assertIn(ps1_spelling, text)
        self.assertIn("~/" + STATE.as_posix(), text, "the help text names the state file as the bash help does")

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
                        r'\|\| say "(the installer did not finish cleanly)"$'):
            fragment = bash_line(pattern)
            with self.subTest(fragment=fragment[:40]):
                self.assertIn(ascii_dash(fragment), text)
        self.assertIn("- starting again on", text, "the retry line's second half, ASCII")

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
        Bash): the ps1 names it only to say so, and carries none of the shim's mechanics."""
        bash, text = SCRIPT.read_text(), ps1_text()
        for verb in ("setup", "update", "install-shim"):
            self.assertIn(f"  {verb}) ", bash)
        self.assertIn("'setup'", text)
        self.assertIn("'update'", text)
        self.assertIn("'install-shim'", text)
        for shim_only in ("installed_plugins.json", "installPath", "Fuse Claude.cmd", "shim_body"):
            with self.subTest(shim_only=shim_only):
                self.assertNotIn(shim_only, text)
        self.assertIn("usage: fuse-dm", text)

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
        exits = [l for l in text.splitlines() if "exit " in l]
        self.assertEqual(len(exits), 1, exits)
        self.assertIn("$PSCommandPath", exits[0], "under iex the body returns; only a file run exits")


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
