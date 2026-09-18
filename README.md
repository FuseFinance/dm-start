# dm-start

The one line a Fuse deployment manager pastes on a new laptop.

macOS (Terminal):

```
curl -fsSL https://raw.githubusercontent.com/FuseFinance/dm-start/main/dm.sh | bash
```

Windows (PowerShell, before Git for Windows exists):

```
irm https://raw.githubusercontent.com/FuseFinance/dm-start/main/dm.ps1 | iex
```

`dm.ps1` is not published yet — it arrives with OPX-1339; until then, on Windows, install Git for Windows and paste the `curl` line into Git Bash.

## What runs

`dm.sh` is the Fuse launcher. Read from the pipe with no arguments, it runs setup:

1. It installs Claude Code when the computer has none (the official installer), and says the three things Claude asks the first time.
2. **Part one** — a Claude session with this repository's `fuse-start` plugin loaded for that one session (`claude --plugin-url …/fuse-start.zip`, the release asset). Claude installs git, the GitHub CLI, Node and Python without an administrator password, signs the person in to GitHub in the browser, checks that their account can read the private plugin repository `https://github.com/FuseFinance/internal-skills`, and installs the three plugins: `deployment-manager` (from the private marketplace), `slack` and `linear` (from `claude-plugins-official`).
3. **Part two** — a new Claude session on the installed `deployment-manager` plugin finishes the environment. The launcher opens it by itself after part one.

From then on, `fuse-dm` typed in a terminal (or the **Fuse Claude** icon on the Desktop) is the day's session. `dm.sh` is `bin/fuse-dm` from the private plugin, copied here byte for byte at each publish.

## What is in this repository

| File | What it is |
| --- | --- |
| `dm.sh` | the launcher (bash 3.2 and Git Bash) |
| `dm.ps1` | the PowerShell entry for a Windows machine before Git for Windows (arrives with OPX-1339) |
| `fuse-start/` | the bootstrap plugin: one skill, `/fuse-start:begin` |
| `tests/test_launcher.py` | the launcher's behaviour tests, run by CI against `dm.sh` with a fake `claude` |
| `scripts/testlib/` | the fake-executable helper those tests import (`fake_exec.py`, stdlib only: hard links to one assessed shim, so macOS never re-assesses a fresh stub) |
| `dist/fuse-start.zip` | the release asset the launcher fetches (one top folder, `fuse-start/`) |

Nothing here holds a secret, a client name or a credential: the plugin's only internal URL is the private plugin repository's. Everything client-specific lives in the private plugin, which needs the GitHub access part one proves.

## Publishing

This repository is published from `FuseFinance/internal-skills` by `scripts/publish-dm-start.sh <checkout>`; nothing is edited here by hand. Pushes are limited to the maintainers: whoever can push here runs code on every new DM laptop with prompts off.
