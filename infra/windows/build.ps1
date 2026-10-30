param([switch]$BundleOnly, [switch]$SkipImages, [switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $taskRoot
$taskConfig = Get-Content -Raw -LiteralPath "$PSScriptRoot/src-tauri/tauri.conf.json" | ConvertFrom-Json
$taskVersion = $taskConfig.version
$taskOut = Join-Path $taskRoot 'var/windows'
New-Item -ItemType Directory -Force -Path $taskOut | Out-Null
function Invoke-BuildTool([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}
Invoke-BuildTool pnpm @('--dir', 'infra/windows', 'install', '--frozen-lockfile')
if (-not $SkipFrontend) {
    Invoke-BuildTool pnpm @('--dir', 'frontend', 'install', '--frozen-lockfile')
    Invoke-BuildTool pnpm @('--dir', 'frontend', 'build')
}
if (-not $SkipImages) {
    Invoke-BuildTool docker @('build', '--tag', "trustrepro-backend:desktop-build-$taskVersion", '--file', 'infra/docker/backend.Dockerfile', '.')
    Invoke-BuildTool docker @('build', '--tag', "trustrepro-backend:$taskVersion", '--build-arg', "BACKEND_IMAGE=trustrepro-backend:desktop-build-$taskVersion", '--file', 'infra/windows/desktop.Dockerfile', '.')
    Invoke-BuildTool docker @('build', '--tag', "trustrepro-sandbox:$taskVersion", '--file', 'infra/docker/sandbox.Dockerfile', '.')
    Invoke-BuildTool docker @('build', '--tag', "trustrepro-egress:$taskVersion", '--file', 'infra/docker/egress.Dockerfile', '.')
    Invoke-BuildTool docker @('build', '--tag', 'trustrepro-windows-runtime:1', '--file', 'infra/windows/runtime.Dockerfile', '.')
}
Invoke-BuildTool uv @('run', '--project', 'backend', 'python', 'infra/windows/build_bundle.py', '--version', $taskVersion)
Invoke-BuildTool pnpm @('--dir', 'infra/windows', 'exec', 'tauri', 'icon', 'icon.svg', '--output', '../../var/windows/icons')
Copy-Item -LiteralPath "$taskOut/icons/icon.ico" -Destination "$PSScriptRoot/src-tauri/icons/icon.ico" -Force
Copy-Item -LiteralPath "$taskOut/icons/32x32.png" -Destination "$PSScriptRoot/src-tauri/icons/32x32.png" -Force
if (-not $BundleOnly) {
    if (-not (Test-Path -LiteralPath "$taskOut/MicrosoftEdgeWebView2RuntimeInstallerX64.exe")) {
        Invoke-BuildTool uv @('run', '--project', 'backend', 'python', 'infra/windows/fetch_webview.py')
    }
    $taskWebviewSignature = Get-AuthenticodeSignature -LiteralPath "$taskOut/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    if ($taskWebviewSignature.Status -ne 'Valid' -or $taskWebviewSignature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
        throw 'Microsoft WebView2 signature verification failed'
    }
    Invoke-BuildTool pnpm @('--dir', 'infra/windows', 'build')
    New-Item -ItemType Directory -Force -Path "$taskOut/installer" | Out-Null
    $taskInstallerName = "TrustRepro_${taskVersion}_x64-setup.exe"
    $taskInstallerSource = Join-Path $PSScriptRoot "src-tauri/target/release/bundle/nsis/$taskInstallerName"
    if ((Get-Item -LiteralPath $taskInstallerSource).Length -eq 0) { throw 'The new installer is empty.' }
    $taskPendingInstaller = Join-Path $taskOut "installer/$taskInstallerName.pending"
    Copy-Item -LiteralPath $taskInstallerSource -Destination $taskPendingInstaller -Force
    $taskInstallerHash = (Get-FileHash -LiteralPath $taskPendingInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    Move-Item -LiteralPath $taskPendingInstaller -Destination "$taskOut/installer/$taskInstallerName" -Force
    [IO.File]::WriteAllText("$taskOut/installer/SHA256SUMS", "$taskInstallerHash  $taskInstallerName`n", [Text.UTF8Encoding]::new($false))
    # Never clean after a failed build or a resource-only build.
    & "$PSScriptRoot/clean-build.ps1" -Version $taskVersion
}
