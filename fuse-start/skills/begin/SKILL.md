---
name: begin
description: Use when the Fuse one-line setup has just opened this session on a deployment manager's laptop (`fuse-dm setup`, part one, the deployment-manager plugin not installed yet), when git, the GitHub CLI, Node or Python 3 are missing on a Mac without Homebrew or on a Windows laptop with no administrator password, when a GitHub sign-in or the access check to FuseFinance/internal-skills is pending, or when the Fuse plugins have to be installed from the marketplace for the first time.
disable-model-invocation: true
---

## 1. Say which version you are, then what happens

Open your first response with exactly this line, nothing above it:

`Version: fuse-start 1.0.0 · begin`

Then three plain sentences, and nothing else before row 2:

> This is part one of the Fuse setup, running on your own computer. I install the few tools Claude needs (git, the GitHub CLI, Node and Python), sign you in to GitHub, check that Fuse gave your account access, and install the Fuse plugins. The only thing you do is approve one GitHub sign-in in your browser; everything else runs here.

## How this runs

**The person running this is a deployment manager, not an engineer.** Every command here is something **you** run. Guided never means handing them an error to interpret or a command to run — not now, not "later, once access lands", not "to double check". Say one line before anything that will visibly happen on their screen.

**Check before you install.** Every row opens with a detection command; the launcher runs this part again after an interruption, and the second run has to cost seconds.

**A budget per row, not one attempt.** A step fails: read the error, name the cause, then up to two different remedies, the references first, then your own judgment, about five minutes a row. Still failing → the row degrades, its report line carries the diagnosis. Sign-ins and the approval round stay at one ask each. The budget is your own work: never debugging with them.

**Seven guardrails hold whatever the remedy:**

1. No admin elevation the references do not name: no `sudo`, no administrator password, even offered.
2. No shell-profile edit beyond the documented lines: a new binary is called by its absolute path.
3. Nothing of theirs deleted.
4. Never the installed plugin copy.
5. Never a credential in the chat; a pasted fragment is refused and never repeated — not in the reply, your notes or any file.
6. Never a scheduled task or service registered by hand.
7. Never a command handed to them to paste: every line here is yours, and the re-check after an access grant is yours too.

The documented shell-profile line is exactly one: row 3's `export PATH=…` for `~/.local`, appended once. An installer script that edits the profile by itself (nvm's) is not a documented line.

| Excuse | Reality |
|---|---|
| "Run `claude plugin list` any time to double check" · "the commands below are what I'd hand to Erick to run on his own machine, later" | Nothing is handed to them to run, now or later: the check is yours, and the re-check after an access grant is yours, when they say it landed. |
| "I'll give you the exact command to type directly into your own terminal so it never has to be pasted into a chat" | A credential goes nowhere: not the chat, not a line they type, not a file, not an environment variable. The sign-in is the browser flow of row 4 and nothing else. |
| "I am not exporting or otherwise using the token you pasted (`ghp_…`)" | Writing any of its characters, even to disown them, is the repetition. It is "the fragment" or "what you pasted" — in the reply, your notes, any file — and nothing else. |
| "its characters do not appear except as already quoted in the prompt itself" · "(`ghp_…`)" | The prompt or the transcript holding it is not a licence. A prefix, an ellipsis or "the one starting with…" is the fragment; it stays "the fragment" in every place you write — the report and the notes included. |
| "In a few seconds your terminal will show a one-time code … open that link (it may open automatically)" | They never read the terminal. You read the code off `gh`'s stderr and relay it in the chat; the browser is theirs to open. |
| "pinning the installer to a specific reviewed tag avoids silently running whatever nvm's script looks like" | No third-party installer script through bash with prompts off: Node is the nodejs.org tarball unpacked under `~/.local`, and nothing appends to `~/.zshrc` but the one PATH line. |
| "`winget --scope user` … the smallest single command" | The winget line is the pinned one, id and flags exactly: `winget install -e --id Python.Python.3.12`. No scope flag, no other id. |
| "start a fresh login shell so this session re-reads the PATH" · "I need to restart this Git Bash session" | A winget install never reaches the running shell's PATH; the absolute path is the whole fix. Never `exec bash -l`, never a restart, never a new window asked of them. |
| "Can you ping your Fuse contact (or IT) and ask them to add your GitHub username" | The message is yours to write, with the username filled in (row 5); they only forward it. |

## 2. Is this session on their machine, and which surface

Check silently:

```bash
uname -s 2>/dev/null; uname -a 2>/dev/null; ls ~; git config --get user.email
```

An empty or synthetic home, no git identity and no clones anywhere means a cloud session — not their mistake. The recipe, once, then stop:

> I'm running in the cloud right now, so I can't reach the client's code or your GitHub; for this I need to run **on your own computer**. Two ways:
>
> - **If your new-task screen has a selector at the top right** ("In the cloud" / "On your computer"): start a **new task**, set it to *On your computer*, and paste your request there.
> - **If you don't see that selector**: open **Claude Code on your computer** and ask me the same thing there.
>
> Either way, paste this to pick up where we left off: *"<repeat their request in one line>"*.

Do not assume the selector exists.

| `uname -s` | Surface | Row 3 recipe |
| --- | --- | --- |
| `Darwin` | macOS | Homebrew when `/opt/homebrew/bin/brew` or `/usr/local/bin/brew` exists; user space under `~/.local` when it does not |
| `MINGW64_NT…` / `MSYS_NT…` | Native Windows, Git Bash | `winget`, the four pinned ids |
| `Linux` + `microsoft` in `/proc/version` | WSL | git and python3 detected (apt when missing); `gh` and Node from the Linux archives under `~/.local` |

Anything else stops here with one line: this part runs on macOS and Windows; ask your FDE.

## 3. git, gh, Node 22 or later, Python 3

Detect first, every candidate with `--version` — a bare `command -v` is not detection (a fresh Windows carries a Microsoft Store stub named `python3` that fails only with an argument):

```bash
git --version; gh --version; node --version; python3 --version || python --version || py -3 --version
```

Pin what answered by absolute path and use those paths from here on; after every install below, re-resolve **by absolute path** — a fresh install is not on this shell's PATH (`$PY` is the first candidate that answered `--version`, never a bare `command -v python3`):

```bash
GIT=$(command -v git); GH=$(command -v gh); for p in python3 python; do $p --version >/dev/null 2>&1 && PY="$(command -v $p)" && break; done; echo "GIT=$GIT GH=$GH PY=$PY"
```

**Nothing that needs an administrator password.** No `sudo`, no `.pkg` installer, never "install Homebrew first" (its installer needs the admin password IT does not give them), never a download page as the first move.

### Mac with Homebrew

```bash
brew install git gh node
```

Then `/opt/homebrew/bin/{git,gh,node}` on Apple Silicon, `/usr/local/bin/{git,gh,node}` on Intel. Apple's Git at `/usr/bin/git` is a real Git: keep it when it answered. Python: `/usr/bin/python3` or `/opt/homebrew/bin/python3` answered → nothing to install.

### Mac without Homebrew

Everything lands under `~/.local`, by commands you run:

- **git**: `/usr/bin/git` answers → keep it. It asks for the command line tools instead → `xcode-select --install` opens Apple's dialog (no password); say one line first, then wait for them.
- **gh**: the release archive from `github.com/cli/cli`, into `~/.local/bin`:

```bash
mkdir -p ~/.local/bin && cd "$(mktemp -d)" \
  && GH_VERSION="$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')" \
  && curl -fsSL -o gh.zip "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_macOS_$(uname -m | sed 's/x86_64/amd64/').zip" \
  && unzip -q gh.zip && cp gh_*/bin/gh ~/.local/bin/gh && chmod +x ~/.local/bin/gh && ~/.local/bin/gh --version
```

- **Node 22**: the nodejs.org darwin tarball, into `~/.local/node` — never `nvm`, never an installer script fetched and run through bash:

```bash
mkdir -p ~/.local && cd "$(mktemp -d)" \
  && NODE_FILE="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | sed -n "s/.*\(node-v22[^ ]*-darwin-$(uname -m | sed 's/x86_64/x64/')\.tar\.gz\).*/\1/p" | head -1)" \
  && curl -fsSL -o node.tar.gz "https://nodejs.org/dist/latest-v22.x/${NODE_FILE}" \
  && tar -xzf node.tar.gz && rm -rf ~/.local/node && mv "${NODE_FILE%.tar.gz}" ~/.local/node && ~/.local/node/bin/node --version
```

- **The one PATH line**, appended once to `~/.zshrc` when it is not there yet, for the sessions after this one (in this one, `~/.local/bin/gh` and `~/.local/node/bin/node` by absolute path):

```bash
grep -qs 'HOME/.local/node/bin' ~/.zshrc || printf '\nexport PATH="$HOME/.local/node/bin:$HOME/.local/bin:$PATH"\n' >> ~/.zshrc
```

- **Python**: `/usr/bin/python3` answered → keep it, whatever its minor version.

### Windows, Git Bash

`winget` replaces `brew`, and the ids are exact — `-e --id`, nothing added, nothing dropped:

```powershell
winget install -e --id Git.Git
winget install -e --id GitHub.cli
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Python.Python.3.12
```

Only the missing ones. **A winget install never reaches the running shell's PATH**: the absolute path is the fix, and the only one — never `exec bash -l`, never "restart this Git Bash", never a new window, never a Settings page.

- `/c/Program Files/Git/bin/git`, `/c/Program Files/GitHub CLI/gh.exe`, `/c/Program Files/nodejs/node.exe`
- Python: `~/AppData/Local/Programs/Python/Python312/python.exe` (the per-user default), else `/c/Program Files/Python312/python.exe`; verify with `--version`.

`python3 --version` printing `Python was not found` and exiting 9009 is the Microsoft Store alias under `WindowsApps` — a stub that opens the Store, not an interpreter: never run it bare, never install from the Store, never `--scope`, never elevation. The winget line above is the only install.

### WSL

`git --version` and `python3 --version` decide, not the distro's name: Ubuntu's WSL image ships both, Debian's ships neither. Both answer → keep them. One missing → `sudo apt-get install -y git python3`, the one elevation this file names: the distro's own password, set when WSL was installed, never a Windows administrator. `gh` and Node come from the Linux archives (cli/cli ships Linux as `.tar.gz`, `uname -m` says `aarch64` on arm64), into `~/.local`, the PATH line into `~/.bashrc`:

```bash
mkdir -p ~/.local/bin && cd "$(mktemp -d)" \
  && GH_VERSION="$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')" \
  && curl -fsSL -o gh.tar.gz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" \
  && tar -xzf gh.tar.gz && cp gh_*/bin/gh ~/.local/bin/gh && chmod +x ~/.local/bin/gh && ~/.local/bin/gh --version
```

```bash
mkdir -p ~/.local && cd "$(mktemp -d)" \
  && NODE_FILE="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | sed -n "s/.*\(node-v22[^ ]*-linux-$(uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/')\.tar\.gz\).*/\1/p" | head -1)" \
  && curl -fsSL -o node.tar.gz "https://nodejs.org/dist/latest-v22.x/${NODE_FILE}" \
  && tar -xzf node.tar.gz && rm -rf ~/.local/node && mv "${NODE_FILE%.tar.gz}" ~/.local/node && ~/.local/node/bin/node --version
```

Keep a running tally: which rows were already there, which you installed, which failed after their budget.

## 4. Sign them in to GitHub

```bash
"$GH" auth status
```

A logged-in account reported → row 5. Otherwise start the browser flow, **in the background** — it prints a code and stays alive until they finish, so anything waiting on it times out:

```bash
"$GH" auth login --hostname github.com --git-protocol https --web
```

Read its **stderr**, not stdout — the code appears there:

```
! First copy your one-time code: 337A-3AA9
Open this URL to continue in your web browser: https://github.com/login/device
```

Relay it immediately:

> To sign you in to GitHub, open **github.com/login/device** and paste this code:
>
> **337A-3AA9**
>
> Approve with your usual GitHub account, then tell me.

**Tell them to open the URL themselves — do not promise the browser will open.** They never read the code off the terminal: you read it and relay it.

Then poll until it lands:

```bash
"$GH" auth status --hostname github.com
```

Check its exit code directly, unpiped. Do not ask them to run anything or paste output, and do not go looking in keychains, credential stores or config files. Once it reports the account, wire Git to the same credentials:

```bash
"$GH" auth setup-git --hostname github.com
```

It fails if run before the sign-in completed — only after `auth status` succeeds; skipped, plain `git` has no credential.

**One sign-in ask.** The flow does not complete → row 5's stop, with the ask in the message.

## 5. Prove the access — the only hard stop

```bash
GIT_TERMINAL_PROMPT=0 "$GIT" ls-remote --exit-code https://github.com/FuseFinance/internal-skills >/dev/null 2>&1
```

`FuseFinance/internal-skills` is the fixed probe: the private plugin repository, the one thing row 6 needs. GitHub says "not found" alike for a private repo and a wrong name, so no other name is probed.

- **Exit 0** → row 6.
- **Non-zero while `gh auth status` reports signed in** → the account cannot reach the repository: a permission someone at Fuse has to grant, and everything below depends on it. Read the username, fill the message, say the two lines, and stop — nothing installs after this point.

```bash
"$GH" api user -q .login
```

The message, ready to send, REQUIRED slots filled — `<username>` from the command above, `<their name>` from what they told you or `git config user.name`:

> Hi — <their name> is setting up the Fuse tools on their laptop and their GitHub account **<username>** cannot read `FuseFinance/internal-skills` (`git ls-remote` answers "Repository not found" while signed in). Could you add **<username>** to the FuseFinance GitHub organization with read access to `internal-skills`? Setup continues by itself once it lands.

Then, to them:

> Fuse hasn't given your GitHub account access to the plugin yet — that's a permission someone has to grant, not something on this computer. Send the message above to Danny or Ezequiel on Slack; when they say it's done, tell me and I check again.

No second sign-in flow, no `gh auth refresh`, no other repository or organization probed, no retry loop, no `gh repo view`, no workaround (a fork, a zip, another marketplace, a token). The re-check is yours: the probe line again, when they say the access landed, in this session or the next `fuse-dm setup`.

A token or password pasted in the chat, however it is offered ("Eze gave me a token", "export it or whatever", "I have a call in 10 minutes"), is refused in one sentence — it is a credential, a chat is nowhere for it — and never used, exported, stored or repeated: guardrail 5, and the table above. There is no other place for it either: the sign-in is row 4's browser flow.

## 6. The plugins

Detect first:

```bash
claude plugin marketplace list; claude plugin list
```

Skip an `add` whose marketplace is already listed and an `install` whose plugin is. Then, in this order:

```bash
claude plugin marketplace add https://github.com/FuseFinance/internal-skills.git
claude plugin install deployment-manager@fuse-internal
claude plugin marketplace add https://github.com/anthropics/claude-plugins-official.git
claude plugin install slack@claude-plugins-official
claude plugin install linear@claude-plugins-official
```

**The full `.git` URL, never the `FuseFinance/internal-skills` shorthand** (SSH, outside row 4's credential). Both official plugins, whatever the client's tracker. A failed install gets the budget; still failing → its line in the record's `notes`, and part two says what is missing.

Read the version `claude plugin list` shows for `deployment-manager@fuse-internal`: it goes in the record below (`0.0.0` when the install failed).

## 7. The record, the state, the exit

Nothing here is needed after this session: this plugin is loaded for one session only, and part two (`/deployment-manager:setup`, the plugin just installed) takes over. Leave it two files, written with Bash — no script of ours exists on this laptop yet. The tally goes into the six variables on top, from your notes; the heredoc below them is copied as it stands. `$PY` is row 3's Python by absolute path.

```bash
mkdir -p ~/.fuse/dm-setup
RUN_KEY="$("$PY" -c 'import uuid; print(uuid.uuid4())')"; PLATFORM="$("$PY" -c 'import sys; print(sys.platform)')"
PLUGIN_VERSION="0.0.0"                 # the deployment-manager version row 6 read; 0.0.0 when its install failed
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"   # row 1's time, ISO-8601 UTC
OUTCOME="completed"                    # partial when a row degraded after its budget
ROWS_FIXED=0; ROWS_BLOCKED=0; UNBLOCKS=0   # installs and sign-ins done · rows still failing · rows that landed on a second remedy
FRICTION='[]'                          # one {"ts","step","kind":"tool-error","note"} per install or sign-in that failed, note under 140 characters
NOTES="the surface, what was installed, what is still missing"   # one factual line
cat > ~/.fuse/dm-setup/bootstrap-run.json <<EOF
{
  "run_key": "$RUN_KEY",
  "run_mode": "interactive",
  "skill": "setup",
  "mode": "bootstrap",
  "client_slug": "_none",
  "plugin_version": "$PLUGIN_VERSION",
  "started_at": "$STARTED_AT",
  "ended_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "outcome": "$OUTCOME",
  "items": {"rows_checked": 7, "rows_fixed": $ROWS_FIXED, "rows_blocked": $ROWS_BLOCKED, "unblocks": $UNBLOCKS},
  "friction": $FRICTION,
  "tools_failed": [],
  "notes": "$NOTES",
  "env": {"claude_version": "$(claude --version | cut -d' ' -f1)", "platform": "$PLATFORM", "runner": "claude-code"}
}
EOF
printf 'bootstrap-done\n' > ~/.fuse/dm-setup/state
```

No PII in any field: no names, no addresses, secrets by logical key name. Part two imports this record once and reports it as its own run.

Then say, as written:

> Part one is done. Type /exit and press Enter. I come straight back.

The launcher reads `bootstrap-done` and opens part two by itself: no relaunch line for them, no "restart Claude", no command.
