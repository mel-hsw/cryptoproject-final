#!/bin/bash
# Start the full end-to-end pipeline: ingestion + feature computation
# This script starts data ingestion and feature pipeline in the background

# Exit on error, but allow some commands to fail gracefully
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Starting End-to-End Pipeline...${NC}"
echo ""

# Check if Docker services are running, start if not
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${YELLOW}Docker services not running. Starting services...${NC}"
    
    if ! command -v docker > /dev/null 2>&1; then
        echo -e "${RED}✗ Docker not found. Please install Docker and ensure it's running${NC}"
        exit 1
    fi
    
    # Check if Docker images need to be built
    echo -e "${YELLOW}Checking Docker images...${NC}"
    # Check if the API container exists
    if ! docker ps -a --format "{{.Names}}" 2>/dev/null | grep -q "^volatility-api$"; then
        echo -e "${YELLOW}Docker images not found. Building images (this may take 2-5 minutes on first run)...${NC}"
        docker compose -f docker/compose.yaml build
        if [ $? -ne 0 ]; then
            echo -e "${RED}✗ Failed to build Docker images${NC}"
            echo "  Check Docker logs and ensure Docker Desktop is running"
            exit 1
        fi
        echo -e "${GREEN}✓ Docker images built successfully${NC}"
    else
        echo -e "${GREEN}✓ Docker images found${NC}"
    fi
    
    echo -e "${YELLOW}Starting Docker services...${NC}"
    docker compose -f docker/compose.yaml up -d
    
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"
    sleep 10
    
    # Wait for API to be ready (max 60 seconds)
    API_READY=false
    for i in {1..30}; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            API_READY=true
            break
        fi
        sleep 2
    done
    
    if [ "$API_READY" = false ]; then
        echo -e "${RED}✗ API did not become ready. Check Docker logs:${NC}"
        echo "  docker compose -f docker/compose.yaml logs api"
        exit 1
    fi
    
    echo -e "${GREEN}✓ Docker services started and ready${NC}"
else
    echo -e "${GREEN}✓ Docker services already running${NC}"
fi

# Check/create Kafka topics
echo -e "${YELLOW}Step 1: Ensuring Kafka topics exist...${NC}"
if ! docker exec kafka kafka-topics --list --bootstrap-server localhost:9092 2>/dev/null | grep -q "ticks.raw"; then
    echo "Creating Kafka topics..."
    docker exec kafka kafka-topics --create \
        --topic ticks.raw \
        --bootstrap-server localhost:9092 \
        --partitions 3 \
        --replication-factor 1 \
        --if-not-exists 2>/dev/null || true
    
    docker exec kafka kafka-topics --create \
        --topic ticks.features \
        --bootstrap-server localhost:9092 \
        --partitions 3 \
        --replication-factor 1 \
        --if-not-exists 2>/dev/null || true
    echo -e "${GREEN}✓ Kafka topics created${NC}"
else
    echo -e "${GREEN}✓ Kafka topics already exist${NC}"
fi

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate || true
    # Use venv's python directly
    PYTHON_CMD=".venv/bin/python"
else
    # Use python3 if python is not available
    if ! command -v python &> /dev/null && ! command -v python3 &> /dev/null; then
        echo -e "${RED}✗ Python not found. Please install Python 3.9+ and ensure it's in PATH${NC}"
        exit 1
    fi
    PYTHON_CMD=$(command -v python3 || command -v python)
fi

# Create logs and data directories
mkdir -p logs
mkdir -p data/processed
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Start data ingestion
echo ""
echo -e "${YELLOW}Step 2: Starting data ingestion from Coinbase...${NC}"
$PYTHON_CMD scripts/ws_ingest.py --pair BTC-USD --save-disk > "logs/ingest_${TIMESTAMP}.log" 2>&1 &
INGEST_PID=$!
echo -e "${GREEN}✓ Data ingestion started (PID: $INGEST_PID)${NC}"
echo "  Logs: logs/ingest_${TIMESTAMP}.log"

# Wait for data to start flowing
sleep 5

# Start feature pipeline
echo ""
echo -e "${YELLOW}Step 3: Starting feature computation pipeline...${NC}"
$PYTHON_CMD features/featurizer.py \
    --topic_in ticks.raw \
    --topic_out ticks.features \
    --bootstrap_servers localhost:9092 \
    --output_file "data/processed/features_live_${TIMESTAMP}.parquet" \
    --windows 30 60 300 \
    --add-labels > "logs/featurizer_${TIMESTAMP}.log" 2>&1 &
FEATURIZER_PID=$!
echo -e "${GREEN}✓ Feature pipeline started (PID: $FEATURIZER_PID)${NC}"
echo "  Logs: logs/featurizer_${TIMESTAMP}.log"

# Wait for features to start being generated
sleep 5

# Start prediction consumer (completes end-to-end flow)
echo ""
echo -e "${YELLOW}Step 4: Starting prediction consumer (auto-calls /predict)...${NC}"
$PYTHON_CMD scripts/prediction_consumer.py > "logs/predictions_${TIMESTAMP}.log" 2>&1 &
PREDICTOR_PID=$!
echo -e "${GREEN}✓ Prediction consumer started (PID: $PREDICTOR_PID)${NC}"
echo "  Logs: logs/predictions_${TIMESTAMP}.log"
echo "  This automatically calls /predict API for each feature message"

# Save PIDs to file for easy cleanup
echo "$INGEST_PID" > logs/pipeline_pids.txt
echo "$FEATURIZER_PID" >> logs/pipeline_pids.txt
echo "$PREDICTOR_PID" >> logs/pipeline_pids.txt

echo ""
echo -e "${GREEN}=========================================="
echo "Pipeline is Running!"
echo "==========================================${NC}"
echo ""
echo "Components:"
echo "  • WebSocket Ingestion: PID $INGEST_PID"
echo "  • Feature Pipeline: PID $FEATURIZER_PID"
echo "  • Prediction Consumer: PID $PREDICTOR_PID (auto-calls /predict)"
echo ""
echo "End-to-End Flow:"
echo "  Coinbase → Kafka (ticks.raw) → Features → Kafka (ticks.features) → /predict API → Metrics"
echo ""
echo "Monitoring:"
echo "  • Grafana: http://localhost:3000"
echo "  • Prometheus: http://localhost:9090"
echo "  • API Health: http://localhost:8000/health"
echo "  • API Docs: http://localhost:8000/docs"
echo ""
echo "To stop the pipeline:"
echo "  ./scripts/stop_pipeline.sh"
echo "  or: kill $INGEST_PID $FEATURIZER_PID"
echo ""
echo -e "${YELLOW}Pipeline will run until stopped (Ctrl+C or stop script)${NC}"

