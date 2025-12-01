# Stop the running pipeline processes and Docker services

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR
Set-Location $PROJECT_DIR

Write-Host "Stopping pipeline processes..." -ForegroundColor Yellow

# Step 1: Stop Python pipeline processes
# Read PIDs from file if it exists
if (Test-Path "logs/pipeline_pids.txt") {
    $pids = Get-Content "logs/pipeline_pids.txt"
    foreach ($pid in $pids) {
        $pid = $pid.Trim()
        if ($pid -match '^\d+$') {
            try {
                $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($process) {
                    Write-Host "Stopping PID $pid..."
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                }
            } catch {
                # Process may already be stopped
            }
        }
    }
    Remove-Item "logs/pipeline_pids.txt" -ErrorAction SilentlyContinue
}

# Also try to find and kill by process name
Get-Process | Where-Object { $_.CommandLine -like "*ws_ingest.py*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process | Where-Object { $_.CommandLine -like "*featurizer.py*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process | Where-Object { $_.CommandLine -like "*prediction_consumer.py*" } | Stop-Process -Force -ErrorAction SilentlyContinue

# Alternative: kill by process name using WMI
Get-WmiObject Win32_Process | Where-Object { 
    $_.CommandLine -like "*ws_ingest.py*" -or 
    $_.CommandLine -like "*featurizer.py*" -or 
    $_.CommandLine -like "*prediction_consumer.py*" 
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "✓ Python processes stopped" -ForegroundColor Green

# Step 2: Stop Docker services
Write-Host ""
Write-Host "Stopping Docker services..." -ForegroundColor Yellow

if (Get-Command docker -ErrorAction SilentlyContinue) {
    $composeCheck = docker compose -f docker/compose.yaml ps 2>&1
    if ($LASTEXITCODE -eq 0) {
        docker compose -f docker/compose.yaml down
        Write-Host "✓ Docker services stopped" -ForegroundColor Green
    } else {
        Write-Host "⚠ Docker services not running or compose file not found" -ForegroundColor Yellow
    }
} else {
    Write-Host "⚠ Docker not found, skipping Docker services" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "✓ All pipeline processes and services stopped" -ForegroundColor Green

