$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$appRoot = Join-Path $repoRoot 'PersonalHealthEngine-L7\app'
$androidRoot = Join-Path $appRoot 'android'
$keyProperties = Join-Path $androidRoot 'key.properties'
$keystore = 'D:\PersonalHealthEngine\secrets\android\phe-release.jks'
$definesFile = 'D:\PersonalHealthEngine\secrets\android\production-defines.json'
$artifact = 'D:\PersonalHealthEngine\artifacts\PHE-Android-production.apk'
$artifactsDir = 'D:\PersonalHealthEngine\artifacts'

# ---- 版本号：日期 + 当日第几次构建（2026083001 < 21 亿上限）----
$versionFile = Join-Path $artifactsDir 'version_code.txt'
New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null
$today = Get-Date -Format 'yyyyMMdd'
$lastCode = if (Test-Path $versionFile) { Get-Content $versionFile -Raw } else { '' }
$buildNumber = if ($lastCode -and $lastCode.StartsWith($today)) {
    [int]$lastCode + 1
} else {
    [int]"$today" * 10 + 1
}
Set-Content -Path $versionFile -Value $buildNumber -NoNewline
$buildName = "1.1.$buildNumber"
Write-Output "build: versionName=$buildName versionCode=$buildNumber"

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
            "--build-name=$buildName" `
            "--build-number=$buildNumber" `
            "--dart-define-from-file=$definesFile"
        if ($LASTEXITCODE -ne 0) { throw 'Flutter release build failed' }
    } finally {
        Pop-Location
    }

    $builtApk = Join-Path $appRoot 'build\app\outputs\flutter-apk\app-release.apk'
    Copy-Item -LiteralPath $builtApk -Destination $artifact -Force

    # ---- 上传到服务器（应用内自更新源）----
    $keyPath = "$env:USERPROFILE\.ssh\phe_vps"
    $server = 'root@47.111.229.39'
    $remoteApk = "PHE-$buildName.apk"
    & scp -i $keyPath -o BatchMode=yes $artifact "${server}:/srv/phe/app-releases/$remoteApk"
    if ($LASTEXITCODE -ne 0) { throw 'APK upload to server failed' }

    $apkHash = (Get-FileHash -Algorithm SHA256 $artifact).Hash.ToLower()
    $latest = @{
        available = $true
        version_code = [string]$buildNumber
        version_name = $buildName
        file = $remoteApk
        url = '/app/download'
        sha256 = $apkHash
        published = (Get-Date -Format 'yyyy-MM-dd HH:mm')
    } | ConvertTo-Json
    $latestFile = Join-Path $artifactsDir 'latest.json'
    Set-Utf8NoBom $latestFile $latest
    & scp -i $keyPath -o BatchMode=yes $latestFile "${server}:/srv/phe/app-releases/latest.json"
    if ($LASTEXITCODE -ne 0) { throw 'latest.json upload to server failed' }

    Write-Output "published: $remoteApk (sha256 $apkHash)"
    Write-Output $artifact
} finally {
    Remove-Item -LiteralPath $keyProperties -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $definesFile -Force -ErrorAction SilentlyContinue
    $apiToken = $null
    $signingPassword = $null
}
