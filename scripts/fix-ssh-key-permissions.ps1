# Fix OpenSSH "bad permissions" on Windows for Oracle VPS key
$key = Join-Path (Split-Path $PSScriptRoot -Parent) "ssh-key-2026-06-24.key"
if (-not (Test-Path $key)) { Write-Error "Missing $key" }
icacls $key /inheritance:r
icacls $key /grant:r "$($env:USERNAME):(R)"
Write-Host "Fixed permissions on $key"
