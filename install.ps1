# Altair - one-command installer for Windows.
#
#   irm https://raw.githubusercontent.com/Qweezyy/AltairAgent/main/install.ps1 | iex
#
# Downloads the latest release from GitHub, installs it (NSIS/MSI setup if the
# release ships one, otherwise a portable build into %LOCALAPPDATA%\Altair with
# Desktop + Start Menu shortcuts), and launches the app. FOSS, no telemetry.

# Everything runs inside a script block: under `irm | iex` the script shares the
# caller's session, and settings like $ErrorActionPreference would leak into it.
& {
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws the progress bar per chunk, which makes
# Invoke-WebRequest many times slower on a large download.
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo    = "Qweezyy/AltairAgent"
$AppName = "Altair"
$Root    = Join-Path $env:LOCALAPPDATA $AppName

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  == $AppName installer ==" -ForegroundColor Yellow
Write-Host ""

# 1. Latest release from the GitHub API.
Info "Looking up the latest release..."
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "AltairInstaller"; "Accept" = "application/vnd.github+json" }
} catch {
    $status = $null
    try { $status = [int]$_.Exception.Response.StatusCode } catch {}
    if ($status -eq 404) {
        Warn "No published release found for $Repo yet."
        Warn "Once the first $AppName release is published this command will install it automatically."
        Warn "For now you can run from source: clone the repo and run 'python pc/main.py'."
    } else {
        Warn "Couldn't reach GitHub to look up the latest release: $($_.Exception.Message)"
        Warn "Check your internet connection (or proxy) and run the command again."
    }
    return
}

$assets = @($rel.assets)
if (-not $assets -or $assets.Count -eq 0) {
    Warn "Release '$($rel.tag_name)' has no downloadable assets yet."
    return
}
Ok "Found $($rel.tag_name)."

# The release ships a portable .zip (backend + Altair window). A setup installer
# is used only if a release provides one and no app .zip is present.
$zip   = $assets | Where-Object { $_.name -match '(?i)(altair|localaiagent).*\.zip$' } | Select-Object -First 1
if (-not $zip) { $zip = $assets | Where-Object { $_.name -match '(?i)\.zip$' } | Select-Object -First 1 }
$setup = $null
if (-not $zip) {
    $setup = $assets | Where-Object { $_.name -match '(?i)(setup|installer).*\.exe$' -or $_.name -match '(?i)\.msi$' } | Select-Object -First 1
}

$tmp = Join-Path $env:TEMP ("altair_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    if ($setup) {
        # --- Installer path (NSIS/MSI) ---
        $file = Join-Path $tmp $setup.name
        Info "Downloading $($setup.name) ($([math]::Round($setup.size/1MB,1)) MB)..."
        Invoke-WebRequest -Uri $setup.browser_download_url -OutFile $file
        Info "Running the installer..."
        if ($setup.name -match '(?i)\.msi$') {
            Start-Process msiexec.exe -ArgumentList "/i", "`"$file`"", "/passive" -Wait
        } else {
            # NSIS silent install.
            Start-Process $file -ArgumentList "/S" -Wait
        }
        Ok "$AppName installed."
    }
    elseif ($zip) {
        # --- Portable path ---
        $file = Join-Path $tmp $zip.name
        Info "Downloading $($zip.name) ($([math]::Round($zip.size/1MB,1)) MB)..."
        Invoke-WebRequest -Uri $zip.browser_download_url -OutFile $file

        Info "Installing into $Root ..."
        # A running copy locks its files; close it first (user data lives elsewhere,
        # in %LOCALAPPDATA%\LocalAIAgent, and is not touched).
        Get-Process -Name "Altair", "LocalAIAgent" -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) } |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
        Info "Unpacking..."
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($file, $Root)

        # Flatten a single top-level folder from the zip, if present.
        $entries = @(Get-ChildItem $Root)
        if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
            $inner = $entries[0].FullName
            Get-ChildItem $inner -Force | Move-Item -Destination $Root -Force
            Remove-Item $inner -Recurse -Force
        }

        # Locate the launch exe (Altair shell preferred, else the backend exe).
        # Not bin\altair.exe (the terminal command): Windows file names ignore case.
        $exe = Get-ChildItem $Root -Recurse -Filter "$AppName.exe" | Where-Object { $_.Directory.Name -ne "bin" } | Select-Object -First 1
        if (-not $exe) { $exe = Get-ChildItem $Root -Recurse -Filter "LocalAIAgent.exe" | Select-Object -First 1 }
        if (-not $exe) { throw "Couldn't find the application executable in the archive." }

        # Shortcuts: Desktop + Start Menu.
        $shell = New-Object -ComObject WScript.Shell
        foreach ($dir in @([Environment]::GetFolderPath("Desktop"),
                           (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
            $lnk = $shell.CreateShortcut((Join-Path $dir "$AppName.lnk"))
            $lnk.TargetPath = $exe.FullName
            $lnk.WorkingDirectory = $exe.DirectoryName
            $lnk.IconLocation = $exe.FullName
            $lnk.Save()
        }
        Ok "$AppName installed into $Root (shortcuts created)."
        # The terminal command `altair` (altair.exe next to the app): the app folder goes on the
        # user's PATH, so it works in any new terminal. ALTAIR_NO_PATH=1 skips this.
        $cli = Get-ChildItem (Join-Path $Root "bin") -Filter "altair.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cli -and -not $env:ALTAIR_NO_PATH) {
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $parts = @($userPath -split ";" | Where-Object { $_ })
            if ($parts -notcontains $cli.DirectoryName) {
                [Environment]::SetEnvironmentVariable("Path", (($parts + $cli.DirectoryName) -join ";"), "User")
                Ok "Terminal command 'altair' added to PATH (open a new terminal to use it)."
            }
        }

        $wv2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        $hasWebView2 = @(
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$wv2",
            "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$wv2",
            "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$wv2"
        ) | Where-Object { (Get-ItemProperty -Path $_ -Name pv -ErrorAction SilentlyContinue).pv } |
            Select-Object -First 1
        if (-not $hasWebView2) {
            Warn "The $AppName window needs the Microsoft Edge WebView2 Runtime, which was not found."
            Warn "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 if the window doesn't open."
        }

        Info "Launching $AppName..."
        Start-Process $exe.FullName
    }
    else {
        Warn "Release '$($rel.tag_name)' has no Windows installer or .zip asset."
        return
    }
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Ok "Done. Enjoy $AppName!"
Write-Host ""
}
