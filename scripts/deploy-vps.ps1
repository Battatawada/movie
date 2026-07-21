# Deploy Retro Movie Archive to Oracle VPS (from Windows)

param(
    [string]$Key = "ssh-key-2026-06-24.key",
    [string]$SshHost = "ubuntu@140.245.245.123",
    [string]$RemoteRoot = "/opt/retro-movies"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$KeyPath = Join-Path $Root $Key
if (-not (Test-Path $KeyPath)) {
    Write-Error "Missing SSH key: $KeyPath"
}

Write-Host "==> Sync to VPS /tmp/retro-deploy/"
ssh -i $KeyPath $SshHost "rm -rf /tmp/retro-deploy && mkdir -p /tmp/retro-deploy"
scp -i $KeyPath -r vps deploy scripts config/pipeline.json "${SshHost}:/tmp/retro-deploy/"
if (Test-Path "config/bg_music") {
    scp -i $KeyPath -r config/bg_music "${SshHost}:/tmp/retro-deploy/"
}

$installCmd = @(
    "sudo mkdir -p $RemoteRoot/vps $RemoteRoot/deploy $RemoteRoot/scripts $RemoteRoot/config/bg_music $RemoteRoot/runs /opt/movies",
    "sudo cp -r /tmp/retro-deploy/vps/* $RemoteRoot/vps/",
    "sudo cp -r /tmp/retro-deploy/deploy/* $RemoteRoot/deploy/",
    "sudo cp -r /tmp/retro-deploy/scripts/* $RemoteRoot/scripts/",
    "sudo cp /tmp/retro-deploy/pipeline.json $RemoteRoot/config/",
    "sudo cp -r /tmp/retro-deploy/bg_music/* $RemoteRoot/config/bg_music/ 2>/dev/null || true",
    "sudo chown -R niche:niche $RemoteRoot /opt/movies",
    "test -d $RemoteRoot/.venv || sudo -u niche python3 -m venv $RemoteRoot/.venv",
    "sudo -u niche $RemoteRoot/.venv/bin/pip install -U pip -q",
    "sudo -u niche $RemoteRoot/.venv/bin/pip install -r $RemoteRoot/vps/requirements.txt -q"
) -join " && "

Write-Host "==> Install on VPS"
ssh -i $KeyPath $SshHost $installCmd

Write-Host "==> Install systemd service"
$existingSecret = ssh -i $KeyPath $SshHost "sudo grep -E '^WEBHOOK_SECRET=' $RemoteRoot/.env 2>/dev/null | head -n1 | cut -d= -f2-"
if ($existingSecret) {
    ssh -i $KeyPath $SshHost "sudo WEBHOOK_SECRET='$existingSecret' bash $RemoteRoot/scripts/vps-install-service.sh"
} else {
    ssh -i $KeyPath $SshHost "sudo bash $RemoteRoot/scripts/vps-install-service.sh"
}

Write-Host "==> Health"
ssh -i $KeyPath $SshHost "curl -sf http://127.0.0.1:8766/health; echo"
