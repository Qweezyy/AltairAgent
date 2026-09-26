# Altair — one-command installer for Windows.
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
Write-Host "  ★ $AppName installer" -ForegroundColor Yellow
Write-Host ""

# 1. Latest release from the GitHub API.
Info "Looking up the latest release..."
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "AltairInstaller"; "Accept" = "application/vnd.github+json" }
} catch {
    Warn "No published release found for $Repo yet."
    Warn "Once $AppName 0.1.0 is released this command will install it automatically."
    Warn "For now you can run from source: clone the repo and run 'python pc/main.py'."
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
        Expand-Archive -Path $file -DestinationPath $Root -Force

        # Flatten a single top-level folder from the zip, if present.
        $entries = @(Get-ChildItem $Root)
        if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
            $inner = $entries[0].FullName
            Get-ChildItem $inner -Force | Move-Item -Destination $Root -Force
            Remove-Item $inner -Recurse -Force
        }

        # Locate the launch exe (Altair shell preferred, else the backend exe).
        $exe = Get-ChildItem $Root -Recurse -Filter "$AppName.exe" | Select-Object -First 1
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
Ok "Done. Enjoy $AppName ✨"
Write-Host ""
}
