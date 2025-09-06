$required = @("src/api", "src/core", "config/exchanges")
$missing = $required | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "Missing directories: $($missing -join ', ')" }