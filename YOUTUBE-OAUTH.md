# YouTube OAuth — Retro Movie Archive

Same Google Cloud OAuth **Desktop** client as Niche/Psychology/Crime.  
Mint a **new** refresh token for the Retro Movie Archive YouTube channel account.

## 1. One-time local auth

```powershell
cd "C:\Users\Pracheer\Music\Retro Movie Archive"
pip install google-auth-oauthlib
python scripts/youtube_oauth_refresh.py
```

Browser opens → sign in as your **Retro Movie Archive** Google account → Allow.

Copy `YOUTUBE_REFRESH_TOKEN` and client id/secret from `client_secret_*.json`.

## 2. GitHub secrets

```powershell
$Repo = "Battatawada/movie"
"CLIENT_ID" | gh secret set YOUTUBE_CLIENT_ID --repo $Repo
"CLIENT_SECRET" | gh secret set YOUTUBE_CLIENT_SECRET --repo $Repo
"REFRESH_TOKEN" | gh secret set YOUTUBE_REFRESH_TOKEN --repo $Repo
```

## 3. GCP test user

Add the Retro channel Gmail as a **test user** on the OAuth consent screen (same GCP project as Niche).
