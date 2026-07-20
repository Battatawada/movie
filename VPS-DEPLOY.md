# VPS deploy — Retro Movie Archive

Same Oracle VPS as Niche/Crime. **Port 8766** (crime FlowKit uses 8765).

## SSH (from Windows)

```powershell
cd "C:\Users\Pracheer\Music\Retro Movie Archive"
ssh -i "ssh-key-2026-06-24.key" ubuntu@140.245.245.123
```

## First-time VPS setup

```powershell
# 1. Copy repo to VPS (or git clone on VPS)
.\scripts\deploy-vps.ps1

# 2. On VPS as root — full bootstrap
sudo bash /opt/retro-movies/scripts/vps-setup.sh
sudo bash /opt/retro-movies/scripts/vps-install-service.sh
sudo bash /opt/retro-movies/scripts/vps-preflight.sh
```

## Movie library

```bash
sudo mkdir -p /opt/movies/inception-2010
# upload movie.mp4 + subtitles.srt
sudo chown -R niche:niche /opt/movies
```

## GitHub secrets

| Secret | Value |
|--------|--------|
| `VPS_WEBHOOK_URL` | `http://140.245.245.123:8766` |
| `VPS_WEBHOOK_SECRET` | from `vps-install-service.sh` output |
| `NOTEBOOKLM_AUTH_JSON` | `.\scripts\export_notebooklm_secret.ps1` |

## Quick health

```bash
curl http://140.245.245.123:8766/health
```
