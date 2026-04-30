<#
.SYNOPSIS
Production smoke-check for fis-lead-gen.

.DESCRIPTION
Hits the prod frontend's BFF proxy and key user-facing routes,
verifies expected HTTP codes + key page markers. Designed to
run in <30 seconds after every deploy. Exits non-zero on any
failure so it's safe to wire into CI or a Cloud Scheduler job.

.NOTES
Auth is via gcloud identity token — caller must be logged in
as a user with run.invoker on fis-backend OR an admin user
for the protected pages. Run from a PowerShell with
`gcloud auth login` already done.

.EXAMPLE
./scripts/ops/smoke-prod.ps1
#>

$ErrorActionPreference = "Stop"
$BASE = "https://fis.alchemydev.io"
$FAILURES = 0

function Test-Endpoint {
    param(
        [string]$Path,
        [int]$ExpectedStatus = 200,
        [string]$Method = "GET",
        [string]$ContainsText = $null,
        [string]$Description
    )

    $url = "$BASE$Path"
    $start = Get-Date
    try {
        $response = Invoke-WebRequest -Uri $url -Method $Method `
            -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 30
        $duration = ((Get-Date) - $start).TotalMilliseconds
        $statusOK = $response.StatusCode -eq $ExpectedStatus
        $textOK = $true
        if ($ContainsText) {
            $textOK = $response.Content -like "*$ContainsText*"
        }
        if ($statusOK -and $textOK) {
            Write-Host (" OK   {0,-50} {1} {2}ms" -f $Description, $response.StatusCode, [math]::Round($duration)) -ForegroundColor Green
        } else {
            $script:FAILURES++
            $reason = if (-not $statusOK) { "status $($response.StatusCode), expected $ExpectedStatus" } else { "missing text '$ContainsText'" }
            Write-Host (" FAIL {0,-50} {1}" -f $Description, $reason) -ForegroundColor Red
        }
    } catch {
        $script:FAILURES++
        Write-Host (" FAIL {0,-50} {1}" -f $Description, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "Smoke-checking $BASE ..." -ForegroundColor Cyan
Write-Host ""

# --- Public health endpoint (via BFF proxy, no auth) ---
Test-Endpoint -Path "/api/backend/api/v1/health" -Description "BFF proxy health"

# --- Public-facing pages (HTML, look for distinctive text per page) ---
Test-Endpoint -Path "/login" -ContainsText "Sign in" -Description "/login renders"
Test-Endpoint -Path "/signup" -ContainsText "Sign up" -Description "/signup renders"
Test-Endpoint -Path "/pending-approval" -Description "/pending-approval reachable"

# --- Authenticated routes return redirect or 200 (depending on session)
# NOTE: these will redirect to /login if no session cookie. They should
# NEVER return 500. If they do, that's the incident class we want to catch.
Test-Endpoint -Path "/dashboard" -Description "/dashboard reachable (no 5xx)"
Test-Endpoint -Path "/master-list" -Description "/master-list reachable (no 5xx)"
Test-Endpoint -Path "/alerts" -Description "/alerts reachable (no 5xx)"
Test-Endpoint -Path "/my-favorites" -Description "/my-favorites reachable (no 5xx)"
Test-Endpoint -Path "/visited-firms" -Description "/visited-firms reachable (no 5xx)"
Test-Endpoint -Path "/email-extractor" -Description "/email-extractor reachable (no 5xx)"
Test-Endpoint -Path "/export" -Description "/export reachable (no 5xx)"
Test-Endpoint -Path "/settings/users" -Description "/settings/users reachable (no 5xx)"
Test-Endpoint -Path "/settings/pipelines" -Description "/settings/pipelines reachable (no 5xx)"

# --- Firm-detail page — the one that 500'd today during the user_favorite drop
# Hit a known-good firm ID. If this 500s, regression caught.
Test-Endpoint -Path "/master-list/17759" -Description "/master-list/17759 (regression watch)"

Write-Host ""
if ($FAILURES -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$FAILURES check(s) failed." -ForegroundColor Red
    exit 1
}
