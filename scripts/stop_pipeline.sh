#!/bin/bash
# Stop the running pipeline processes and Docker services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Stopping pipeline processes...${NC}"

# Step 1: Stop Python pipeline processes
# Read PIDs from file if it exists
if [ -f "logs/pipeline_pids.txt" ]; then
    while read pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping PID $pid..."
            kill "$pid" 2>/dev/null || true
        fi
    done < logs/pipeline_pids.txt
    rm -f logs/pipeline_pids.txt
fi

# Also try to find and kill by process name
pkill -f "ws_ingest.py" 2>/dev/null || true
pkill -f "featurizer.py" 2>/dev/null || true
pkill -f "prediction_consumer.py" 2>/dev/null || true

echo -e "${GREEN}✓ Python processes stopped${NC}"

# Step 2: Stop Docker services
echo ""
echo -e "${YELLOW}Stopping Docker services...${NC}"

if command -v docker > /dev/null 2>&1; then
    if docker compose -f docker/compose.yaml ps > /dev/null 2>&1; then
        docker compose -f docker/compose.yaml down
        echo -e "${GREEN}✓ Docker services stopped${NC}"
    else
        echo -e "${YELLOW}⚠ Docker services not running or compose file not found${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Docker not found, skipping Docker services${NC}"
fi

echo ""
echo -e "${GREEN}✓ All pipeline processes and services stopped${NC}"

