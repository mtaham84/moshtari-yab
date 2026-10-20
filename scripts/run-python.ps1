$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PYTHONPYCACHEPREFIX = Join-Path $repoRoot ".cache/python"

Push-Location $repoRoot
try {
    & python @args
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $exitCode
