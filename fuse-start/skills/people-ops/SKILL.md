---
name: people-ops
description: Use when the People Ops one-line setup (people-ops.sh) has just opened this session on a People Ops teammate's Mac and the people-ops plugin is not installed yet, when git, the GitHub CLI or Node are missing on a Mac without an administrator password, when a GitHub sign-in or the access check to FuseFinance/internal-skills is pending, or when the people-ops plugin has to be installed from the fuse-internal marketplace for the first time.
disable-model-invocation: true
---

## 1. Say which version you are, then what happens

Open your first response with exactly this line, nothing above it:

`Version: fuse-start 1.1.0 · people-ops`

Then three plain sentences, and nothing else before row 2:

> This is part one of the People Ops Studio setup, running on your own Mac. I install the few tools Claude needs (git, the GitHub CLI and Node), sign you in to GitHub, check that Fuse gave your account access, and install the People Ops plugin. The only thing you do is approve one GitHub sign-in in your browser; everything else runs here.

## How this runs

**The person running this is on the People Ops team, not an engineer.** Every command here is something **you** run. Guided never means handing them an error to interpret or a command to run — not now, not "later, once access lands", not "to double check". Say one line before anything that will visibly happen on their screen.

**Check before you install.** Every row opens with a detection command; running this again after an interruption has to cost seconds.

**Nothing carries over between commands.** Each command runs in a fresh shell: when a block uses `$GIT` or `$GH` from an earlier block, start that command by setting it again to the path row 3 printed (`GH=<path>; …`).

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

A token or password pasted in the chat, however it is offered, is refused in one sentence — it is a credential, a chat is nowhere for it — and never used, exported, stored or repeated: it stays "the fragment" in every place you write. The sign-in is row 4's browser flow and nothing else.

## 2. Is this session on their Mac

```bash
uname -s 2>/dev/null; ls ~
```

`Darwin` → row 3. An empty or synthetic home is a cloud session: say "this part runs on your own Mac — paste the People Ops line into Terminal there" once, and stop. Anything other than `Darwin` stops with one line: People Ops Studio runs on a Mac; ask the person onboarding you to the studio.

## 3. git, gh, Node 22 or later

Detect first, every candidate with `--version`:

```bash
git --version; gh --version; node --version
```

Pin what answered by absolute path and use those paths from here on; after every install below, re-resolve **by absolute path** — a fresh install is not on this shell's PATH:

```bash
GIT=$(command -v git); GH=$(command -v gh); echo "GIT=$GIT GH=$GH"
```

**Nothing that needs an administrator password.** No `sudo`, no `.pkg` installer, never "install Homebrew first" (its installer needs the admin password IT does not give them), never a download page as the first move.

### Mac with Homebrew

```bash
brew install git gh node
```

Then `/opt/homebrew/bin/{git,gh,node}` on Apple Silicon, `/usr/local/bin/{git,gh,node}` on Intel. Apple's Git at `/usr/bin/git` is a real Git: keep it when it answered.

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

- **Exit 0** → row 6.
- **Non-zero while `gh auth status` reports signed in** → the account cannot reach the plugin's repository: a permission someone at Fuse grants. Read the username, fill the message, say the two lines, and stop — nothing installs after this point.

```bash
"$GH" api user -q .login
```

> Hi — <their name> is setting up the People Ops Studio on their Mac and their GitHub account **<username>** cannot read `FuseFinance/internal-skills`. Could you add **<username>** to the FuseFinance GitHub organization's **people-ops** team, with read access to `internal-skills`? Setup continues by itself once it lands.

> Fuse hasn't given your GitHub account access to the plugin yet — that's a permission someone grants, not something on this Mac. Send the message above on Slack to whoever manages Fuse's GitHub organization (the person onboarding you to the studio knows who); when they say it's done, tell me and I check again.

No second sign-in flow, no other repository probed, no retry loop, no workaround (a fork, a zip, another marketplace, a token). The re-check is yours, when they say the access landed.

## 6. The plugin

This session may be the Claude app's, where `claude` is not on PATH: use the running Claude Code's own binary.

```bash
CLAUDE="${CLAUDE_CODE_EXECPATH:-$(command -v claude)}"; "$CLAUDE" plugin marketplace list; "$CLAUDE" plugin list
```

Skip an `add` whose marketplace is already listed and an `install` whose plugin is. Then:

```bash
"$CLAUDE" plugin marketplace add https://github.com/FuseFinance/internal-skills.git
"$CLAUDE" plugin install people-ops@fuse-internal
```

**The full `.git` URL, never the `FuseFinance/internal-skills` shorthand** (SSH, outside row 4's credential). A failed install gets the budget; still failing → say what is missing in one line, and that the person onboarding you to the studio is the one to ask.

## 7. The exit

Nothing is written for later: this plugin is loaded for one session only, and the People Ops plugin takes over. Say, as written:

> Part one is done. Type /exit and press Enter. Then open the Claude app (if it isn't on your Mac yet, download it from claude.ai/download and sign in), choose Code, start a new session in the folder Fuse → studio in your home folder, and type /people-ops:studio-setup.
