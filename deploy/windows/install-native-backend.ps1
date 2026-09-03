<#
.SYNOPSIS
  Installs the C-ECM backend as a native Windows Service — the FileNet
  content-write path (a subprocess call to a local WebSphere Java runtime,
  see backend/app/config.py's JAVA_BIN/JACE_JAR/WAS_* settings) needs to
  run directly on Windows next to a real WebSphere install; it can't be
  containerized, which is the whole reason this path exists alongside the
  plain docker-compose.yml one.

.DESCRIPTION
  Run postgres+nginx first (docker-compose.windows-hybrid.yml in this same
  folder), THEN run this script to set up and start the backend itself.
  Must be run from an elevated (Administrator) PowerShell prompt.

  What this script does NOT do, because it's licensed IBM software with
  its own installer, not something to script here: install WebSphere
  Application Server or FileNet's CEClient (Jace.jar). Install those
  yourself first, then point FD_WAS_JAVA_HOME/FD_JACE_JAR/FD_WAS_RUNTIMES/
  FD_WAS_PROFILE_PROPS at wherever you installed them, in deploy\.env —
  see backend/app/config.py for every FileNet-related setting and its
  default path.

.PARAMETER PythonExe
  Path to a Python 3.12+ interpreter. Defaults to whatever "python" resolves
  to on PATH.
#>
param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot\..\.."
$BackendDir = Join-Path $RepoRoot "backend"
$EnvFile = Join-Path $PSScriptRoot "..\.env"
$ServiceName = "CECMBackend"

if (-not (Test-Path $EnvFile)) {
    Write-Error "Missing $EnvFile -- copy deploy\.env.production.example to deploy\.env and fill it in first."
}

# --- venv + dependencies ----------------------------------------------------
Write-Host "==> Checking Python version..."
& $PythonExe --version
$VenvDir = Join-Path $BackendDir ".venv-prod"
if (-not (Test-Path $VenvDir)) {
    Write-Host "==> Creating virtualenv at $VenvDir..."
    & $PythonExe -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "==> Installing dependencies (including the postgres driver)..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")
& $VenvPython -m pip install "psycopg[binary]>=3.2"

# --- environment variables --------------------------------------------------
# The service inherits the MACHINE environment at start time, so .env's
# contents are set there (persistently) rather than passed some other way
# nssm-specific -- this also means they're visible to `python run.py` if
# you ever run it directly for debugging, not just under the service.
Write-Host "==> Applying deploy\.env as machine-level environment variables..."
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Length -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Machine")
    }
}
# Points at the postgres container from docker-compose.windows-hybrid.yml,
# which publishes to 127.0.0.1:5432 specifically so the native backend can
# reach it -- overrides whatever FD_DB_HOST .env may have set for the pure-
# container deployment.
[Environment]::SetEnvironmentVariable("FD_DB_ENGINE", "postgres", "Machine")
[Environment]::SetEnvironmentVariable("FD_DB_HOST", "127.0.0.1", "Machine")
[Environment]::SetEnvironmentVariable("FD_DB_PORT", "5432", "Machine")

# --- Windows Service via NSSM -----------------------------------------------
$Nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $Nssm) {
    Write-Error "nssm.exe not found on PATH. Install it first (e.g. 'choco install nssm -y'), then re-run this script."
}

Write-Host "==> Registering the '$ServiceName' Windows Service..."
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "    Service already exists -- stopping it to reinstall."
    nssm stop $ServiceName confirm | Out-Null
    nssm remove $ServiceName confirm | Out-Null
}
nssm install $ServiceName $VenvPython "run.py"
nssm set $ServiceName AppDirectory $BackendDir
nssm set $ServiceName AppStdout (Join-Path $BackendDir "service-stdout.log")
nssm set $ServiceName AppStderr (Join-Path $BackendDir "service-stderr.log")
nssm set $ServiceName Start SERVICE_AUTO_START

Write-Host "==> Starting $ServiceName..."
Start-Service -Name $ServiceName
Start-Sleep -Seconds 3
Get-Service -Name $ServiceName | Format-List Name, Status, StartType

Write-Host ""
Write-Host "==> Done. Backend running natively on http://127.0.0.1:8020"
Write-Host "    nginx (from docker-compose.windows-hybrid.yml) proxies http://<this-host>/api/ to it."
Write-Host "    Logs: $BackendDir\service-stdout.log / service-stderr.log"
