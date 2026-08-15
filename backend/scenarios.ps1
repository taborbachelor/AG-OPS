# M4 SITL scenario runner — named entry points per ARCHITECTURE.md ("sim:link-loss" etc).
# Usage (from backend\, venv installed):
#   .\scenarios.ps1 field-test      # the maiden-flight dress rehearsal
#   .\scenarios.ps1 link-loss
#   .\scenarios.ps1 gps-failure
#   .\scenarios.ps1 battery-fault
#   .\scenarios.ps1 rtl-recovery
#   .\scenarios.ps1 link-watchdog
#   .\scenarios.ps1 all             # full scenario suite (~10-20 min)
# Each scenario spawns the bundled SITL itself — close any hand-started SITL first.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("field-test", "link-loss", "gps-failure", "battery-fault",
                 "rtl-recovery", "link-watchdog", "guardian", "preflight",
                 "bench", "soak", "all")]
    [string]$Scenario
)

$map = @{
    "field-test"    = "tests\sitl\test_scenario_field_test.py"
    "link-loss"     = "tests\sitl\test_scenario_link_loss.py"
    "gps-failure"   = "tests\sitl\test_scenario_gps_failure.py"
    "battery-fault" = "tests\sitl\test_scenario_battery_fault.py"
    "rtl-recovery"  = "tests\sitl\test_scenario_rtl_recovery.py"
    "link-watchdog" = "tests\sitl\test_scenario_link_watchdog.py"
    "guardian"      = "tests\sitl\test_scenario_guardian.py"
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
