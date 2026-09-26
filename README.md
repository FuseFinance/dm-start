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

## What runs

`dm.sh` is the Fuse launcher. Read from the pipe with no arguments, it runs setup:

1. It installs Claude Code when the computer has none (the official installer), and says the three things Claude asks the first time.
2. **Part one** — a Claude session with this repository's `fuse-start` plugin loaded for that one session (`claude --plugin-url …/fuse-start.zip`, the release asset). Claude installs git, the GitHub CLI, Node and Python without an administrator password, signs the person in to GitHub in the browser, checks that their account can read the private plugin repository `https://github.com/FuseFinance/internal-skills`, and installs the three plugins: `deployment-manager` (from the private marketplace), `slack` and `linear` (from `claude-plugins-official`).
3. **Part two** — a new Claude session on the installed `deployment-manager` plugin finishes the environment. The launcher opens it by itself after part one.

`dm.ps1` is the same launcher for Windows PowerShell (5.1 and 7), read through `iex` with no arguments. Before it does anything `dm.sh` does, it installs Git for Windows when there is none (`winget install -e --id Git.Git`) and sets `CLAUDE_CODE_GIT_BASH_PATH` to `C:\Program Files\Git\bin\bash.exe` in the same PowerShell — no new window, no PATH refresh — so Claude Code opens with a Bash tool from its first session. Then it matches `dm.sh` step for step: Claude Code when missing, the three things, part one, part two, the same state file. `install-shim` stays with `dm.sh`, which the Desktop icon runs under Git Bash.

From then on, `fuse-dm` typed in a terminal (or the **Fuse Claude** icon on the Desktop) is the day's session. `dm.sh` is `bin/fuse-dm` and `dm.ps1` is `bin/fuse-dm.ps1` from the private plugin, copied here byte for byte at each publish.

## What is in this repository

| File | What it is |
| --- | --- |
| `dm.sh` | the launcher (bash 3.2 and Git Bash) |
| `dm.ps1` | the same launcher for a Windows machine before Git for Windows (Windows PowerShell 5.1 and 7) |
| `people-ops.sh` | the People Ops one line (bash 3.2): Claude Code when missing, then one session on `/fuse-start:people-ops` |
| `fuse-start/` | the bootstrap plugin: `/fuse-start:begin` for deployment managers, `/fuse-start:people-ops` for People Ops |
| `tests/test_launcher.py` | the launcher's behaviour tests, run by CI against `dm.sh` with a fake `claude`; its `Parity` class pins `dm.ps1` to `dm.sh` |
| `tests/fuse-dm.Tests.ps1` | the Pester suite for `dm.ps1`, run by CI on `windows-latest` under Windows PowerShell 5.1 and pwsh with a fake `claude` and a fake `winget` |
| `scripts/testlib/` | the fake-executable helper those tests import (`fake_exec.py`, stdlib only: hard links to one assessed shim, so macOS never re-assesses a fresh stub) |
| `dist/fuse-start.zip` | the release asset the launcher fetches (one top folder, `fuse-start/`) |

Nothing here holds a secret, a client name or a credential: the plugin's only internal URL is the private plugin repository's. Everything client-specific lives in the private plugin, which needs the GitHub access part one proves.

## People Ops

The one line a Fuse People Ops teammate pastes into Terminal on a new Mac, once:

```
curl -fsSL https://raw.githubusercontent.com/FuseFinance/dm-start/main/people-ops.sh | bash
```

`people-ops.sh` installs Claude Code when the Mac has none, then opens one Claude session with this repository's `fuse-start` plugin loaded for that session only, running `/fuse-start:people-ops`: git, the GitHub CLI and Node without an administrator password, one GitHub sign-in in the browser, the access check to `https://github.com/FuseFinance/internal-skills`, and the `people-ops` plugin from the private marketplace. Then the person types `/exit`, opens the Claude app (downloading it from claude.ai/download first if it isn't on their Mac yet, and signing in), chooses **Code**, starts a new session in the folder Fuse → studio in their home folder, and types `/people-ops:studio-setup`.

## Publishing

This repository is published from `FuseFinance/internal-skills` by `scripts/publish-dm-start.sh <checkout>`; nothing is edited here by hand. Pushes are limited to the maintainers: whoever can push here runs code on every new DM laptop with prompts off.
