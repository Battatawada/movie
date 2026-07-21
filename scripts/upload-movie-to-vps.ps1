# Upload movie.mp4 + subtitles.srt to VPS, then delete local source folder.
#
# Usage:
#   .\scripts\upload-movie-to-vps.ps1 -Slug magnolia-1999 -SourceDir "C:\Users\Pracheer\Downloads\Magnolia 1999"
#   .\scripts\upload-movie-to-vps.ps1 -Slug magnolia-1999 -SourceDir "..." -KeepSource

param(
    [Parameter(Mandatory = $true)]
    [string]$Slug,

    [Parameter(Mandatory = $true)]
    [string]$SourceDir,

    [string]$Key = "ssh-key-2026-06-24.key",
    [string]$SshHost = "ubuntu@140.245.245.123",
    [switch]$KeepSource
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$KeyPath = Join-Path $Root $Key
if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Error "Missing SSH key: $KeyPath"
}

if (-not (Test-Path -LiteralPath $SourceDir)) {
    Write-Error "Source folder not found: $SourceDir"
}

$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$mp4 = Get-ChildItem -LiteralPath $SourceDir -Filter *.mp4 | Select-Object -First 1
$srt = Get-ChildItem -LiteralPath $SourceDir -Filter *.srt | Select-Object -First 1

if (-not $mp4) { Write-Error "No .mp4 file in $SourceDir" }
if (-not $srt) { Write-Error "No .srt file in $SourceDir" }

$remoteMoviesDir = "/opt/movies/$Slug"
$log = Join-Path $Root "upload-$Slug.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    Write-Host $line
    $line | Out-File -FilePath $log -Append -Encoding utf8
}

"=== Upload started: $Slug ===" | Out-File -FilePath $log -Encoding utf8
Write-Log "Source: $SourceDir"
Write-Log "Video:  $($mp4.Name) ($([math]::Round($mp4.Length / 1GB, 2)) GB)"
Write-Log "SRT:    $($srt.Name)"

Write-Log "Creating VPS folder $remoteMoviesDir"
ssh -i $KeyPath $SshHost "sudo mkdir -p $remoteMoviesDir"
if ($LASTEXITCODE -ne 0) { throw "Failed to create VPS folder (exit $LASTEXITCODE)" }

Write-Log "Uploading subtitles..."
scp -i $KeyPath $srt.FullName "${SshHost}:/tmp/subtitles.srt"
if ($LASTEXITCODE -ne 0) { throw "SRT upload failed (exit $LASTEXITCODE)" }

Write-Log "Uploading video (this may take a while)..."
scp -i $KeyPath $mp4.FullName "${SshHost}:/tmp/movie.mp4"
if ($LASTEXITCODE -ne 0) { throw "Video upload failed (exit $LASTEXITCODE)" }

Write-Log "Installing on VPS..."
$installCmd = @(
    "sudo mv /tmp/movie.mp4 $remoteMoviesDir/movie.mp4",
    "sudo mv /tmp/subtitles.srt $remoteMoviesDir/subtitles.srt",
    "sudo chown -R niche:niche $remoteMoviesDir",
    "ls -lh $remoteMoviesDir/"
) -join " && "

ssh -i $KeyPath $SshHost $installCmd
if ($LASTEXITCODE -ne 0) { throw "VPS install failed (exit $LASTEXITCODE)" }

Write-Log "=== Upload complete ==="

if (-not $KeepSource) {
    Write-Log "Deleting local source folder: $SourceDir"
    Remove-Item -LiteralPath $SourceDir -Recurse -Force
    Write-Log "Local source deleted"
} else {
    Write-Log "Keeping local source (-KeepSource)"
}
