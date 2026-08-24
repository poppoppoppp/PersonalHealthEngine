$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$appRoot = Join-Path $repoRoot 'PersonalHealthEngine-L7\app'
$androidRoot = Join-Path $appRoot 'android'
$keyProperties = Join-Path $androidRoot 'key.properties'
$keystore = 'D:\PersonalHealthEngine\secrets\android\phe-release.jks'
$definesFile = 'D:\PersonalHealthEngine\secrets\android\production-defines.json'
$artifact = 'D:\PersonalHealthEngine\artifacts\PHE-Android-production.apk'

function Get-KeyringSecret([string]$username) {
    $value = & python -c `
        'import keyring,sys; value=keyring.get_password(sys.argv[1],sys.argv[2]); print(value or chr(32))' `
        'personal-health-engine' $username
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Missing Windows keyring value for $username"
    }
    return $value.Trim()
}

function Set-Utf8NoBom([string]$path, [string]$content) {
    [System.IO.File]::WriteAllText(
        $path,
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

$apiToken = Get-KeyringSecret 'l7_api_token'
$signingPassword = Get-KeyringSecret 'android_keystore_password'
if (-not (Test-Path -LiteralPath $keystore)) {
    throw "Release keystore not found: $keystore"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $definesFile) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $artifact) | Out-Null

try {
    $defines = @{
        PHE_API_BASE_URL = 'https://47.111.229.39'
        PHE_API_TOKEN = $apiToken
    } | ConvertTo-Json
    Set-Utf8NoBom $definesFile $defines

    $keyConfig = @(
        "storePassword=$signingPassword"
        "keyPassword=$signingPassword"
        'keyAlias=phe-release'
        'storeFile=D:/PersonalHealthEngine/secrets/android/phe-release.jks'
    ) -join [Environment]::NewLine
    Set-Utf8NoBom $keyProperties $keyConfig

    Push-Location $appRoot
    try {
        & flutter --no-version-check build apk --release `
            "--dart-define-from-file=$definesFile"
        if ($LASTEXITCODE -ne 0) { throw 'Flutter release build failed' }
    } finally {
        Pop-Location
    }

    $builtApk = Join-Path $appRoot 'build\app\outputs\flutter-apk\app-release.apk'
    Copy-Item -LiteralPath $builtApk -Destination $artifact -Force
    Write-Output $artifact
} finally {
    Remove-Item -LiteralPath $keyProperties -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $definesFile -Force -ErrorAction SilentlyContinue
    $apiToken = $null
    $signingPassword = $null
}
