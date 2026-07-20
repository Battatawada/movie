# Refresh GitHub secret from local NotebookLM storage_state.json (Retro Movie Archive)
# Prerequisite: notebooklm -p retro auth check --test must pass

$ErrorActionPreference = "Stop"
$Repo = if ($env:RETRO_GH_REPO) { $env:RETRO_GH_REPO } else { "Battatawada/movie" }
$Profile = if ($env:NOTEBOOKLM_PROFILE) { $env:NOTEBOOKLM_PROFILE } else { "retro" }
$storage = Join-Path $env:USERPROFILE ".notebooklm\profiles\$Profile\storage_state.json"

if (-not (Test-Path $storage)) {
    Write-Error "Not found: $storage. Run: notebooklm -p $Profile login"
}

Write-Host "Checking local auth (profile=$Profile)..."
notebooklm -p $Profile auth check --test
if ($LASTEXITCODE -ne 0) {
    Write-Error "Auth check failed. Re-login: notebooklm -p $Profile login"
}

Write-Host "Simulating CI (NOTEBOOKLM_AUTH_JSON env)..."
$json = Get-Content $storage -Raw
$env:NOTEBOOKLM_AUTH_JSON = $json
$env:NOTEBOOKLM_PROFILE = $Profile
notebooklm auth check --test --json | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "CI simulation failed - refresh login before pushing secret."
}
Remove-Item Env:NOTEBOOKLM_AUTH_JSON -ErrorAction SilentlyContinue

Write-Host "Checking GitHub CLI access to $Repo secrets..."
gh auth status 2>&1 | Write-Host
$whoami = (gh api user -q .login 2>$null)
if (-not $whoami) {
    Write-Error "gh not logged in. Run: gh auth login"
}
Write-Host "Logged in as: $whoami"

gh api "repos/$Repo/actions/secrets/public-key" -q .key_id 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Cannot write secrets to $Repo yet (repo may not exist). Copy JSON manually to GitHub Secrets."
    Write-Host "Storage file: $storage"
    exit 0
}

Write-Host "Updating NOTEBOOKLM_AUTH_JSON on $Repo..."
$json | gh secret set NOTEBOOKLM_AUTH_JSON --repo $Repo
if ($LASTEXITCODE -ne 0) {
    Write-Error "gh secret set failed."
}

Write-Host "Done. Retro NotebookLM secret updated on $Repo (profile=$Profile)."
