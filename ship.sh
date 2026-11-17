#!/usr/bin/env bash
set -euo pipefail

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREENSTREET_HOST="${1:-greenstreet@greenstreet.local}"
REMOTE_USER="${GREENSTREET_HOST%@*}"
REMOTE_DIR="/home/${REMOTE_USER}/timeline"

GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
GIT_COUNT=$(git rev-list --count HEAD 2>/dev/null || echo "dev")

echo -e "${BLUE}${BOLD}==> [1/4] Preparing remote directory structure on ${GREENSTREET_HOST} (commit: ${GIT_COMMIT}, count: ${GIT_COUNT})...${NC}"
ssh "$GREENSTREET_HOST" "mkdir -p ${REMOTE_DIR}/data ${REMOTE_DIR}/server ${REMOTE_DIR}/mood-gen"

echo -e "${BLUE}${BOLD}==> [2/4] Syncing application code, templates, and configs to ${GREENSTREET_HOST}...${NC}"
# Copy server directory (code, static, templates)
rsync -avz --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    server/ \
    "${GREENSTREET_HOST}:${REMOTE_DIR}/server/"

# Copy mood-gen worker directory
rsync -avz --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    mood-gen/ \
    "${GREENSTREET_HOST}:${REMOTE_DIR}/mood-gen/"

# Copy root deployment files
rsync -avz \
    Dockerfile \
    docker-compose.yaml \
    .dockerignore \
    nginx.conf \
    README.md \
    "${GREENSTREET_HOST}:${REMOTE_DIR}/"

if [[ -f .env ]]; then
    scp .env "${GREENSTREET_HOST}:${REMOTE_DIR}/.env"
    ssh "$GREENSTREET_HOST" "chmod 600 ${REMOTE_DIR}/.env"
fi

echo -e "${BLUE}${BOLD}==> [3/4] Building Timeline container natively for ARM64 on ${GREENSTREET_HOST}...${NC}"
ssh "$GREENSTREET_HOST" "cd ${REMOTE_DIR} && docker compose build"

echo -e "${BLUE}${BOLD}==> [4/4] Starting Timeline service stack on ${GREENSTREET_HOST}...${NC}"
ssh "$GREENSTREET_HOST" "cd ${REMOTE_DIR} && docker compose up -d --remove-orphans --force-recreate"

echo -e "${BLUE}${BOLD}==> Waiting for Timeline service health check (http://127.0.0.1:3001/health)...${NC}"
MAX_ATTEMPTS=20
ATTEMPT=1
HEALTHY=false

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
    HTTP_CODE=$(ssh "$GREENSTREET_HOST" "curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:3001/health" 2>/dev/null || true)
    if [ "$HTTP_CODE" = "200" ]; then
        HEALTHY=true
        break
    fi
    echo -e "Attempt ${ATTEMPT}/${MAX_ATTEMPTS}: Service probing returned HTTP ${HTTP_CODE}, waiting 2s..."
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))
done

if [ "$HEALTHY" = true ]; then
    echo -e "${GREEN}${BOLD}✅ Timeline service is healthy and responding on port 3001!${NC}"
else
    echo -e "${RED}${BOLD}❌ Health check failed after $((MAX_ATTEMPTS * 2)) seconds. Inspecting logs:${NC}"
    ssh "$GREENSTREET_HOST" "cd ${REMOTE_DIR} && docker compose logs --tail 40 timeline"
    exit 1
fi

ssh "$GREENSTREET_HOST" "cd ${REMOTE_DIR} && docker compose ps"

echo ""
echo -e "${GREEN}${BOLD}🎉 Timeline deployment to ${GREENSTREET_HOST} completed successfully!${NC}"
echo -e "${BLUE}Live Web URL (via reverse proxy): ${BOLD}https://thread.dryark.uk${NC}"
echo -e "${BLUE}Greenstreet Local URL:            ${BOLD}http://greenstreet.local:3001${NC}"
echo -e "${BLUE}Tailscale Endpoint:               ${BOLD}http://100.97.26.113:3001${NC}"
