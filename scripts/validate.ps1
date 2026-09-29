$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

py -3 -m compileall -q server\panel
node --check server\panel\static\panel.js
node --check server\panel\static\happ-actions.js

$scanFiles = Get-ChildItem -Recurse -File server,docs | Where-Object { $_.FullName -notlike '*scripts*validate.ps1' }
$forbidden = $scanFiles | Select-String -Pattern '8b2f92bc|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|"(client_secret|password)"\s*:\s*"(?!<[^>]+>)[^"]+"' -CaseSensitive:$false
if ($forbidden) {
    throw 'Potential secret-like value found in publication files.'
}

Write-Host 'FocusVPN publication checks passed.'
