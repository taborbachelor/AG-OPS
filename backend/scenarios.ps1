# M4 SITL scenario runner — named entry points per ARCHITECTURE.md ("sim:link-loss" etc).
# Usage (from backend\, venv installed):
#   .\scenarios.ps1 field-test      # the maiden-flight dress rehearsal
#   .\scenarios.ps1 link-loss
#   .\scenarios.ps1 gps-failure
#   .\scenarios.ps1 battery-fault
#   .\scenarios.ps1 rtl-recovery
#   .\scenarios.ps1 link-watchdog
#   .\scenarios.ps1 ekf-variance    # guardian EKF-variance monitor, live
#   .\scenarios.ps1 airspeed-stall  # guardian stall-margin monitor, live
#   .\scenarios.ps1 bank-angle      # guardian bank-angle monitor, live
#   .\scenarios.ps1 keepout-prox    # live keepout/hazard proximity
#   .\scenarios.ps1 linkless        # fence + rally enforced with the link cut
#   .\scenarios.ps1 terrain         # terrain following at spray alt, link cut
#   .\scenarios.ps1 all             # full scenario suite (~10-20 min)
# Each scenario spawns the bundled SITL itself — close any hand-started SITL first.
#
# -Instance <n> runs against SITL instance n (ports offset by 10*n) so a second
# session can run the suite concurrently instead of silently fighting over 5760:
#   .\scenarios.ps1 all -Instance 1
# Without it the suite uses instance 0 / port 5760, exactly as it always has.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("field-test", "link-loss", "gps-failure", "battery-fault",
                 "rtl-recovery", "link-watchdog", "guardian", "preflight",
                 "ekf-variance", "airspeed-stall", "bank-angle",
                 "keepout-prox", "linkless", "terrain",
                 "bench", "soak", "all")]
    [string]$Scenario,

    [ValidateRange(0, 9)]
    [int]$Instance
)

# Only override the environment when actually asked, so an instance exported by
# the surrounding shell keeps working.
if ($PSBoundParameters.ContainsKey('Instance')) {
    $env:SITL_INSTANCE = $Instance
}
if ($env:SITL_INSTANCE) {
    Write-Host "SITL instance $($env:SITL_INSTANCE) (TCP $(5760 + 10 * [int]$env:SITL_INSTANCE))" -ForegroundColor Cyan
}

$map = @{
    "field-test"    = "tests\sitl\test_scenario_field_test.py"
    "link-loss"     = "tests\sitl\test_scenario_link_loss.py"
    "gps-failure"   = "tests\sitl\test_scenario_gps_failure.py"
    "battery-fault" = "tests\sitl\test_scenario_battery_fault.py"
    "rtl-recovery"  = "tests\sitl\test_scenario_rtl_recovery.py"
    "link-watchdog" = "tests\sitl\test_scenario_link_watchdog.py"
    "guardian"      = "tests\sitl\test_scenario_guardian.py"
    "ekf-variance"   = "tests\sitl\test_scenario_ekf_variance.py"
    "airspeed-stall" = "tests\sitl\test_scenario_airspeed_stall.py"
    "bank-angle"     = "tests\sitl\test_scenario_bank_angle.py"
    "keepout-prox"   = "tests\sitl\test_scenario_keepout_proximity.py"
    "linkless"       = "tests\sitl\test_scenario_linkless.py"
    "terrain"        = "tests\sitl\test_scenario_terrain.py"
    "preflight"     = "tests\sitl\test_scenario_preflight.py"
    "bench"         = "tests\sitl\test_scenario_bench.py"
    "soak"          = "tests\sitl\test_scenario_soak.py"
}

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# `-m sitl` (last -m wins) overrides pytest.ini's default "not sitl" filter.
if ($Scenario -eq "all") {
    & $python -m pytest -m sitl tests\sitl -v
} else {
    & $python -m pytest -m sitl $map[$Scenario] -v
}
exit $LASTEXITCODE
