# Start the full end-to-end pipeline: ingestion + feature computation
# This script starts data ingestion and feature pipeline in the background

$ErrorActionPreference = "Stop"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR
Set-Location $PROJECT_DIR

Write-Host "Starting End-to-End Pipeline..." -ForegroundColor Yellow
Write-Host ""

# Check if Docker services are running, start if not
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2 -ErrorAction Stop
    if ($health.status -ne "healthy") {
        throw "API not healthy"
    }
    Write-Host "✓ Docker services already running" -ForegroundColor Green
} catch {
    Write-Host "Docker services not running. Starting services..." -ForegroundColor Yellow
    
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "✗ Docker not found. Please install Docker and ensure it's running" -ForegroundColor Red
        exit 1
    }
    
    # Check if Docker images need to be built
    Write-Host "Checking Docker images..." -ForegroundColor Yellow
    # Check if the API container exists
    $containers = docker ps -a --format "{{.Names}}" 2>&1
    $containerExists = ($containers | Select-String -Pattern "^volatility-api$" -Quiet)
    
    if (-not $containerExists) {
        Write-Host "Docker images not found. Building images (this may take 2-5 minutes on first run)..." -ForegroundColor Yellow
        docker compose -f docker/compose.yaml build
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ Failed to build Docker images" -ForegroundColor Red
            Write-Host "  Check Docker logs and ensure Docker Desktop is running" -ForegroundColor Yellow
            exit 1
        }
        Write-Host "✓ Docker images built successfully" -ForegroundColor Green
    } else {
        Write-Host "✓ Docker images found" -ForegroundColor Green
    }
    
    Write-Host "Starting Docker services..." -ForegroundColor Yellow
    docker compose -f docker/compose.yaml up -d
    
    Write-Host "Waiting for services to be ready..." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
    
    # Wait for API to be ready (max 60 seconds)
    $API_READY = $false
    for ($i = 1; $i -le 30; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2 -ErrorAction Stop
            if ($health.status -eq "healthy") {
                $API_READY = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    
    if (-not $API_READY) {
        Write-Host "✗ API did not become ready. Check Docker logs:" -ForegroundColor Red
        Write-Host "  docker compose -f docker/compose.yaml logs api" -ForegroundColor Yellow
        exit 1
    }
    
    Write-Host "✓ Docker services started and ready" -ForegroundColor Green
}

# Check/create Kafka topics
Write-Host "Step 1: Ensuring Kafka topics exist..." -ForegroundColor Yellow
$topics = docker exec kafka kafka-topics --list --bootstrap-server localhost:9092 2>$null
if ($topics -notmatch "ticks.raw") {
    Write-Host "Creating Kafka topics..."
    docker exec kafka kafka-topics --create `
        --topic ticks.raw `
        --bootstrap-server localhost:9092 `
        --partitions 3 `
        --replication-factor 1 `
        --if-not-exists 2>$null
    
    docker exec kafka kafka-topics --create `
        --topic ticks.features `
        --bootstrap-server localhost:9092 `
        --partitions 3 `
        --replication-factor 1 `
        --if-not-exists 2>$null
    Write-Host "✓ Kafka topics created" -ForegroundColor Green
} else {
    Write-Host "✓ Kafka topics already exist" -ForegroundColor Green
}

# Activate virtual environment if it exists and determine Python command
if (Test-Path ".venv") {
    & ".venv\Scripts\Activate.ps1" | Out-Null
    # Use venv's python directly
    $PYTHON_CMD = ".venv\Scripts\python.exe"
} else {
    # Use python3 if python is not available
    if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command python3 -ErrorAction SilentlyContinue)) {
        Write-Host "✗ Python not found. Please install Python 3.9+ and ensure it's in PATH" -ForegroundColor Red
        exit 1
    }
    # Try python3 first, then python
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        $PYTHON_CMD = "python3"
    } else {
        $PYTHON_CMD = "python"
    }
}

# Create logs and data directories
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
New-Item -ItemType Directory -Force -Path "data\processed" | Out-Null
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"

# Start data ingestion
Write-Host ""
Write-Host "Step 2: Starting data ingestion from Coinbase..." -ForegroundColor Yellow
$ingestJob = Start-Process -FilePath $PYTHON_CMD `
    -ArgumentList "scripts/ws_ingest.py", "--pair", "BTC-USD", "--save-disk" `
    -RedirectStandardOutput "logs/ingest_${TIMESTAMP}.log" `
    -RedirectStandardError "logs/ingest_${TIMESTAMP}.log" `
    -PassThru -WindowStyle Hidden
Write-Host "✓ Data ingestion started (PID: $($ingestJob.Id))" -ForegroundColor Green
Write-Host "  Logs: logs/ingest_${TIMESTAMP}.log"

# Wait for data to start flowing
Start-Sleep -Seconds 5

# Start feature pipeline
Write-Host ""
Write-Host "Step 3: Starting feature computation pipeline..." -ForegroundColor Yellow
$featurizerJob = Start-Process -FilePath $PYTHON_CMD `
    -ArgumentList "features/featurizer.py", `
        "--topic_in", "ticks.raw", `
        "--topic_out", "ticks.features", `
        "--bootstrap_servers", "localhost:9092", `
        "--output_file", "data/processed/features_live_${TIMESTAMP}.parquet", `
        "--windows", "30", "60", "300", `
        "--add-labels" `
    -RedirectStandardOutput "logs/featurizer_${TIMESTAMP}.log" `
    -RedirectStandardError "logs/featurizer_${TIMESTAMP}.log" `
    -PassThru -WindowStyle Hidden
Write-Host "✓ Feature pipeline started (PID: $($featurizerJob.Id))" -ForegroundColor Green
Write-Host "  Logs: logs/featurizer_${TIMESTAMP}.log"

# Wait for features to start being generated
Start-Sleep -Seconds 5

# Start prediction consumer (completes end-to-end flow)
Write-Host ""
Write-Host "Step 4: Starting prediction consumer (auto-calls /predict)..." -ForegroundColor Yellow
$predictorJob = Start-Process -FilePath $PYTHON_CMD `
    -ArgumentList "scripts/prediction_consumer.py" `
    -RedirectStandardOutput "logs/predictions_${TIMESTAMP}.log" `
    -RedirectStandardError "logs/predictions_${TIMESTAMP}.log" `
    -PassThru -WindowStyle Hidden
Write-Host "✓ Prediction consumer started (PID: $($predictorJob.Id))" -ForegroundColor Green
Write-Host "  Logs: logs/predictions_${TIMESTAMP}.log"
Write-Host "  This automatically calls /predict API for each feature message"

# Save PIDs to file for easy cleanup
@($ingestJob.Id, $featurizerJob.Id, $predictorJob.Id) | Out-File -FilePath "logs/pipeline_pids.txt" -Encoding ASCII

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Pipeline is Running!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Components:"
Write-Host "  • WebSocket Ingestion: PID $($ingestJob.Id)"
Write-Host "  • Feature Pipeline: PID $($featurizerJob.Id)"
Write-Host "  • Prediction Consumer: PID $($predictorJob.Id) (auto-calls /predict)"
Write-Host ""
Write-Host "End-to-End Flow:"
Write-Host "  Coinbase → Kafka (ticks.raw) → Features → Kafka (ticks.features) → /predict API → Metrics"
Write-Host ""
Write-Host "Monitoring:"
Write-Host "  • Grafana: http://localhost:3000"
Write-Host "  • Prometheus: http://localhost:9090"
Write-Host "  • API Health: http://localhost:8000/health"
Write-Host "  • API Docs: http://localhost:8000/docs"
Write-Host ""
Write-Host "To stop the pipeline:"
Write-Host "  .\scripts\stop_pipeline.ps1"
Write-Host "  or: Stop-Process -Id $($ingestJob.Id), $($featurizerJob.Id)"
Write-Host ""
Write-Host "Pipeline will run until stopped (Ctrl+C or stop script)" -ForegroundColor Yellow

