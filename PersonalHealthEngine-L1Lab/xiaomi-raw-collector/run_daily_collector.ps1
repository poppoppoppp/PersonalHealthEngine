$ErrorActionPreference = "Stop"

$Root = "D:\PersonalHealthEngine-L1Lab\xiaomi-raw-collector"
$Python = "D:\PersonalHealthEngine-L1Lab\.venv\Scripts\python.exe"
$Collector = Join-Path $Root "collector.py"
$LogDir = Join-Path $Root "logs"

New-Item -ItemType Directory -Force $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$Stdout = Join-Path $LogDir "collector_$Stamp.out.txt"
$Stderr = Join-Path $LogDir "collector_$Stamp.err.txt"
$Summary = Join-Path $LogDir "collector_$Stamp.summary.txt"

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

if (-not (Test-Path $Collector)) {
    throw "Collector not found: $Collector"
}

$Started = Get-Date

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList @($Collector) `
    -WorkingDirectory $Root `
    -Wait `
    -PassThru `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr

$Finished = Get-Date

@"
started=$($Started.ToString("o"))
finished=$($Finished.ToString("o"))
exit_code=$($Process.ExitCode)
stdout=$Stdout
stderr=$Stderr
"@ | Set-Content -Encoding UTF8 $Summary

if ($Process.ExitCode -ne 0) {
    exit $Process.ExitCode
}

exit 0
