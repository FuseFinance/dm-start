"""Behaviour tests for bin/fuse-dm (OPX-1331): the real script under /bin/bash (3.2 on this Mac) with a fake `claude` in
a stub bin that is FIRST and ONLY on PATH (the five coreutils the script needs are linked in beside it, so the suite
also pins that dependency set), a temp HOME, nothing of the machine's. The fake records every invocation as
$FAKE_DIR/launch.<n> (argv0, one arg= line per argument, the cwd, the env it saw), answers `plugin list` from
$FAKE_PLUGIN_LIST, writes the state file when FAKE_CLAUDE_WRITE_STATE tells it to (the nth comma-separated value on the
nth session launch) and exits with FAKE_CLAUDE_EXIT's nth value, FAKE_CLAUDE_STDERR on stderr — so the relaunch loop,
the stage choice and the opus retry are all driven from the outside. The real `claude` is never executed.

Run: python3 deployment-manager/setup/tests/test_launcher.py
"""
from pathlib import Path
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
        """Every invocation of the fake, in order: {argv0, args, cwd, launcher, effort_env}."""
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


class Hygiene(unittest.TestCase):
    def test_no_machine_paths_or_secrets(self):
        for f in (SCRIPT, Path(__file__)):
            with self.subTest(file=f.name):
                text = f.read_text()
                self.assertNotIn("/" + "Users/", text)
                self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", text))


if __name__ == "__main__":
    unittest.main()
