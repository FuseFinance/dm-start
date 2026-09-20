# fuse-dm.ps1 - the Windows entry point of the DM launcher (OPX-1339, S-2b of the setup launcher series): bin/fuse-dm's
# behaviour for Windows PowerShell 5.1 (the Windows 11 default) and PowerShell 7, verb for verb, flag for flag,
# message for message. `fuse-dm.ps1 setup` runs the setup loop: Git for Windows first (below), then claude by absolute
# path (the official installer runs when there is none), the three things Claude asks once, the stage from
# `claude plugin list` (the bootstrap plugin's part one, or setup's part two), the session with the setup flags, and
# after each session ~/.fuse/dm-setup/state decides whether another pass follows - at most three launches. A bare
# `fuse-dm.ps1` from a file is the daily session: Fable, auto mode, ~/Fuse, no bypass flag, ever. `fuse-dm.ps1 update`
# runs the plugin update ritual, then the daily session. Both keep the machine current (OPX-1366, S-6): the launcher
# records the deployment-manager version it last ran a pass for in ~/.fuse/dm-setup/plugin-version (its own file -
# setup rewrites `state` whole) and reads the installed one from installed_plugins.json (ConvertFrom-Json, no claude
# process); moved -> the daily session opens on setup's keep-current pass, the prompt "/deployment-manager:setup
# keep-current" as the last token of the same daily argv, one line said first, and the record is rewritten after a
# clean run (code 0); equal -> nothing; no record -> today's daily and the version recorded after a clean run; unknown
# (no file, a malformed file) -> nothing. `install-shim` is NOT here: the shim at ~/.fuse/bin/fuse-dm and
# the Desktop icon are written by the plugin's bash copy under Git Bash (setup's row 14), and the icon execs that copy.
# The same file is published as dm.ps1 in FuseFinance/dm-start: the one line `irm .../dm.ps1 | iex` runs this text in
# the DM's own PowerShell with no argument and an EMPTY $PSCommandPath (there is no file) - that case is setup, the
# bash BASH_SOURCE rule. Under iex there is no child process either: the body is a function that returns the code, and
# the tail exits only when the text was run as a file; under iex it returns, so the DM's console stays open - and its
# functions and top-level variables stay defined in that console afterwards (by design for $fuseDmExitCode, harmless
# for the rest; nothing of theirs is read back). For the same reason the Claude Code installer runs in a CHILD shell,
# the running host by absolute path: the official install.ps1 ends every failure path with an `exit`, which in this
# process would close that console (or end a file run with code 1 instead of 69) - bash's `bash -c` is a child too.
# Windows-specific (AC3): a machine before Git for Windows has no Git Bash, and Claude Code picks its Bash tool from
# `git` on PATH or CLAUDE_CODE_GIT_BASH_PATH. So, before any claude launch: `git` on PATH, else the default
# %ProgramFiles%\Git\bin\bash.exe; neither -> windows.md row 2's own line, `winget install -e --id Git.Git`, then
# CLAUDE_CODE_GIT_BASH_PATH set IN THIS PROCESS (a winget install never reaches the running shell's PATH, and this
# PowerShell is the one claude inherits from) - no new window, no PATH refresh, no restart.
# The session runs through Start-Process -NoNewWindow -Wait with stdin and stdout on the console (claude is a TUI) and
# only stderr captured to a file, for the model-refusal check; never a pipeline on the native call.
# Windows PowerShell 5.1 and 7 run the same file: pure 7-bit ASCII (5.1 reads a BOM-less file as cp1252), nothing
# 7-only, seams as FUSE_DM_* environment only (-Help), no module, no settings file, no default mode.
# Exit: 0 . 64 usage . 69 a prerequisite is missing . 70 a write failed . else claude's own code.
param([string]$Verb, [switch]$Help)

function Get-FuseDmSetting([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) { return $value }
    return $Default
}

$Usage = 'usage: fuse-dm.ps1 [setup | update | -Help]'
$Model = Get-FuseDmSetting 'FUSE_DM_MODEL' 'fable'
$Effort = Get-FuseDmSetting 'FUSE_DM_EFFORT' 'medium'
$BootstrapUrl = Get-FuseDmSetting 'FUSE_DM_BOOTSTRAP_URL' 'https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip'
$RetryModel = 'opus'
$InstallerLine = 'irm https://claude.ai/install.ps1 | iex'
$HomeDir = $env:USERPROFILE                     # the Windows home; HOME on a Mac running the suite under pwsh
if (-not $HomeDir) { $HomeDir = $env:HOME }
if (-not $HomeDir) { $HomeDir = [string]$HOME }
$RootDir = [IO.Path]::Combine($script:HomeDir, 'Fuse')
$StateFile = [IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'state')   # read only: the bootstrap plugin and setup write it
$VersionFile = [IO.Path]::Combine($script:HomeDir, '.fuse', 'dm-setup', 'plugin-version')   # the launcher's own: the plugin version it last ran a pass for
$InstalledPlugins = [IO.Path]::Combine($script:HomeDir, '.claude', 'plugins', 'installed_plugins.json')
$KeepCurrentPrompt = '/deployment-manager:setup keep-current'
$Claude = $null
$Retried = $false
$Installed = ''
$Recorded = ''
$KeepCurrent = $false

function Say([string]$Message) { Write-Host ('fuse-dm: ' + $Message) }
function Die([string]$Message, [int]$Code) { [Console]::Error.WriteLine('fuse-dm: ' + $Message); return $Code }

function Show-Help {
    foreach ($line in @(
        $script:Usage,
        '',
        '  setup         install or finish the Fuse environment: part one (the bootstrap plugin) when deployment-manager is',
        '                not installed, part two (/deployment-manager:setup) when it is; another pass follows when',
        '                ~/.fuse/dm-setup/state says so (bootstrap-done, needs-second-pass); at most three launches',
        '  (none)        the daily session: claude on Fable, auto mode, in ~/Fuse - prompts on, no bypass; when the installed',
        '                deployment-manager version differs from ~/.fuse/dm-setup/plugin-version (the one the last pass ran',
        '                for), the session opens on /deployment-manager:setup keep-current - every row re-checked, seconds -',
        '                and the record is rewritten after a clean run; no record yet -> the daily session, the version recorded',
        '  update        claude plugin marketplace update fuse-internal, claude plugin update deployment-manager@fuse-internal,',
        '                then the daily session, with the same keep-current check',
        '  install-shim  not here: the shim at ~/.fuse/bin/fuse-dm and the Desktop icon are written by the plugin''s bash',
        '                copy, under Git Bash (bash "$ROOT/bin/fuse-dm" install-shim)',
        '',
        'environment   FUSE_DM_MODEL (fable)',
        '              FUSE_DM_EFFORT (medium, the setup session only)',
        '              FUSE_DM_BOOTSTRAP_URL (the fuse-start.zip release the --plugin-url of part one points at)',
        '              FUSE_DM_INSTALLER (the PowerShell expression run when claude is missing; default: the official installer line)',
        '              FUSE_DM_GIT_BASH (where Git for Windows'' bash.exe is looked for; default: %ProgramFiles%\Git\bin\bash.exe)'
    )) { Write-Host $line }
}

# ---- Git for Windows, before any claude launch (AC3): already pointed at -> untouched; git on PATH or bash.exe at
# its default place -> the variable set when the file is there; neither -> row 2's winget line, then the variable.
# winget itself missing -> the row 2 text's own fallback, one line, code 69; never a hang. ----
function Confirm-GitForWindows {
    if ($env:CLAUDE_CODE_GIT_BASH_PATH) { return 0 }
    $bash = $env:FUSE_DM_GIT_BASH
    if (-not $bash) { $bash = [IO.Path]::Combine([string]$env:ProgramFiles, 'Git', 'bin', 'bash.exe') }
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue
    if ((-not $git) -and (-not (Test-Path -LiteralPath $bash -PathType Leaf))) {
        $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue
        if (-not $winget) {
            return (Die 'Git for Windows is missing and winget is not on this computer - install Git from https://git-scm.com/downloads/win, then paste the line again' 69)
        }
        Say 'installing Git for Windows - one minute'
        & winget install -e --id Git.Git | Out-Host
        if ($LASTEXITCODE -ne 0) { Say 'the Git for Windows install did not finish cleanly' }
        if (-not (Test-Path -LiteralPath $bash -PathType Leaf)) {
            return (Die ('Git for Windows is still missing after winget ran (winget install -e --id Git.Git) - install it from https://git-scm.com/downloads/win, then paste the line again') 69)
        }
    }
    if (Test-Path -LiteralPath $bash -PathType Leaf) { $env:CLAUDE_CODE_GIT_BASH_PATH = $bash }
    return 0
}

# ---- claude, by absolute path: ~/.local/bin first (with a PATHEXT extension on Windows: the official installer writes
# claude.exe there), then PATH; missing -> the official installer runs here (one line said first), then the resolution
# again; 69 only when claude is still missing. Never a line for the DM to paste. FUSE_DM_INSTALLER replaces the
# installer expression (the tests' stub; the real one is never run by a test). ----
function Get-ExecutableExtension {
    $exts = @('')
    if ($env:PATHEXT) { $exts = @($env:PATHEXT.Split(';') | Where-Object { $_ } | ForEach-Object { $_.ToLower() }) }
    return ,$exts
}
function Resolve-Claude {
    $local = [IO.Path]::Combine($script:HomeDir, '.local', 'bin', 'claude')
    foreach ($ext in (Get-ExecutableExtension)) {
        if (Test-Path -LiteralPath ($local + $ext) -PathType Leaf) { return ($local + $ext) }
    }
    $found = @(Get-Command claude -CommandType Application -ErrorAction SilentlyContinue)
    if ($found.Count -gt 0) { return $found[0].Path }
    return $null
}
function Confirm-Claude {
    $script:Claude = Resolve-Claude
    if ($script:Claude) { return 0 }
    Say 'installing Claude Code - one minute'
    $installer = $env:FUSE_DM_INSTALLER
    if (-not $installer) { $installer = $script:InstallerLine }
    # in a CHILD shell - the running host by absolute path, nothing from PATH - never in this process: the official
    # install.ps1 ends every failure path with an `exit`, which here would close the DM's console under iex (or end a
    # file run with code 1 instead of 69); in a child it is only $LASTEXITCODE, the shape of bash's `bash -c`
    $shell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    try {
        & $shell -NoProfile -ExecutionPolicy Bypass -Command $installer | Out-Host
        if ($LASTEXITCODE -ne 0) { Say 'the installer did not finish cleanly' }
    } catch { Say 'the installer did not finish cleanly' }
    $script:Claude = Resolve-Claude
    if ($script:Claude) { return 0 }
    return (Die ('claude is still missing after the installer ran (' + $script:InstallerLine + ') - ask your FDE') 69)
}

# ---- the plugin: installed or not, from `claude plugin list` (the stage of part one / part two) ----
function Test-PluginInstalled {
    $listing = @(& $script:Claude plugin list 2>$null)
    return (($listing -join "`n") -like '*deployment-manager@fuse-internal*')
}

# ---- the state file (read after every setup exit; never written here) ----
function Read-State {
    $state = ''
    if (Test-Path -LiteralPath $script:StateFile -PathType Leaf) {
        $first = @(Get-Content -LiteralPath $script:StateFile -TotalCount 1)
        if ($first.Count -gt 0 -and $null -ne $first[0]) { $state = ([string]$first[0]).Trim() }
    }
    return $state
}

# ---- keep current (OPX-1366): the installed deployment-manager version from installed_plugins.json (Test-Path, then
# Get-Content -Raw | ConvertFrom-Json in a try; the entry is an array, its first object's `version`; a missing or
# malformed file = unknown = ''), the record from ~/.fuse/dm-setup/plugin-version (the launcher's own file: setup
# rewrites `state` whole, so this is not a second line of it). Different -> $KeepCurrent and one line said; the record
# is written only after a clean run. ----
function Get-InstalledVersion {
    if (-not (Test-Path -LiteralPath $script:InstalledPlugins -PathType Leaf)) { return '' }
    try {
        $json = Get-Content -LiteralPath $script:InstalledPlugins -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $entries = @($json.plugins.'deployment-manager@fuse-internal')
        if ($entries.Count -gt 0 -and $null -ne $entries[0] -and $entries[0].version) { return [string]$entries[0].version }
    } catch { }
    return ''
}
function Read-Record {
    if (Test-Path -LiteralPath $script:VersionFile -PathType Leaf) {
        $first = @(Get-Content -LiteralPath $script:VersionFile -TotalCount 1)
        if ($first.Count -gt 0 -and $null -ne $first[0]) { return ([string]$first[0]).Trim() }
    }
    return ''
}
function Test-KeepCurrent {
    $script:KeepCurrent = $false
    $script:Recorded = ''
    $script:Installed = Get-InstalledVersion
    if (-not $script:Installed) { return }
    $script:Recorded = Read-Record
    if ($script:Recorded -and $script:Recorded -ne $script:Installed) {
        $script:KeepCurrent = $true
        Say ('the plugin moved to ' + $script:Installed + ' - re-checking the environment first, seconds')
    }
}
function Write-Record {
    # after a clean run only; nothing to write when the version is unknown or already recorded
    if ((-not $script:Installed) -or ($script:Recorded -eq $script:Installed)) { return }
    try {
        $dir = Split-Path -Parent $script:VersionFile
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null }
        [IO.File]::WriteAllText($script:VersionFile, $script:Installed + "`n")
    } catch { Say ('could not record the plugin version at ' + $script:VersionFile + ' - the next launch re-checks the environment again') }
}

# ---- one session: claude in ~/Fuse, the argv rebuilt from the current model; stdin and stdout on the console (the
# TUI), stderr captured to a file so a model refusal (non-zero, and stderr names the model) can be told from any other
# exit, and printed back after a non-zero one so the DM sees it. A refusal retries once with opus and keeps it for the
# passes that follow; anything else propagates as claude's own code. The setup session alone carries FUSE_DM_LAUNCHER=1
# (setup's silent case) and the effort pin, set right before the launch and put back right after: under iex they would
# otherwise leak into the DM's session, and the daily session runs on the seat's effort. ----
function ConvertTo-CommandLineToken([string[]]$Tokens) {
    # Start-Process joins -ArgumentList with spaces and quotes nothing (Windows PowerShell 5.1): a token with a space
    # or a quote is wrapped here so it arrives as one argument; the flags, the prompts and the URL go as they are
    $out = @()
    foreach ($t in $Tokens) {
        if ($t -eq '' -or $t -match '[\s"]') { $out += ('"' + ($t -replace '"', '\"') + '"') } else { $out += $t }
    }
    return ,$out
}
function Start-Session([string]$Kind, [string]$Stage) {
    if ($Kind -eq 'setup') {
        $argv = @('--model', $script:Model, '--effort', $script:Effort, '--dangerously-skip-permissions')
        if ($Stage -eq 'one') { $argv += @('--plugin-url', $script:BootstrapUrl, '/fuse-start:begin') }
        else { $argv += @('/deployment-manager:setup') }
    } else {
        $argv = @('--model', $script:Model, '--permission-mode', 'auto')
        if ($script:KeepCurrent) { $argv += @($script:KeepCurrentPrompt) }   # setup's keep-current pass, the prompt last
    }
    if (-not (Test-Path -LiteralPath $script:RootDir -PathType Container)) {
        try { New-Item -ItemType Directory -Path $script:RootDir -Force -ErrorAction Stop | Out-Null }
        catch { return (Die ('cannot enter ' + $script:RootDir) 70) }
    }
    $errf = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'fuse-dm.' + $PID + '.err')
    $savedLauncher = $env:FUSE_DM_LAUNCHER
    $savedEffort = $env:CLAUDE_CODE_EFFORT_LEVEL
    $rc = 70
    try {
        if ($Kind -eq 'setup') { $env:FUSE_DM_LAUNCHER = '1'; $env:CLAUDE_CODE_EFFORT_LEVEL = $script:Effort }
        $proc = Start-Process -FilePath $script:Claude -ArgumentList (ConvertTo-CommandLineToken $argv) -WorkingDirectory $script:RootDir `
            -NoNewWindow -Wait -PassThru -RedirectStandardError $errf -ErrorAction Stop
        $proc.WaitForExit()
        $rc = [int]$proc.ExitCode
    } catch {
        [Console]::Error.WriteLine('fuse-dm: cannot start ' + $script:Claude + ': ' + $_.Exception.Message)
    } finally {
        if ($Kind -eq 'setup') {
            if ($null -eq $savedLauncher) { Remove-Item Env:\FUSE_DM_LAUNCHER -ErrorAction SilentlyContinue } else { $env:FUSE_DM_LAUNCHER = $savedLauncher }
            if ($null -eq $savedEffort) { Remove-Item Env:\CLAUDE_CODE_EFFORT_LEVEL -ErrorAction SilentlyContinue } else { $env:CLAUDE_CODE_EFFORT_LEVEL = $savedEffort }
        }
    }
    $err = ''
    if (Test-Path -LiteralPath $errf -PathType Leaf) {
        $err = [IO.File]::ReadAllText($errf)
        Remove-Item -LiteralPath $errf -Force -ErrorAction SilentlyContinue
    }
    if ($rc -ne 0 -and $err) { [Console]::Error.Write($err) }
    if ($rc -ne 0 -and (-not $script:Retried) -and ($script:Model -ne $script:RetryModel) -and $err.Contains($script:Model)) {
        $script:Retried = $true
        Say ('this seat refused the model ' + $script:Model + ' - starting again on ' + $script:RetryModel)
        $script:Model = $script:RetryModel
        return (Start-Session $Kind $Stage)
    }
    return $rc
}

# ---- setup: the loop ----
function Invoke-Setup {
    $code = Confirm-Claude
    if ($code -ne 0) { return $code }
    Say 'three things Claude asks the first time, then never again: a colour theme (press Enter), a sign-in (pick your @fusefinance.com Google account), a permissions warning (answer Yes)'
    if (Test-PluginInstalled) { $stage = 'two' } else { $stage = 'one' }
    $n = 0
    $state = ''
    while ($n -lt 3) {
        $rc = Start-Session 'setup' $stage
        $n = $n + 1
        if ($rc -ne 0) { return $rc }
        $state = Read-State
        if ($state -eq 'bootstrap-done' -or $state -eq 'needs-second-pass') { $stage = 'two' } else { break }
    }
    if ($state -eq 'done') {
        $script:Installed = Get-InstalledVersion; $script:Recorded = ''; Write-Record   # read after the passes: part one installs the plugin
        Say 'setup finished - from now on double-click Fuse Claude on your Desktop, or type fuse-dm'
    } else {
        $shown = $state
        if (-not $shown) { $shown = 'none' }
        Say ('setup closed (state: ' + $shown + ') - run fuse-dm setup again to finish; day to day, double-click Fuse Claude on your Desktop, or type fuse-dm')
    }
    return 0
}

# ---- daily, and update-then-daily: the keep-current check before the launch, the record after a clean run ----
function Invoke-Daily {
    $code = Confirm-Claude
    if ($code -ne 0) { return $code }
    Test-KeepCurrent
    $rc = Start-Session 'daily' ''
    if ($rc -eq 0) { Write-Record }
    return $rc
}
function Invoke-Update {
    $code = Confirm-Claude
    if ($code -ne 0) { return $code }
    & $script:Claude plugin marketplace update fuse-internal | Out-Host
    $ok = ($LASTEXITCODE -eq 0)
    if ($ok) {
        & $script:Claude plugin update deployment-manager@fuse-internal | Out-Host
        $ok = ($LASTEXITCODE -eq 0)
    }
    if (-not $ok) { Say 'the plugin update did not go through - the session opens on the copy you have; run fuse-dm update again later' }
    Test-KeepCurrent
    $rc = Start-Session 'daily' ''
    if ($rc -eq 0) { Write-Record }
    return $rc
}

# ---- arguments ----
# No argument and no $PSCommandPath: the one-line stub (`irm ... | iex`) - setup. A bare run from a file is the daily
# session. `install-shim` belongs to the bash copy; anything else unknown is usage, code 64.
function Invoke-FuseDm([string]$Verb, [bool]$WantHelp, [bool]$FromFile) {
    if ($WantHelp -or $Verb -eq '--help' -or $Verb -eq '-h' -or $Verb -eq '-help') { Show-Help; return 0 }
    if ($Verb -eq '') {
        $code = Confirm-GitForWindows
        if ($code -ne 0) { return $code }
        if ($FromFile) { return (Invoke-Daily) }
        return (Invoke-Setup)
    }
    if ($Verb -eq 'setup' -or $Verb -eq 'update') {
        $code = Confirm-GitForWindows
        if ($code -ne 0) { return $code }
        if ($Verb -eq 'setup') { return (Invoke-Setup) }
        return (Invoke-Update)
    }
    [Console]::Error.WriteLine($script:Usage)
    if ($Verb -eq 'install-shim') {
        return (Die 'install-shim belongs to the bash copy of the launcher: run it from Git Bash, `bash "$ROOT/bin/fuse-dm" install-shim` (setup''s row 14 does)' 64)
    }
    return (Die ('unknown verb ' + $Verb) 64)
}

$fuseDmExitCode = Invoke-FuseDm -Verb $Verb -WantHelp ([bool]$Help) -FromFile ([bool]$PSCommandPath)
if ($PSCommandPath) { exit $fuseDmExitCode }
