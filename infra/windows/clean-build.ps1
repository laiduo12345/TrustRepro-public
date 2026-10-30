[CmdletBinding(SupportsShouldProcess)]
param([string]$Version)

$ErrorActionPreference = 'Stop'

function Assert-BuildPath([string]$Root, [string]$Path) {
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($rootPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup path is outside the workspace: $fullPath"
    }
    # Reject junctions and symbolic links in every ancestor, including the root.
    $ancestor = $fullPath
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Cleanup refuses a reparse point: $ancestor"
            }
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    return $fullPath
}

function Measure-BuildCleanupPath([string]$Root, [string]$Path) {
    $fullPath = Assert-BuildPath $Root $Path
    if (-not (Test-Path -LiteralPath $fullPath)) { return [long]0 }
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($fullPath)
    [long]$bytes = 0
    while ($queue.Count) {
        $item = Get-Item -LiteralPath $queue.Dequeue() -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Cleanup refuses a nested reparse point: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
                $queue.Enqueue($child.FullName)
            }
        } else {
            $bytes += $item.Length
        }
    }
    return $bytes
}

function Remove-OldDesktopImages {
    [CmdletBinding(SupportsShouldProcess)]
    param([string]$Version)
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning 'Docker unavailable; image cleanup skipped.'
        return
    }
    $images = @(& docker image ls --format '{{.Repository}}:{{.Tag}}' --filter 'reference=trustrepro-*')
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Docker unavailable; image cleanup skipped.'
        return
    }
    $removed = 0
    foreach ($image in $images) {
        if ($image -notmatch '^trustrepro-(backend|sandbox|egress):(?<intermediate>desktop-build-)?(?<version>\d+\.\d+\.\d+)$') { continue }
        $imageVersion = [version]$Matches.version
        if ($imageVersion -gt [version]$Version) { continue }
        if ($imageVersion -eq [version]$Version -and -not $Matches.intermediate) { continue }
        # Includes stopped containers and descendants; never force-delete images.
        $users = @(& docker ps --all --quiet --filter "ancestor=$image")
        if ($LASTEXITCODE -ne 0) { throw 'Cannot check image ownership and usage.' }
        if ($users.Count) { continue }
        if ($PSCmdlet.ShouldProcess($image, 'Remove unused TrustRepro desktop build image')) {
            & docker image rm $image | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Image cleanup skipped: $image"
            } else { $removed++ }
        }
    }
    Write-Host "Removed $removed unused desktop image tags. Shared Docker build cache retained."
}

function Invoke-DesktopBuildCleanup {
    [CmdletBinding(SupportsShouldProcess)]
    param([string]$Root, [string]$Version, [switch]$SkipDocker)
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a stable desktop version.' }
    $rootPath = [IO.Path]::GetFullPath($Root)
    $installerDir = Assert-BuildPath $rootPath (Join-Path $rootPath 'var/windows/installer')
    $name = "TrustRepro_${Version}_x64-setup.exe"
    $package = Assert-BuildPath $rootPath (Join-Path $installerDir $name)
    $sums = Assert-BuildPath $rootPath (Join-Path $installerDir 'SHA256SUMS')
    if (-not (Test-Path -LiteralPath $package -PathType Leaf) -or (Get-Item -LiteralPath $package).Length -eq 0) {
        throw 'A completed new installer is required before cleanup.'
    }
    # The build writes this only after copying and hashing the finished package.
    $checksum = @(Get-Content -LiteralPath $sums | Where-Object { $_ -match ('^[a-fA-F0-9]{64}  ' + [regex]::Escape($name) + '$') })
    if ($checksum.Count -ne 1) { throw 'The completed installer must have one SHA256SUMS entry.' }

    $targets = [Collections.Generic.List[string]]::new()
    foreach ($item in Get-ChildItem -LiteralPath $installerDir -File -Force) {
        if ($item.Name -match '^TrustRepro_(\d+\.\d+\.\d+)_x64-setup\.exe(?:\.sha256)?$') {
            $itemVersion = [version]$Matches[1]
            if ($itemVersion -gt [version]$Version) { throw "A newer installer exists: $($item.Name)" }
            if ($itemVersion -lt [version]$Version) { $targets.Add($item.FullName) }
        }
    }
    # Explicit build outputs only: no app data, test fixtures, venvs or global caches.
    foreach ($relative in @(
        'infra/windows/src-tauri/target', 'infra/windows/src-tauri/gen',
        'var/windows/bundle', 'var/windows/release', 'var/windows/icons',
        'var/windows/webview-parts', 'var/windows/webview-range.bin',
        'var/windows/webview.headers', 'var/windows/webview-source.json',
        'var/windows/MicrosoftEdgeWebView2RuntimeInstallerX64.exe',
        'var/windows/MicrosoftEdgeWebView2RuntimeInstallerX64.pending',
        'var/windows/runtime.tar', 'var/windows/__pycache__',
        'infra/windows/__pycache__', 'infra/windows/runtime/__pycache__',
        'frontend/node_modules/.vite', 'backend/.pytest_cache', 'backend/.ruff_cache',
        '.pytest_cache', '.ruff_cache'
    )) { $targets.Add((Join-Path $rootPath $relative)) }

    # Preflight the complete allowlist before the first deletion. Do not follow links.
    $plan = foreach ($target in $targets) {
        [pscustomobject]@{ Path = (Assert-BuildPath $rootPath $target); Bytes = (Measure-BuildCleanupPath $rootPath $target) }
    }
    [long]$removedBytes = 0
    foreach ($entry in $plan) {
        if ((Test-Path -LiteralPath $entry.Path) -and $PSCmdlet.ShouldProcess($entry.Path, 'Remove obsolete installer or build cache')) {
            $null = Assert-BuildPath $rootPath $entry.Path
            Remove-Item -LiteralPath $entry.Path -Recurse -Force -ErrorAction Stop
            $removedBytes += $entry.Bytes
        }
    }
    if ($PSCmdlet.ShouldProcess($sums, 'Keep the current installer checksum')) {
        [IO.File]::WriteAllText($sums, $checksum[0] + "`n", [Text.UTF8Encoding]::new($false))
    }
    if (-not $SkipDocker) { Remove-OldDesktopImages -Version $Version -WhatIf:$WhatIfPreference }
    Write-Host ('Cleaned {0:N2} GiB of workspace files; retained {1}.' -f ($removedBytes / 1GB), $name)
    return [pscustomobject]@{ RemovedBytes = $removedBytes; Installer = $package }
}

if ($MyInvocation.InvocationName -ne '.') {
    $rootPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if (-not $Version) {
        $Version = (Get-Content -Raw -LiteralPath "$PSScriptRoot/src-tauri/tauri.conf.json" | ConvertFrom-Json).version
    }
    Invoke-DesktopBuildCleanup -Root $rootPath -Version $Version -WhatIf:$WhatIfPreference
}
