# fuse-dm.Tests.ps1 (OPX-1339): the Pester suite for bin/fuse-dm.ps1, the Windows entry point of the launcher. It mirrors
# setup/tests/test_launcher.py class for class (Launcher, RelaunchLoop, NoBypass, ModelRetry) with the same case names in
# words, plus GitForWindows (the ps1-only branch) and Text (the file's own pins). Every case runs the real script in a
# CHILD PowerShell process - the script exits - with a stub bin that is FIRST and ONLY on PATH, a temp HOME/USERPROFILE and
# nothing of the machine's. The fake `claude` speaks the Python fake's protocol: it records every invocation as
# $FAKE_DIR/launch.<n> (argv0, one arg= line per argument, the cwd, and the env it saw: launcher=, effort_env=, git_bash=),
# answers `plugin list` from $FAKE_PLUGIN_LIST, exits `plugin marketplace` / `plugin update` with $FAKE_UPDATE_RC, writes
# the state file when FAKE_CLAUDE_WRITE_STATE tells it to (the nth comma-separated value on the nth session launch) and
# exits with FAKE_CLAUDE_EXIT's nth value, FAKE_CLAUDE_STDERR on stderr. The fake `winget` records its argv and creates the
# file FUSE_DM_GIT_BASH names. The real `claude`, `winget` and installer are never executed.
#
# The primary shell is Windows PowerShell 5.1 (the DM's shell, powershell.exe); FUSE_DM_TEST_SHELL=pwsh runs the same suite
# under pwsh. Off Windows (a Mac with pwsh) every case runs except the ones that need powershell.exe, a .cmd or
# %ProgramFiles%; on Windows nothing skips (AC4: a suite that only skips is not a pass). FUSE_DM_SCRIPT points the suite at
# another copy of the script - the public repo's CI runs it against dm.ps1. Pure 7-bit ASCII, like the script.
#
# Run: Invoke-Pester -Path deployment-manager/setup/tests/fuse-dm.Tests.ps1 -Output Detailed

Describe 'fuse-dm.ps1' {

    BeforeAll {
        $script:OnWindows = [Environment]::OSVersion.Platform -eq 'Win32NT'
        $tests = $PSScriptRoot
        if ($env:FUSE_DM_SCRIPT) {
            $script:Script = (Resolve-Path -LiteralPath $env:FUSE_DM_SCRIPT).Path
        } else {
            $inPlugin = [IO.Path]::Combine($tests, '..', '..', 'bin', 'fuse-dm.ps1')
            $inPublic = [IO.Path]::Combine($tests, '..', 'dm.ps1')
            if (Test-Path -LiteralPath $inPlugin) { $script:Script = (Resolve-Path -LiteralPath $inPlugin).Path }
            elseif (Test-Path -LiteralPath $inPublic) { $script:Script = (Resolve-Path -LiteralPath $inPublic).Path }
            else { $script:Script = [IO.Path]::GetFullPath($inPlugin) }   # missing: every case fails on it, the RED
        }
        if ($env:FUSE_DM_TEST_SHELL) { $script:Shell = (Get-Command $env:FUSE_DM_TEST_SHELL -CommandType Application | Select-Object -First 1).Path }
        elseif ($script:OnWindows) { $script:Shell = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe') }
        else { $script:Shell = Join-Path $PSHOME 'pwsh' }
        $script:Pwsh = Join-Path $PSHOME 'pwsh'   # the fakes' interpreter off Windows

        # The suite's temp root, physical: a child started with -WorkingDirectory sees the OS's own path (macOS keeps
        # /var -> /private/var), so the expected cwd is resolved the same way. GitHub's RUNNER_TEMP first: the Windows
        # runner's TEMP carries an 8.3 name (RUNNER~1) that the child's cwd would spell out.
        $base = $env:RUNNER_TEMP
        if (-not $base) { $base = [IO.Path]::GetTempPath() }
        $root = Join-Path $base ('fuse-dm-pester-' + [IO.Path]::GetRandomFileName().Replace('.', ''))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        $saved = [IO.Directory]::GetCurrentDirectory()
        try { [IO.Directory]::SetCurrentDirectory($root); $script:TmpRoot = [IO.Directory]::GetCurrentDirectory() }
        finally { [IO.Directory]::SetCurrentDirectory($saved) }

        # ---- the shared stub: the fakes and their wrappers, written once (macOS assesses a fresh executable on its first
        # run, so each test hard-links these instead of writing its own) ----
        $script:Stub = Join-Path $script:TmpRoot 'stub'
        New-Item -ItemType Directory -Path $script:Stub -Force | Out-Null
        $script:WrapperName = if ($script:OnWindows) { 'claude.cmd' } else { 'claude' }

        function Write-Ascii([string]$Path, [string]$Text) {
            [IO.File]::WriteAllText($Path, $Text, [Text.Encoding]::ASCII)
        }
        function Set-Executable([string]$Path) {
            if (-not $script:OnWindows) { & /bin/chmod 755 $Path }
        }
        function Write-Wrapper([string]$Name, [string]$FakeScript) {
            # Windows: a .cmd that runs the fake under Windows PowerShell by absolute path, its own full path first (argv0).
            # Elsewhere: a sh script that execs pwsh on the fake, $0 first. Both hand every argument through unchanged.
            if ($script:OnWindows) {
                $path = Join-Path $script:Stub ($Name + '.cmd')
                $ps = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
                Write-Ascii $path ('@"' + $ps + '" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $FakeScript + '" "%~f0" %*' + "`r`n")
            } else {
                $path = Join-Path $script:Stub $Name
                Write-Ascii $path ('#!/bin/sh' + "`n" + 'exec "' + $script:Pwsh + '" -NoProfile -NonInteractive -File "' + $FakeScript + '" "$0" "$@"' + "`n")
                Set-Executable $path
            }
            return $path
        }

        $claudeFake = Join-Path $script:Stub 'claude-fake.ps1'
        Write-Ascii $claudeFake @'
# claude-fake.ps1 (OPX-1339): the fake `claude` behind fuse-dm.Tests.ps1 - the Python FAKE_CLAUDE's protocol. The first
# argument is the wrapper's own path (argv0); the rest is claude's argv.
$fakeDir = $env:FAKE_DIR
$argv0 = ''
$rest = @()
if ($args.Count -gt 0) { $argv0 = [string]$args[0]; if ($args.Count -gt 1) { $rest = @($args[1..($args.Count - 1)]) } }
function Read-Number([string]$Name) { $f = Join-Path $fakeDir $Name; if (Test-Path -LiteralPath $f) { return [int]([IO.File]::ReadAllText($f).Trim()) }; return 0 }
function Get-Nth([string]$Csv, [int]$I) { if (-not $Csv) { return '' }; $parts = $Csv.Split(','); if ($I -le $parts.Count) { return $parts[$I - 1] }; return '' }
function Get-EnvOrUnset([string]$Name) { $v = [Environment]::GetEnvironmentVariable($Name); if ($null -eq $v -or $v -eq '') { return 'unset' }; return $v }
$n = (Read-Number 'count') + 1
[IO.File]::WriteAllText((Join-Path $fakeDir 'count'), [string]$n)
$lines = @('argv0=' + $argv0)
foreach ($a in $rest) { $lines += ('arg=' + $a) }
$lines += ('cwd=' + [IO.Directory]::GetCurrentDirectory())
$lines += ('launcher=' + (Get-EnvOrUnset 'FUSE_DM_LAUNCHER'))
$lines += ('effort_env=' + (Get-EnvOrUnset 'CLAUDE_CODE_EFFORT_LEVEL'))
$lines += ('git_bash=' + (Get-EnvOrUnset 'CLAUDE_CODE_GIT_BASH_PATH'))
[IO.File]::WriteAllLines((Join-Path $fakeDir ('launch.' + $n)), [string[]]$lines)
$head = ''
if ($rest.Count -ge 2) { $head = [string]$rest[0] + ' ' + [string]$rest[1] }
if ($head -eq 'plugin list') { [Console]::Out.WriteLine([string]$env:FAKE_PLUGIN_LIST); exit 0 }
if ($head -eq 'plugin marketplace' -or $head -eq 'plugin update') { $rc = 0; if ($env:FAKE_UPDATE_RC) { $rc = [int]$env:FAKE_UPDATE_RC }; exit $rc }
$s = (Read-Number 'sessions') + 1
[IO.File]::WriteAllText((Join-Path $fakeDir 'sessions'), [string]$s)
$st = Get-Nth $env:FAKE_CLAUDE_WRITE_STATE $s
if ($st) {
    $homeDir = $env:USERPROFILE
    if (-not $homeDir) { $homeDir = $env:HOME }
    $dir = [IO.Path]::Combine($homeDir, '.fuse', 'dm-setup')
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $dir 'state'), $st + "`n")
}
$rc = 0
$rcText = Get-Nth $env:FAKE_CLAUDE_EXIT $s
if ($rcText) { $rc = [int]$rcText }
if ($rc -ne 0) { $msg = $env:FAKE_CLAUDE_STDERR; if (-not $msg) { $msg = 'claude: exit ' + $rc }; [Console]::Error.WriteLine($msg) }
exit $rc
'@
        $wingetFake = Join-Path $script:Stub 'winget-fake.ps1'
        Write-Ascii $wingetFake @'
# winget-fake.ps1 (OPX-1339): records its argv (after the wrapper's own path) to $FAKE_DIR/winget.txt and "installs" Git for
# Windows by creating the file FUSE_DM_GIT_BASH names. Never the real winget.
$lines = @()
if ($args.Count -gt 1) { foreach ($a in $args[1..($args.Count - 1)]) { $lines += [string]$a } }
[IO.File]::WriteAllLines((Join-Path $env:FAKE_DIR 'winget.txt'), [string[]]$lines)
$target = $env:FUSE_DM_GIT_BASH
if ($target) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    [IO.File]::WriteAllText($target, '')
}
exit 0
'@
        $script:InstallerNoop = Join-Path $script:Stub 'installer-noop.ps1'
        Write-Ascii $script:InstallerNoop @'
# the installer seam (FUSE_DM_INSTALLER): records that it ran and installs nothing
[IO.File]::AppendAllText((Join-Path $env:FAKE_DIR 'installer.txt'), "ran`n")
'@
        $script:InstallerExits = Join-Path $script:Stub 'installer-exit1.ps1'
        Write-Ascii $script:InstallerExits @'
# the installer seam (FUSE_DM_INSTALLER): records that it ran and ends the way the official install.ps1 ends every
# failure path - with `exit 1` (review round 1): in the launcher's own process that would close the DM's console under
# iex, or end a file run with code 1 instead of 69
[IO.File]::AppendAllText((Join-Path $env:FAKE_DIR 'installer.txt'), "ran`n")
exit 1
'@
        $script:InstallerInstalls = Join-Path $script:Stub 'installer-installs.ps1'
        Write-Ascii $script:InstallerInstalls @'
# the installer seam (FUSE_DM_INSTALLER): records that it ran and lands the fake claude where the real installer puts it
[IO.File]::AppendAllText((Join-Path $env:FAKE_DIR 'installer.txt'), "ran`n")
$homeDir = $env:USERPROFILE
if (-not $homeDir) { $homeDir = $env:HOME }
$name = 'claude'
if ([Environment]::OSVersion.Platform -eq 'Win32NT') { $name = 'claude.cmd' }
$dir = [IO.Path]::Combine($homeDir, '.local', 'bin')
New-Item -ItemType Directory -Path $dir -Force | Out-Null
New-Item -ItemType HardLink -Path (Join-Path $dir $name) -Target (Join-Path $env:FAKE_STUB $name) | Out-Null
'@
        $script:ClaudeWrapper = Write-Wrapper 'claude' $claudeFake
        $script:WingetWrapper = Write-Wrapper 'winget' $wingetFake
        if ($script:OnWindows) {
            $script:GitWrapper = Join-Path $script:Stub 'git.cmd'
            Write-Ascii $script:GitWrapper ("@exit /b 0`r`n")
        } else {
            $script:GitWrapper = Join-Path $script:Stub 'git'
            Write-Ascii $script:GitWrapper ("#!/bin/sh`nexit 0`n")
            Set-Executable $script:GitWrapper
        }

        # ---- the environment the children inherit: saved and restored around every case ----
        $script:EnvKeys = @('HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'TMPDIR', 'TEMP', 'TMP', 'PATH', 'FAKE_DIR', 'FAKE_STUB',
            'FAKE_PLUGIN_LIST', 'FAKE_CLAUDE_WRITE_STATE', 'FAKE_CLAUDE_EXIT', 'FAKE_CLAUDE_STDERR', 'FAKE_UPDATE_RC',
            'FUSE_DM_MODEL', 'FUSE_DM_EFFORT', 'FUSE_DM_BOOTSTRAP_URL', 'FUSE_DM_INSTALLER', 'FUSE_DM_GIT_BASH',
            'FUSE_DM_LAUNCHER', 'CLAUDE_CODE_EFFORT_LEVEL', 'CLAUDE_CODE_GIT_BASH_PATH')
        $script:PluginListAbsent = "Installed plugins:`n`n  > slack@claude-plugins-official`n    Version: 1.0.0`n    Scope: user`n    Status: enabled`n"
        $script:PluginListPresent = $script:PluginListAbsent + "`n  > deployment-manager@fuse-internal`n    Version: 0.0.0`n    Scope: user`n    Status: enabled`n"
        $script:BootstrapUrl = 'https://github.com/FuseFinance/dm-start/releases/latest/download/fuse-start.zip'
        $script:SetupFlags = @('--model', 'fable', '--effort', 'medium', '--dangerously-skip-permissions')
        $script:DailyArgv = @('--model', 'fable', '--permission-mode', 'auto')
        $script:PartOne = '/fuse-start:begin'
        $script:PartTwo = '/deployment-manager:setup'
        $script:Refusal = "Error: the model 'fable' is not available on this account"

        function Set-Env([string]$Name, $Value) {
            if ($null -eq $Value) { [Environment]::SetEnvironmentVariable($Name, $null) }
            else { [Environment]::SetEnvironmentVariable($Name, [string]$Value) }
        }
        function Get-Installer([string]$Path) { return "& '" + $Path + "'" }   # FUSE_DM_INSTALLER is a PowerShell expression
        function Get-InstallerAsText([string]$Path) {
            # the official line's own shape - text piped into iex, no script boundary: an `exit` inside it is the caller's
            return "Get-Content -Raw '" + $Path + "' | Invoke-Expression"
        }
        function Get-StubName([string]$Name) {
            # the wrapper's file name on this platform: `winget` is winget.cmd on Windows (Write-Wrapper's rule), bare
            # elsewhere; a name that already carries .cmd (the claude wrapper's $script:WrapperName) is left alone.
            # CI round 1: Add-Stub 'winget' / 'git' joined the bare name on windows-latest and the link's target was missing.
            if ($script:OnWindows -and -not $Name.ToLower().EndsWith('.cmd')) { return ($Name + '.cmd') }
            return $Name
        }
        function Add-Stub([string]$Name) {
            # a hard link into the case's bin (the assessed inode, not a fresh file)
            $file = Get-StubName $Name
            New-Item -ItemType HardLink -Path (Join-Path $script:T.Bin $file) -Target (Join-Path $script:Stub $file) | Out-Null
        }
        function Remove-Stub([string]$Name) { Remove-Item -LiteralPath (Join-Path $script:T.Bin (Get-StubName $Name)) -Force }
        function Reset-Fake {
            Remove-Item -LiteralPath $script:T.Fake -Recurse -Force -ErrorAction SilentlyContinue
            New-Item -ItemType Directory -Path $script:T.Fake -Force | Out-Null
        }
        function ConvertTo-CommandLineToken([string[]]$Tokens) {
            # Start-Process joins -ArgumentList with spaces and quotes nothing: a token with a space or a quote is wrapped
            $out = @()
            foreach ($t in $Tokens) {
                if ($t -eq '' -or $t -match '[\s"]') { $out += ('"' + ($t -replace '"', '\"') + '"') } else { $out += $t }
            }
            return ,$out
        }
        function Invoke-Launcher {
            # The script in a child shell: -File <script> <verb> (the DM's `fuse-dm.ps1 setup`), or the one-line shape
            # `Get-Content -Raw <script> | Invoke-Expression` under -Command (-Piped), with the code the body left behind
            # read back through `exit`. stdout and stderr of the child land in files: the child's own Start-Process
            # -NoNewWindow output is the child's console, which is this capture.
            param([string[]]$Verb = @(), [switch]$Piped)
            $out = Join-Path $script:T.Tmp 'stdout.txt'
            $err = Join-Path $script:T.Tmp 'stderr.txt'
            if ($Piped) {
                $command = "Get-Content -Raw '" + $script:Script + "' | Invoke-Expression; exit `$fuseDmExitCode"
                $argv = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', $command)
            } else {
                $argv = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $script:Script) + $Verb
            }
            $p = Start-Process -FilePath $script:Shell -ArgumentList (ConvertTo-CommandLineToken $argv) -WorkingDirectory $script:T.Tmp `
                -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
            $p.WaitForExit()
            return @{ Code = [int]$p.ExitCode; Out = [IO.File]::ReadAllText($out); Err = [IO.File]::ReadAllText($err) }
        }
        function Get-Launches {
            # every invocation of the fake, in order: @{ Argv0; Args; Cwd; Launcher; EffortEnv; GitBash }
            $records = @()
            $files = Get-ChildItem -LiteralPath $script:T.Fake -Filter 'launch.*' -ErrorAction SilentlyContinue | Sort-Object { [int]($_.Name.Split('.')[1]) }
            foreach ($f in $files) {
                $rec = @{ Args = @() }
                foreach ($line in [IO.File]::ReadAllLines($f.FullName)) {
                    $i = $line.IndexOf('=')
                    $k = $line.Substring(0, $i); $v = $line.Substring($i + 1)
                    switch ($k) {
                        'arg' { $rec.Args += $v }
                        'argv0' { $rec.Argv0 = $v }
                        'cwd' { $rec.Cwd = $v }
                        'launcher' { $rec.Launcher = $v }
                        'effort_env' { $rec.EffortEnv = $v }
                        'git_bash' { $rec.GitBash = $v }
                    }
                }
                $records += $rec
            }
            return ,$records
        }
        function Get-Sessions {
            # the session launches only: the invocations carrying --model (never `plugin ...`)
            $s = @()
            foreach ($l in (Get-Launches)) { if ($l.Args -contains '--model') { $s += $l } }
            return ,$s
        }
        function Get-Argv($Launch) { return (@($Launch.Args) -join ' ') }
        function Get-State {
            $f = [IO.Path]::Combine($script:T.Home, '.fuse', 'dm-setup', 'state')
            if (Test-Path -LiteralPath $f) { return [IO.File]::ReadAllText($f).Trim() }
            return $null
        }
        function Get-InstallerRuns {
            $f = Join-Path $script:T.Fake 'installer.txt'
            if (Test-Path -LiteralPath $f) { return @([IO.File]::ReadAllLines($f)).Count }
            return 0
        }
        function Get-WingetArgv {
            $f = Join-Path $script:T.Fake 'winget.txt'
            if (Test-Path -LiteralPath $f) { return (@([IO.File]::ReadAllLines($f)) -join ' ') }
            return $null
        }
        function Get-Text { return [IO.File]::ReadAllText($script:Script) }
        function Skip-OffWindows([string]$Because) {
            # only a case that needs powershell.exe, a .cmd or %ProgramFiles% may skip, and only off Windows
            if ($script:OnWindows) { throw 'this case must run on Windows (AC4): ' + $Because }
            Set-ItResult -Skipped -Because $Because
        }
    }

    AfterAll {
        if ($script:TmpRoot -and (Test-Path -LiteralPath $script:TmpRoot)) { Remove-Item -LiteralPath $script:TmpRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }

    BeforeEach {
        $tmp = Join-Path $script:TmpRoot ('case-' + [IO.Path]::GetRandomFileName().Replace('.', ''))
        $script:T = @{ Tmp = $tmp; Home = (Join-Path $tmp 'home'); Bin = (Join-Path $tmp 'bin'); Fake = (Join-Path $tmp 'fake'); GitBash = [IO.Path]::Combine($tmp, 'gitbash', 'bash.exe') }
        foreach ($d in @($script:T.Home, $script:T.Bin, $script:T.Fake, (Split-Path -Parent $script:T.GitBash))) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        [IO.File]::WriteAllText($script:T.GitBash, '')          # Git for Windows "installed": the seam file exists
        Add-Stub $script:WrapperName
        $script:SavedEnv = @{}
        foreach ($k in $script:EnvKeys) { $script:SavedEnv[$k] = [Environment]::GetEnvironmentVariable($k) }
        Set-Env 'HOME' $script:T.Home
        Set-Env 'USERPROFILE' $script:T.Home
        if ($script:OnWindows) { Set-Env 'HOMEDRIVE' ($script:T.Home.Substring(0, 2)); Set-Env 'HOMEPATH' ($script:T.Home.Substring(2)) }
        Set-Env 'TMPDIR' $script:T.Tmp
        Set-Env 'TEMP' $script:T.Tmp
        Set-Env 'TMP' $script:T.Tmp
        Set-Env 'PATH' $script:T.Bin
        Set-Env 'FAKE_DIR' $script:T.Fake
        Set-Env 'FAKE_STUB' $script:Stub
        Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListAbsent
        Set-Env 'FUSE_DM_INSTALLER' (Get-Installer $script:InstallerNoop)   # never the real installer from a test
        Set-Env 'FUSE_DM_GIT_BASH' $script:T.GitBash
        foreach ($k in @('FAKE_CLAUDE_WRITE_STATE', 'FAKE_CLAUDE_EXIT', 'FAKE_CLAUDE_STDERR', 'FAKE_UPDATE_RC', 'FUSE_DM_MODEL', 'FUSE_DM_EFFORT',
                'FUSE_DM_BOOTSTRAP_URL', 'FUSE_DM_LAUNCHER', 'CLAUDE_CODE_EFFORT_LEVEL', 'CLAUDE_CODE_GIT_BASH_PATH')) { Set-Env $k $null }
    }

    AfterEach {
        foreach ($k in $script:EnvKeys) { Set-Env $k $script:SavedEnv[$k] }
        if ($script:T -and (Test-Path -LiteralPath $script:T.Tmp)) { Remove-Item -LiteralPath $script:T.Tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Describe 'Launcher' {
        # AC1: the stage from `claude plugin list`, the exact flag set per stage, FUSE_DM_LAUNCHER=1 and cwd ~/Fuse

        It 'stage one when the plugin is absent' {
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 1 -Because 'one pass: the fake wrote no state, so the loop stops'
            Get-Argv $s[0] | Should -BeExactly (($script:SetupFlags + @('--plugin-url', $script:BootstrapUrl, $script:PartOne)) -join ' ')
            Get-Argv (Get-Launches)[0] | Should -BeExactly 'plugin list' -Because 'the stage is read from `claude plugin list` first'
        }

        It 'stage two when the plugin is present' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 1
            Get-Argv $s[0] | Should -BeExactly (($script:SetupFlags + @($script:PartTwo)) -join ' ')
            $s[0].Args | Should -Not -Contain '--plugin-url'
        }

        It 'the setup flag set is exactly the three flags, in order, on both stages' {
            foreach ($listing in @($script:PluginListAbsent, $script:PluginListPresent)) {
                Reset-Fake
                Set-Env 'FAKE_PLUGIN_LIST' $listing
                Invoke-Launcher 'setup' | Out-Null
                $argv = (Get-Sessions)[0].Args
                (@($argv)[0..4] -join ' ') | Should -BeExactly ($script:SetupFlags -join ' ')
                $flags = @($argv | Where-Object { $_.StartsWith('--') })
                $expected = @('--model', '--effort', '--dangerously-skip-permissions')
                if ($listing -eq $script:PluginListAbsent) { $expected += '--plugin-url' }
                ($flags -join ' ') | Should -BeExactly ($expected -join ' ')
            }
        }

        It 'launcher env and cwd: FUSE_DM_LAUNCHER=1, the effort pin, ~/Fuse created' {
            $fuse = Join-Path $script:T.Home 'Fuse'
            Test-Path -LiteralPath $fuse | Should -BeFalse
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = (Get-Sessions)[0]
            $s.Launcher | Should -BeExactly '1' -Because 'the fake sees FUSE_DM_LAUNCHER=1: the silent case of setup''s permissions check'
            $s.EffortEnv | Should -BeExactly 'medium' -Because 'CLAUDE_CODE_EFFORT_LEVEL outranks --effort: both are set'
            $s.Cwd | Should -Be $fuse -Because 'the working directory is ~/Fuse, created if absent'
            Test-Path -LiteralPath $fuse -PathType Container | Should -BeTrue
        }

        It 'three things said before the first launch' {
            $r = Invoke-Launcher 'setup'
            foreach ($needle in @('theme', 'Enter', '@fusefinance.com', 'Yes')) { $r.Out | Should -BeLike ('*' + $needle + '*') }
        }

        It 'overrides change the argv' {
            Set-Env 'FUSE_DM_MODEL' 'claude-opus-5'
            Set-Env 'FUSE_DM_EFFORT' 'high'
            Set-Env 'FUSE_DM_BOOTSTRAP_URL' 'https://example.test/x.zip'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = (Get-Sessions)[0]
            Get-Argv $s | Should -BeExactly '--model claude-opus-5 --effort high --dangerously-skip-permissions --plugin-url https://example.test/x.zip /fuse-start:begin'
            $s.EffortEnv | Should -BeExactly 'high'
        }

        It 'a token with a space arrives as one argument' {
            # Start-Process joins its argument list unquoted on Windows PowerShell 5.1: the script quotes for it
            Set-Env 'FUSE_DM_MODEL' 'two words'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = (Get-Sessions)[0]
            $s.Args[1] | Should -BeExactly 'two words'
            $s.Args.Count | Should -Be 8
        }

        It 'claude resolved by absolute path, ~/.local/bin first, then PATH' {
            $localBin = [IO.Path]::Combine($script:T.Home, '.local', 'bin')
            New-Item -ItemType Directory -Path $localBin -Force | Out-Null
            $local = Join-Path $localBin $script:WrapperName
            New-Item -ItemType HardLink -Path $local -Target (Join-Path $script:Stub $script:WrapperName) | Out-Null
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            (Get-Sessions)[0].Argv0 | Should -Be $local
            Remove-Item -LiteralPath $local -Force
            Reset-Fake
            Invoke-Launcher 'setup' | Out-Null
            (Get-Sessions)[0].Argv0 | Should -Be (Join-Path $script:T.Bin $script:WrapperName) -Because 'then the PATH copy, by its absolute path'
        }

        It 'missing claude runs the installer then launches' {
            Remove-Stub $script:WrapperName
            Set-Env 'FUSE_DM_INSTALLER' (Get-Installer $script:InstallerInstalls)
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-InstallerRuns | Should -Be 1
            $r.Out | Should -BeLike '*installing Claude Code*'
            $s = Get-Sessions
            $s.Count | Should -Be 1
            $s[0].Argv0 | Should -Be ([IO.Path]::Combine($script:T.Home, '.local', 'bin', $script:WrapperName)) -Because 'resolved again, by absolute path'
            Get-Argv $s[0] | Should -BeExactly (($script:SetupFlags + @('--plugin-url', $script:BootstrapUrl, $script:PartOne)) -join ' ')
        }

        It 'no claude after the installer is 69' {
            Remove-Stub $script:WrapperName
            $r = Invoke-Launcher 'setup'          # the default: the no-op installer
            $r.Code | Should -Be 69
            Get-InstallerRuns | Should -Be 1 -Because 'the installer ran once before giving up'
            $r.Err | Should -BeLike '*claude.ai/install*'
            $r.Err | Should -BeLike '*still*'
            (Get-Launches).Count | Should -Be 0
        }

        It 'an installer that exits does not end the launcher: 69 and the still-missing line, from a file and under iex' {
            # review round 1: the official install.ps1 ends every failure path with `exit 1`; run in the launcher's own
            # process it would close the DM's console under iex, or end the file run with code 1 - the launcher runs the
            # installer expression in a child shell, so the exit is only $LASTEXITCODE and the 69 line still shows
            Remove-Stub $script:WrapperName
            Set-Env 'FUSE_DM_INSTALLER' (Get-InstallerAsText $script:InstallerExits)   # `irm | iex`'s shape, not a file run
            foreach ($piped in @($false, $true)) {
                Reset-Fake
                if ($piped) { $r = Invoke-Launcher -Piped } else { $r = Invoke-Launcher 'setup' }
                $r.Code | Should -Be 69 -Because ('piped=' + $piped + ': ' + $r.Err)
                Get-InstallerRuns | Should -Be 1 -Because ('piped=' + $piped)
                $r.Err | Should -BeLike '*still missing*' -Because ('piped=' + $piped)
                $r.Err | Should -BeLike '*claude.ai/install*' -Because ('piped=' + $piped)
                $r.Out | Should -BeLike '*did not finish cleanly*' -Because ('piped=' + $piped)
                (Get-Launches).Count | Should -Be 0 -Because ('piped=' + $piped)
            }
        }

        It 'the installer never runs when claude is present' {
            foreach ($verb in @(@(), @('setup'), @('update'))) {
                Reset-Fake
                Set-Env 'FUSE_DM_INSTALLER' (Get-Installer $script:InstallerInstalls)
                $r = Invoke-Launcher $verb
                $r.Code | Should -Be 0 -Because $r.Err
                Get-InstallerRuns | Should -Be 0
                $r.Out | Should -Not -BeLike '*installing Claude Code*'
            }
        }

        It 'the script text carries the official installer line and the seam' {
            $text = Get-Text
            $text | Should -BeLike '*irm https://claude.ai/install.ps1 | iex*'
            $text | Should -BeLike '*FUSE_DM_INSTALLER*'
        }

        It 'help names the verbs and the overrides' {
            foreach ($flag in @('-Help', '--help', '-h')) {
                $r = Invoke-Launcher $flag
                $r.Code | Should -Be 0 -Because $r.Err
                foreach ($token in @('setup', 'update', 'FUSE_DM_MODEL', 'FUSE_DM_EFFORT', 'FUSE_DM_BOOTSTRAP_URL', 'FUSE_DM_INSTALLER', 'FUSE_DM_GIT_BASH', '~/Fuse', 'install-shim')) {
                    $r.Out | Should -BeLike ('*' + $token + '*') -Because $flag
                }
                (Get-Launches).Count | Should -Be 0
            }
        }

        It 'unknown verb is 64' {
            $r = Invoke-Launcher 'frobnicate'
            $r.Code | Should -Be 64
            $r.Err | Should -BeLike '*usage: fuse-dm*'
            (Get-Launches).Count | Should -Be 0
        }

        It 'install-shim is not a verb here: the message points at the bash copy, 64' {
            $r = Invoke-Launcher 'install-shim'
            $r.Code | Should -Be 64
            $r.Err | Should -BeLike '*install-shim*'
            $r.Err | Should -BeLike '*Git Bash*'
            (Get-Launches).Count | Should -Be 0
        }

        It 'the iex stub with no argv runs setup' {
            # the one line `irm .../dm.ps1 | iex` reaches the text with no argv and an empty $PSCommandPath: setup, part one
            $r = Invoke-Launcher -Piped
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 1
            Get-Argv $s[0] | Should -BeExactly (($script:SetupFlags + @('--plugin-url', $script:BootstrapUrl, $script:PartOne)) -join ' ')
            $s[0].Launcher | Should -BeExactly '1'
            Get-Argv (Get-Launches)[0] | Should -BeExactly 'plugin list' -Because 'the stage is read from `claude plugin list` first'
            Get-Text | Should -BeLike '*PSCommandPath*' -Because 'the header says why: $PSCommandPath is empty when the text is read through iex'
        }

        It 'the iex stub hands the code back and leaves no launcher env behind' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_EXIT' '3'
            Set-Env 'FAKE_CLAUDE_STDERR' 'Error: something else broke'
            $r = Invoke-Launcher -Piped
            $r.Code | Should -Be 3 -Because 'claude''s own code, read back from the body under iex'
            (Get-Sessions).Count | Should -Be 1
            $r.Out | Should -Not -BeLike '*opus*'
        }

        It 'bare verb from a file is still daily' {
            $r = Invoke-Launcher
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 1
            Get-Argv $s[0] | Should -BeExactly ($script:DailyArgv -join ' ')
            $s[0].Args | Should -Not -Contain '--plugin-url'
            $s[0].Launcher | Should -BeExactly 'unset'
        }
    }

    Describe 'RelaunchLoop' {
        # AC2 of OPX-1331: after each exit the launcher reads ~/.fuse/dm-setup/state - bootstrap-done -> part two,
        # needs-second-pass -> part two once more, done or anything else -> stop; never a fourth launch

        It 'bootstrap-done goes to part two' {
            Set-Env 'FAKE_CLAUDE_WRITE_STATE' 'bootstrap-done,done'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 2
            $s[0].Args[-1] | Should -BeExactly $script:PartOne
            $s[0].Args | Should -Contain '--plugin-url'
            Get-Argv $s[1] | Should -BeExactly (($script:SetupFlags + @($script:PartTwo)) -join ' ') -Because 'part two: no --plugin-url, the setup prompt'
            Get-State | Should -BeExactly 'done'
        }

        It 'needs-second-pass runs part two once more' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_WRITE_STATE' 'needs-second-pass,done'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 2
            foreach ($x in $s) { Get-Argv $x | Should -BeExactly (($script:SetupFlags + @($script:PartTwo)) -join ' ') }
        }

        It 'done stops' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_WRITE_STATE' 'done,done'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            (Get-Sessions).Count | Should -Be 1
        }

        It 'unknown state stops' {
            foreach ($value in @('banana', '', $null)) {
                Reset-Fake
                Remove-Item -LiteralPath (Join-Path $script:T.Home '.fuse') -Recurse -Force -ErrorAction SilentlyContinue
                Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
                if ($null -eq $value) { Set-Env 'FAKE_CLAUDE_WRITE_STATE' $null } else { Set-Env 'FAKE_CLAUDE_WRITE_STATE' ($value + ',done') }
                $r = Invoke-Launcher 'setup'
                $r.Code | Should -Be 0 -Because $r.Err
                (Get-Sessions).Count | Should -Be 1
            }
        }

        It 'at most three launches, then the closing line' {
            Set-Env 'FAKE_CLAUDE_WRITE_STATE' 'bootstrap-done,needs-second-pass,needs-second-pass,needs-second-pass'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 3 -Because 'never a fourth launch'
            (@($s | ForEach-Object { $_.Args[-1] }) -join ' ') | Should -BeExactly (@($script:PartOne, $script:PartTwo, $script:PartTwo) -join ' ')
            $closing = @($r.Out -split "`r?`n" | Where-Object { $_.StartsWith('fuse-dm:') })[-1]
            $closing | Should -BeLike '*Fuse Claude*' -Because 'one plain closing line, and it names the icon for tomorrow'
            $closing | Should -Not -BeLike '*--*' -Because 'plain: no flags in the closing line'
            ([regex]::Matches($r.Out, 'Fuse Claude')).Count | Should -Be 1 -Because 'one closing line, not one per pass'
        }

        It 'the launcher only reads the state file' {
            $dir = [IO.Path]::Combine($script:T.Home, '.fuse', 'dm-setup')
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            [IO.File]::WriteAllText((Join-Path $dir 'state'), "done`n")
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-State | Should -BeExactly 'done' -Because 'untouched by the launcher'
            $writes = [regex]::Matches((Get-Text), '(?m)^.*StateFile.*(Set-Content|Out-File|Add-Content|WriteAllText|WriteAllLines|\s>\s).*$')
            $writes.Count | Should -Be 0 -Because 'no write into the state file'
        }
    }

    Describe 'NoBypass' {
        # AC2: the daily and `update` sessions never bypass and the script never touches a settings file - argv and text

        It 'daily argv is model and auto only' {
            $r = Invoke-Launcher
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 1
            Get-Argv $s[0] | Should -BeExactly ($script:DailyArgv -join ' ')
            $s[0].Cwd | Should -Be (Join-Path $script:T.Home 'Fuse')
            $s[0].Launcher | Should -BeExactly 'unset' -Because 'a daily session is not a setup session: FUSE_DM_LAUNCHER stays unset'
            $s[0].EffortEnv | Should -BeExactly 'unset' -Because 'the seat''s effort on a daily session'
            (Get-Launches).Count | Should -Be 1 -Because 'no plugin list, no update on the daily verb'
        }

        It 'update runs the two update lines then daily' {
            $r = Invoke-Launcher 'update'
            $r.Code | Should -Be 0 -Because $r.Err
            (@((Get-Launches) | ForEach-Object { Get-Argv $_ }) -join '|') | Should -BeExactly (@('plugin marketplace update fuse-internal', 'plugin update deployment-manager@fuse-internal', ($script:DailyArgv -join ' ')) -join '|')
        }

        It 'a failed update still opens the day' {
            Set-Env 'FAKE_UPDATE_RC' '1'
            $r = Invoke-Launcher 'update'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-Argv (Get-Launches)[-1] | Should -BeExactly ($script:DailyArgv -join ' ')
            ($r.Out + $r.Err).ToLower() | Should -BeLike '*update*'
        }

        It 'no bypass token on daily or update' {
            foreach ($verb in @(@(), @('update'))) {
                Reset-Fake
                Invoke-Launcher $verb | Out-Null
                (Get-Launches).Count | Should -BeGreaterThan 0 -Because 'the session launched: the assertion below is not vacuous'
                foreach ($l in (Get-Launches)) {
                    foreach ($bad in @('--dangerously-skip-permissions', 'bypassPermissions', '--plugin-url')) {
                        $l.Args | Should -Not -Contain $bad
                        Get-Argv $l | Should -Not -BeLike ('*' + $bad + '*')
                    }
                }
            }
        }

        It 'the script text never names a settings file or bypassPermissions' {
            $text = Get-Text
            foreach ($bad in @('settings.json', 'settings.local.json', 'defaultMode', 'bypassPermissions', '.claude/settings', '.claude\settings')) {
                $text.Contains($bad) | Should -BeFalse -Because $bad
            }
        }

        It 'daily model follows the override' {
            Set-Env 'FUSE_DM_MODEL' 'claude-sonnet-5'
            Invoke-Launcher | Out-Null
            Get-Argv (Get-Sessions)[0] | Should -BeExactly '--model claude-sonnet-5 --permission-mode auto'
        }
    }

    Describe 'ModelRetry' {
        # AC4 of OPX-1331: `--model fable` refused on the seat -> one retry with `opus`, said in one line; any other exit propagates

        It 'retry once with opus on refusal' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_EXIT' '1'
            Set-Env 'FAKE_CLAUDE_STDERR' $script:Refusal
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            $s.Count | Should -Be 2
            (@($s[0].Args)[0..1] -join ' ') | Should -BeExactly '--model fable'
            Get-Argv $s[1] | Should -BeExactly ('--model opus --effort medium --dangerously-skip-permissions ' + $script:PartTwo)
            $r.Err | Should -BeLike ('*' + $script:Refusal + '*') -Because 'claude''s own stderr still reaches the terminal'
        }

        It 'no retry on other non-zero exit' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_EXIT' '3'
            Set-Env 'FAKE_CLAUDE_STDERR' 'Error: something else broke'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 3 -Because 'claude''s own code'
            (Get-Sessions).Count | Should -Be 1
            ($r.Out + $r.Err) | Should -Not -BeLike '*opus*'
        }

        It 'the retry is said in one line' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_EXIT' '1'
            Set-Env 'FAKE_CLAUDE_STDERR' $script:Refusal
            $r = Invoke-Launcher 'setup'
            $said = @($r.Out -split "`r?`n" | Where-Object { $_ -like '*opus*' })
            $said.Count | Should -Be 1 -Because $r.Out
            $said[0] | Should -BeLike '*fable*'
        }

        It 'only one retry even when opus is refused too' {
            Set-Env 'FAKE_PLUGIN_LIST' $script:PluginListPresent
            Set-Env 'FAKE_CLAUDE_EXIT' '1,1'
            Set-Env 'FAKE_CLAUDE_STDERR' 'Error: the model fable opus is not available'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 1
            (Get-Sessions).Count | Should -Be 2
        }

        It 'the retry model carries into the next pass' {
            Set-Env 'FAKE_CLAUDE_EXIT' '1'
            Set-Env 'FAKE_CLAUDE_STDERR' $script:Refusal
            Set-Env 'FAKE_CLAUDE_WRITE_STATE' ',bootstrap-done,done'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            $s = Get-Sessions
            (@($s | ForEach-Object { $_.Args[1] }) -join ' ') | Should -BeExactly 'fable opus opus'
            (@($s | ForEach-Object { $_.Args[-1] }) -join ' ') | Should -BeExactly (@($script:PartOne, $script:PartOne, $script:PartTwo) -join ' ')
        }

        It 'a daily refusal retries on opus and returns claude''s code otherwise' {
            Set-Env 'FAKE_CLAUDE_EXIT' '1,5'
            Set-Env 'FAKE_CLAUDE_STDERR' $script:Refusal
            $r = Invoke-Launcher
            $r.Code | Should -Be 5
            $s = Get-Sessions
            $s.Count | Should -Be 2
            Get-Argv $s[1] | Should -BeExactly '--model opus --permission-mode auto'
        }
    }

    Describe 'GitForWindows' {
        # AC3: Git for Windows before any claude launch - missing -> the windows.md row 2 line, then
        # CLAUDE_CODE_GIT_BASH_PATH set in THIS process (no PATH refresh); present -> nothing; already set -> untouched

        It 'missing: the winget line ran, the variable is set from the seam, before any claude launch' {
            Remove-Item -LiteralPath $script:T.GitBash -Force              # not installed
            Add-Stub 'winget'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeExactly 'install -e --id Git.Git'
            $launches = Get-Launches
            $launches.Count | Should -Be 2
            $launches[0].GitBash | Should -Be $script:T.GitBash -Because 'the first claude call already sees the variable'
            (Get-Sessions)[0].GitBash | Should -Be $script:T.GitBash
            Test-Path -LiteralPath $script:T.GitBash | Should -BeTrue -Because 'the fake winget installed it'
        }

        It 'git on PATH: no winget, and the variable is set when bash.exe is at its default place' {
            Add-Stub 'git'
            Add-Stub 'winget'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeNullOrEmpty
            (Get-Sessions)[0].GitBash | Should -Be $script:T.GitBash
        }

        It 'git on PATH and no bash.exe at the default place: nothing is set, nothing installed' {
            Add-Stub 'git'
            Add-Stub 'winget'
            Remove-Item -LiteralPath $script:T.GitBash -Force
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeNullOrEmpty
            (Get-Sessions)[0].GitBash | Should -BeExactly 'unset'
        }

        It 'already set: untouched' {
            Set-Env 'CLAUDE_CODE_GIT_BASH_PATH' 'X:\preset\bash.exe'
            Add-Stub 'winget'
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeNullOrEmpty
            (Get-Sessions)[0].GitBash | Should -BeExactly 'X:\preset\bash.exe'
        }

        It 'winget missing: one line naming git-scm.com, 69, no launch' {
            Remove-Item -LiteralPath $script:T.GitBash -Force
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 69
            $r.Err | Should -BeLike '*git-scm.com/downloads/win*'
            (Get-Launches).Count | Should -Be 0
        }

        It 'the install runs before the claude install and the daily verb checks too' {
            Remove-Item -LiteralPath $script:T.GitBash -Force
            Add-Stub 'winget'
            Remove-Stub $script:WrapperName
            Set-Env 'FUSE_DM_INSTALLER' (Get-Installer $script:InstallerInstalls)
            $r = Invoke-Launcher
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeExactly 'install -e --id Git.Git'
            Get-InstallerRuns | Should -Be 1
            (Get-Sessions)[0].GitBash | Should -Be $script:T.GitBash
        }

        It 'Windows: Git installed at %ProgramFiles% but off PATH sets the variable without winget' {
            $default = [IO.Path]::Combine([string]$env:ProgramFiles, 'Git', 'bin', 'bash.exe')
            if (-not $script:OnWindows) { Skip-OffWindows 'needs %ProgramFiles%\Git\bin\bash.exe (windows-latest ships Git for Windows)'; return }
            Test-Path -LiteralPath $default | Should -BeTrue -Because 'windows-latest ships Git for Windows at its default path'
            Set-Env 'FUSE_DM_GIT_BASH' $null
            $r = Invoke-Launcher 'setup'
            $r.Code | Should -Be 0 -Because $r.Err
            Get-WingetArgv | Should -BeNullOrEmpty
            (Get-Sessions)[0].GitBash | Should -Be $default
        }
    }

    Describe 'Text' {
        # the file's own pins: what a future edit could silently drop

        It 'is pure 7-bit ASCII (Windows PowerShell 5.1 reads a BOM-less file as cp1252)' {
            $bytes = [IO.File]::ReadAllBytes($script:Script)
            $bytes.Length | Should -BeGreaterThan 0
            $high = @($bytes | Where-Object { $_ -gt 127 })
            $high.Count | Should -Be 0
        }

        It 'parses on this PowerShell' {
            $tokens = $null; $errors = $null
            [System.Management.Automation.Language.Parser]::ParseFile($script:Script, [ref]$tokens, [ref]$errors) | Out-Null
            @($errors).Count | Should -Be 0 -Because (@($errors | ForEach-Object { $_.ToString() }) -join '; ')
        }

        It 'carries the bypass flag once, in the setup argv, and never a settings file' {
            $text = Get-Text
            ([regex]::Matches($text, '--dangerously-skip-permissions')).Count | Should -Be 1
            foreach ($bad in @('settings.json', 'bypassPermissions', 'defaultMode', '.claude/settings', '.claude\settings')) { $text.Contains($bad) | Should -BeFalse -Because $bad }
        }

        It 'never pipes the claude session: no 2>&1, stdout stays on the console' {
            $text = Get-Text
            $text.Contains('2>&1') | Should -BeFalse
            $text | Should -BeLike '*-NoNewWindow*'
            $text | Should -BeLike '*-RedirectStandardError*'
            $text.Contains('-RedirectStandardOutput') | Should -BeFalse -Because 'the TUI owns stdout'
        }

        It 'exits only in the file-run tail, guarded by $PSCommandPath' {
            $text = Get-Text
            ([regex]::Matches($text, 'exit ')).Count | Should -Be 1
            $line = @($text -split "`r?`n" | Where-Object { $_ -match 'exit ' })[0]
            $line | Should -BeLike '*$PSCommandPath*'
        }

        It 'declares the verb and help parameters first, for iex and for the file' {
            $text = Get-Text
            $text | Should -Match '(?m)^param\('
            $text | Should -BeLike '*$Verb*'
            $text | Should -BeLike '*$Help*'
        }

        It 'reads its seams from FUSE_DM_* only and names the row 2 winget line' {
            $text = Get-Text
            foreach ($needle in @('FUSE_DM_MODEL', 'FUSE_DM_EFFORT', 'FUSE_DM_BOOTSTRAP_URL', 'FUSE_DM_INSTALLER', 'FUSE_DM_GIT_BASH', 'FUSE_DM_LAUNCHER',
                    'CLAUDE_CODE_EFFORT_LEVEL', 'CLAUDE_CODE_GIT_BASH_PATH', 'winget install -e --id Git.Git', 'https://git-scm.com/downloads/win')) {
                $text.Contains($needle) | Should -BeTrue -Because $needle
            }
        }
    }
}
