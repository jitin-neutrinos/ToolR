# install.ps1 - ToolR installer, Windows edition (no WSL / Git Bash needed).
# One-liner:  irm https://toolr.jitinnair.com/install.ps1 | iex
# Mirrors install.sh (bash). Targets Windows PowerShell 5.1+ (no PS7-only syntax).
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

# ---------- banner (ToolR brand: navy / white / cyan accent) -------------------
$ESC = [char]27
$B = "$ESC[38;2;0;200;240m"; $BB = "$ESC[1;38;2;0;200;240m"
$W = "$ESC[38;2;245;248;252m"; $G = "$ESC[38;2;80;220;120m"
$R = "$ESC[38;2;240;90;90m"; $D = "$ESC[38;2;140;148;160m"; $X = "$ESC[0m"
function Write-Step($m) { Write-Host "  $B>$X $D$m$X" }
function Write-Ok($m)   { Write-Host "  $G OK $X $m" }
function Die($m)        { Write-Host "$R[toolr:error]$X $m"; exit 1 }
# UTF-8 without BOM (PS5.1 Set-Content -Encoding utf8 adds a BOM; the TUI
# reads these files as UTF-8 and a BOM shows up as garbage in the logo).
function Write-Utf8NoBom([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host ''
Write-Host "$BB  ToolR $X $D route every prompt to the right skill (windows) $X"
Write-Host ''

# ---------- 0. prerequisites ---------------------------------------------------
if ($PSVersionTable.PSVersion.Major -lt 5) { Die "PowerShell 5+ required (you have $($PSVersionTable.PSVersion))." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Die "git is required. Install it first:  winget install --id Git.Git -e"
}
$py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
  Die "python is required (3.9+). Install it first:  winget install --id Python.Python.3.12 -e"
}

# ---------- 1. repo ------------------------------------------------------------
$ToolRDir = if ($env:TOOLR_DIR) { $env:TOOLR_DIR } else { Join-Path $HOME 'toolr' }
$RepoUrl  = if ($env:TOOLR_REPO_URL) { $env:TOOLR_REPO_URL } else { 'https://github.com/jitin-neutrinos/ToolR' }
if (Test-Path (Join-Path $ToolRDir '.git')) {
  Write-Step "existing copy at $ToolRDir - pulling latest"
  git -C $ToolRDir pull --ff-only 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "  $D(pull failed, using existing copy)$X" }
} else {
  Write-Step "cloning $RepoUrl"
  git clone --depth 1 $RepoUrl $ToolRDir
  if ($LASTEXITCODE -ne 0) { Die "clone failed - check network / repo access" }
}
Write-Ok "repo at $ToolRDir"

# ---------- 2. TUI installer (same one the mac/linux one-liner runs) -----------
Write-Step "detecting harnesses and installing"
& $py.Source (Join-Path $ToolRDir 'toolr_install.py') @args
$rc = $LASTEXITCODE
if ($rc -ne 0) { Die "installer exited $rc" }

Write-Host ''
Write-Host "  $G Done.$X $D Route a prompt to try it:$X"
Write-Host "  $W~\.tool-router\route.cmd `"your request here`"$X"
exit 0
