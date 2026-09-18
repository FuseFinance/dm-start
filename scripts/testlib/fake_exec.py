"""Fake executables for tests, without macOS's first-exec assessment.

macOS assesses every new executable file the first time it is executed, through one
system-wide daemon: a fresh script costs ~1 s, a hard link to an already-executed file
~10-20 ms — the assessment attaches to the inode (spec 2026-09-17-fast-iteration-test-gate-design
§3). `write_exec` writes the test's body to `<path>.body` and hard-links `<path>` to one shim
per interpreter, executed once when it is created. A hard link, not a symlink: production code
under test may `lstat` its executables and deliberately reject symlinks as a security check
(fix round 2, 2026-09-17 — Keeper credential-wrapper detection did exactly this), and a hard
link is an ordinary regular file under `lstat`, sharing the shim's inode and its `0555` mode.
Shell bodies are sourced, so `$0` inside the body is the stub's own path, `$@` and the exit code
pass through unchanged.

Never chmod a stub — it is a hard link to a shared shim: its mode is the shim's, and the shim cache is
shared by every worktree on the machine. (`shim_for` rebuilds a shim whose mode is not 0555, but every
stub already linked to the damaged inode keeps it.) Test "present but not executable" with a plain
`write_text`.

    write_exec(bin / "tool", "#!/bin/sh\\necho \\"$*\\" >> calls\\nexit 0\\n")

`FAKE_EXEC_SHIM_DIR` moves the shim cache (tests of this module; CI).
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

DEFAULT_INTERPRETER = "/bin/sh"
_SHELLS = {"sh", "bash", "dash", "zsh"}


def shim_dir() -> Path:
    override = os.environ.get("FAKE_EXEC_SHIM_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "internal-skills" / "shims"
    base.mkdir(parents=True, exist_ok=True)
    return base


def interpreter_for(body: str) -> str:
    first = body.split("\n", 1)[0]
    if first.startswith("#!"):
        return first[2:].strip() or DEFAULT_INTERPRETER
    return DEFAULT_INTERPRETER


def shim_text(interpreter: str) -> str:
    name = interpreter.split()[-1].rsplit("/", 1)[-1]
    if name in _SHELLS:
        # sourced: `$0` is the link the caller ran, "$@" the caller's arguments
        return '#!/bin/sh\nexec %s -c \'. "$0.body"\' "$0" "$@"\n' % interpreter
    return '#!/bin/sh\nexec %s "$0.body" "$@"\n' % interpreter


def shim_for(interpreter: str) -> Path:
    text = shim_text(interpreter)
    target = shim_dir() / hashlib.sha256(text.encode()).hexdigest()[:16]
    if not target.exists() or stat.S_IMODE(target.stat().st_mode) != 0o555:  # missing, or a chmod through a stub
        tmp = target.with_name(f"{target.name}.{os.getpid()}")
        tmp.write_text(text)
        tmp.chmod(0o555)
        os.replace(tmp, target)
        # pay the assessment once, here; the missing `.body` makes it exit non-zero, which is fine
        subprocess.run([str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target


def write_exec(path, body: str, *, interpreter: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(path.name + ".body").write_text(body)
    if path.is_symlink() or path.exists():
        path.unlink()
    shim = shim_for(interpreter or interpreter_for(body))
    try:
        os.link(shim, path)
    except OSError:  # cross-device, or no links at all (some Windows setups): the slow path is still a correct one
        path.write_text(body)
        path.chmod(0o755)
    return path
