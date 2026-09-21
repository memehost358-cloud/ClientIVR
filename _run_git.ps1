$ErrorActionPreference = "Continue"
$Log = Join-Path $PSScriptRoot "_git_output.log"
Set-Location $PSScriptRoot

"=== 1. git status --porcelain ===" | Set-Content $Log -Encoding UTF8
git status --porcelain 2>&1 | Add-Content $Log -Encoding UTF8

"=== 2. git diff --name-only HEAD ===" | Add-Content $Log -Encoding UTF8
git diff --name-only HEAD 2>&1 | Add-Content $Log -Encoding UTF8

"=== 3. git add -A ===" | Add-Content $Log -Encoding UTF8
git add -A 2>&1 | Add-Content $Log -Encoding UTF8

"=== 4. git status --short ===" | Add-Content $Log -Encoding UTF8
git status --short 2>&1 | Add-Content $Log -Encoding UTF8

"=== 5. git commit ===" | Add-Content $Log -Encoding UTF8
git commit -m "Fix provider validation: PROV_CALLERID_* is now OPTIONAL (only PROV_ENDPOINT_* required); Asterisk trunk default CLI used when unset" --allow-empty-message --allow-empty 2>&1 | Add-Content $Log -Encoding UTF8

"=== 6. git push origin main ===" | Add-Content $Log -Encoding UTF8
git push origin main 2>&1 | Add-Content $Log -Encoding UTF8

"DONE" | Add-Content $Log -Encoding UTF8
Write-Host "Log written to $Log"
Get-Content $Log
