# Pin Palette installer for Windows.
#   irm https://raw.githubusercontent.com/IamOumarIbrahim/pin-palette/main/install.ps1 | iex
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Repo = "IamOumarIbrahim/pin-palette"

# Use this folder if the script runs from a checkout; otherwise download to %USERPROFILE%\pin-palette.
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "app.py"))) {
    $Dir = $PSScriptRoot
} else {
    $Dir = Join-Path $HOME "pin-palette"
    Write-Host "Downloading Pin Palette to $Dir"
    $zip = Join-Path $env:TEMP "pin-palette.zip"
    $tmp = Join-Path $env:TEMP "pin-palette-src"
    Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -ErrorAction Stop
    New-Item -ItemType Directory -Force $Dir | Out-Null
    Copy-Item (Join-Path (Get-ChildItem $tmp)[0].FullName "*") $Dir -Recurse -Force
    Remove-Item $zip, $tmp -Recurse -Force
}
Set-Location $Dir

# Find Python 3.9+. The Microsoft Store "python" stub does not count.
# Returns the full path of a suitable python.exe, or $null.
function Find-Python {
    $check = Join-Path $env:TEMP "pin-palette-pycheck.py"
    Set-Content $check "import sys`nprint(sys.executable if sys.version_info >= (3, 9) else '')"
    $found = $null
    foreach ($try in @({ py -3 $check }, { python $check }, { python3 $check })) {
        try {
            $exe = (& $try 2>$null) | Select-Object -Last 1
            if ($exe -and (Test-Path $exe.Trim())) { $found = $exe.Trim(); break }
        } catch {}
    }
    Remove-Item $check -ErrorAction SilentlyContinue
    return $found
}
$Py = Find-Python
if (-not $Py) {
    Write-Host "Python not found. Installing Python 3.12 with winget..."
    try {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    } catch {}
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $Py = Find-Python
    if (-not $Py) {
        Write-Host "Could not install Python automatically. Get it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again."
        exit 1
    }
}

Write-Host "Installing dependencies"
& $Py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -q --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# Desktop shortcut.
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Pin Palette.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = Join-Path $Dir "Start.bat"
$s.WorkingDirectory = $Dir
$s.Save()
Write-Host "Added 'Pin Palette' to your Desktop."

Write-Host "Done. Starting Pin Palette..."
Start-Process (Join-Path $Dir "Start.bat") -WorkingDirectory $Dir
