<#
.SYNOPSIS
Production smoke-check for fis-lead-gen.

.DESCRIPTION
Hits the prod frontend's BFF proxy and key user-facing routes,
verifies expected HTTP codes + key page markers. Designed to
run in <30 seconds after every deploy. Exits non-zero on any
failure so it's safe to wire into CI or a Cloud Scheduler job.

Two modes:

  Anonymous (default): hits public routes, asserts non-5xx,
  asserts /login + /signup markers, and runs NEGATIVE tests
  to confirm the auth gate redirects unauthenticated callers
  on /dashboard, /master-list, /alerts, /export, /settings/*.

  Authenticated (-AdminCookie ...): also hits each protected
  route with the supplied session cookie and verifies that the
  rendered HTML contains the expected page-title marker. This
  catches regressions where a page returns 200 but fails to
  mount its React tree (the MVP version missed those because
  unauthenticated calls always 307 to /login regardless).

.PARAMETER AdminCookie
Optional. Full Cookie header value for an admin session, e.g.
"__Secure-better-auth.session_token=eyJhbGciOi...". When set,
authenticated checks run with this cookie attached. When unset,
only anonymous checks run.

.NOTES
Requires PowerShell 7+ (pwsh) for -SkipHttpErrorCheck.

.EXAMPLE
./scripts/ops/smoke-prod.ps1

.EXAMPLE
./scripts/ops/smoke-prod.ps1 -AdminCookie "__Secure-better-auth.session_token=eyJhbGciOi..."
#>

param(
    [string]$AdminCookie = $null
)

$ErrorActionPreference = "Stop"
$BASE = "https://fis.alchemydev.io"
$FAILURES = 0
$CHECKS = 0
$START_TIME = Get-Date

function Test-Endpoint {
    param(
        [string]$Path,
        [int]$ExpectedStatus = 200,
        [string]$Method = "GET",
        [string]$ContainsText = $null,
        [string]$ExpectedLocation = $null,
        [bool]$UseAuth = $false,
        [bool]$NoRedirect = $false,
        [string]$Description
    )

    $script:CHECKS++
    $url = "$BASE$Path"
    $start = Get-Date

    # Build Invoke-WebRequest args via splatting so we only attach
    # MaximumRedirection / Headers when actually needed.
    $invokeArgs = @{
        Uri = $url
        Method = $Method
        UseBasicParsing = $true
        SkipHttpErrorCheck = $true
        TimeoutSec = 30
    }
    if ($NoRedirect) {
        # Capture the redirect status directly instead of following it.
        # Used by NEGATIVE auth-gate tests that assert 307->/login.
        $invokeArgs.MaximumRedirection = 0
    }
    if ($UseAuth -and $script:AdminCookie) {
        $invokeArgs.Headers = @{ "Cookie" = "$script:AdminCookie" }
    }

    try {
        $response = Invoke-WebRequest @invokeArgs
        $duration = ((Get-Date) - $start).TotalMilliseconds
        $statusOK = $response.StatusCode -eq $ExpectedStatus
        $textOK = $true
        $locOK = $true
        if ($ContainsText) {
            $textOK = $response.Content -like "*$ContainsText*"
        }
        if ($ExpectedLocation) {
            # Headers.Location can be a string or List[string] depending
            # on pwsh version — normalize before string compare.
            $actualLoc = $response.Headers.Location
            if ($actualLoc -is [array]) { $actualLoc = $actualLoc[0] }
            $locOK = "$actualLoc" -like "*$ExpectedLocation*"
        }
        if ($statusOK -and $textOK -and $locOK) {
            Write-Host (" OK   {0,-50} {1} {2}ms" -f $Description, $response.StatusCode, [math]::Round($duration)) -ForegroundColor Green
        } else {
            $script:FAILURES++
            $reason = if (-not $statusOK) {
                "status $($response.StatusCode), expected $ExpectedStatus"
            } elseif (-not $textOK) {
                "missing text '$ContainsText'"
            } else {
                "Location '$($response.Headers.Location)', expected '*$ExpectedLocation*'"
            }
            Write-Host (" FAIL {0,-50} {1}" -f $Description, $reason) -ForegroundColor Red
        }
    } catch {
        $script:FAILURES++
        Write-Host (" FAIL {0,-50} {1}" -f $Description, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "Smoke-checking $BASE ..." -ForegroundColor Cyan
if ($AdminCookie) {
    Write-Host "Mode: authenticated (admin cookie attached on protected routes)" -ForegroundColor Cyan
} else {
    Write-Host "Mode: anonymous (no admin cookie; auth-gate negative tests only)" -ForegroundColor Cyan
}
Write-Host ""

# --- Public health endpoint (via BFF proxy, no auth) ---
Test-Endpoint -Path "/api/backend/api/v1/health" -Description "BFF proxy health"

# --- Public-facing pages (HTML, look for distinctive text per page) ---
Test-Endpoint -Path "/login" -ContainsText "Sign in" -Description "/login renders"
Test-Endpoint -Path "/signup" -ContainsText "Sign up" -Description "/signup renders"
Test-Endpoint -Path "/pending-approval" -Description "/pending-approval reachable"

# --- Admin-gate NEGATIVE tests (always run, anonymous only) ---
# Middleware + the (app) layout's getRequiredSession() both 307 to /login
# when no session cookie is present. Catches regressions where the gate
# is accidentally removed and an unauthenticated caller reaches the page.
Test-Endpoint -Path "/dashboard" -ExpectedStatus 307 -ExpectedLocation "/login" -NoRedirect $true -Description "/dashboard blocks anon (307->/login)"
Test-Endpoint -Path "/master-list" -ExpectedStatus 307 -ExpectedLocation "/login" -NoRedirect $true -Description "/master-list blocks anon (307->/login)"
Test-Endpoint -Path "/alerts" -ExpectedStatus 307 -ExpectedLocation "/login" -NoRedirect $true -Description "/alerts blocks anon (307->/login)"
Test-Endpoint -Path "/settings/users" -ExpectedStatus 307 -ExpectedLocation "/login" -NoRedirect $true -Description "/settings/users blocks anon (307->/login)"
Test-Endpoint -Path "/settings/pipelines" -ExpectedStatus 307 -ExpectedLocation "/login" -NoRedirect $true -Description "/settings/pipelines blocks anon (307->/login)"

# --- Authenticated routes return redirect or 200 (depending on session)
# NOTE: these will redirect to /login if no session cookie. They should
# NEVER return 500. If they do, that's the incident class we want to catch.
# When -AdminCookie is supplied, additionally assert the page-title
# marker so we catch React-mount failures (page returns 200 but the
# tree never hydrates) or copy regressions.
if ($AdminCookie) {
    Test-Endpoint -Path "/dashboard" -ContainsText "Lead Intelligence Workspace" -UseAuth $true -Description "/dashboard renders for admin"
    Test-Endpoint -Path "/master-list" -ContainsText "Broker-Dealer Master List" -UseAuth $true -Description "/master-list renders for admin"
    Test-Endpoint -Path "/alerts" -ContainsText "Daily filing monitor" -UseAuth $true -Description "/alerts renders for admin"
    Test-Endpoint -Path "/my-favorites" -ContainsText "Saved firms" -UseAuth $true -Description "/my-favorites renders for admin"
    Test-Endpoint -Path "/visited-firms" -ContainsText "Visited Firms" -UseAuth $true -Description "/visited-firms renders for admin"
    Test-Endpoint -Path "/email-extractor" -ContainsText "Domain email discovery" -UseAuth $true -Description "/email-extractor renders for admin"
    Test-Endpoint -Path "/export" -ContainsText "Restricted CSV export" -UseAuth $true -Description "/export renders for admin"
    Test-Endpoint -Path "/settings/users" -ContainsText "User approvals" -UseAuth $true -Description "/settings/users renders for admin"
    Test-Endpoint -Path "/settings/pipelines" -ContainsText "Filing Monitor" -UseAuth $true -Description "/settings/pipelines renders for admin"
} else {
    Test-Endpoint -Path "/my-favorites" -Description "/my-favorites reachable (no 5xx)"
    Test-Endpoint -Path "/visited-firms" -Description "/visited-firms reachable (no 5xx)"
    Test-Endpoint -Path "/email-extractor" -Description "/email-extractor reachable (no 5xx)"
    Test-Endpoint -Path "/export" -Description "/export reachable (no 5xx)"
}

# --- Firm-detail page — the one that 500'd today during the user_favorite drop
# Hit a known-good firm ID. If this 500s, regression caught. The detail
# page is fully client-rendered, so no static-HTML marker check applies.
if ($AdminCookie) {
    Test-Endpoint -Path "/master-list/17759" -UseAuth $true -Description "/master-list/17759 (regression watch)"
} else {
    Test-Endpoint -Path "/master-list/17759" -Description "/master-list/17759 (regression watch)"
}

# --- Summary ---
$elapsed = ((Get-Date) - $START_TIME).TotalSeconds
$timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
$passed = $CHECKS - $FAILURES
Write-Host ""
Write-Host ("Summary: {0} checks, {1} passed, {2} failed in {3}s at {4}" -f $CHECKS, $passed, $FAILURES, [math]::Round($elapsed, 1), $timestamp)
if ($FAILURES -eq 0) {
    Write-Host "Smoke OK." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Smoke FAILED." -ForegroundColor Red
    exit 1
}
