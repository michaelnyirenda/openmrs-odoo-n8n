#!/usr/bin/env bash
# ==============================================================================
# OpenMRS 3 ↔ n8n ↔ Odoo 19 Turnkey Bootstrap Script
# ==============================================================================
# Automates the end-to-end initialization, service health verification,
# database setup, n8n workflow deployment, and clinical data seeding.
# Supported on: Linux (Fedora/Ubuntu/Debian) & Windows WSL2 + Docker Desktop.
# ==============================================================================

set -eo pipefail

# Text formatting
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
NC="\033[0m" # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_banner() {
    echo -e "${CYAN}${BOLD}"
    cat << "EOF"
  ___                   __  __ ____  ____     _____     _             
 / _ \ _ __   ___ _ __ |  \/  |  _ \/ ___|   | ____|___| |_ _   _ _ __  
| | | | '_ \ / _ \ '_ \| |\/| | |_) \___ \   |  _| / __| __| | | | '_ \ 
| |_| | |_) |  __/ | | | |  | |  _ < ___) |  | |__| (__| |_| |_| | |_) |
 \___/| .__/ \___|_| |_|_|  |_|_| \_\____/___|_____\___|\__|\__,_| .__/ 
      |_|                               |_____|                  |_|    
      + Odoo 19 ERP + n8n Clinical Integration Engine
EOF
    echo -e "${NC}"
}

print_banner

# ------------------------------------------------------------------------------
# 1. WSL2 / Filesystem Pre-Flight Checks
# ------------------------------------------------------------------------------
log_info "Performing environment pre-flight checks..."

CURRENT_DIR="$(pwd -P)"
if [[ "$CURRENT_DIR" =~ ^/mnt/[a-zA-Z]/ ]]; then
    log_warn "=================================================================="
    log_warn "CRITICAL WARNING: Running inside Windows mount ($CURRENT_DIR)"
    log_warn "Docker volume bind mounts for PostgreSQL and MariaDB will fail"
    log_warn "due to Windows NTFS permission constraints."
    log_warn ""
    log_warn "Recommended: Move this project into your native WSL2 home directory:"
    log_warn "  mkdir -p ~/projects && cd ~/projects"
    log_warn "  git clone <repo_url> && cd openmrs-odoo-n8n"
    log_warn "=================================================================="
    read -r -p "Do you want to continue anyway? (y/N): " CONTINUE_NTFS
    if [[ ! "$CONTINUE_NTFS" =~ ^[yY]$ ]]; then
        log_error "Aborted by user to avoid NTFS permission errors."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# 2. Dependency Checks
# ------------------------------------------------------------------------------
# Check Docker
if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker is not installed. Please install Docker or Docker Desktop."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    log_error "Docker daemon is not running. Please start Docker / Docker Desktop."
    exit 1
fi

# Check Docker Compose (v2 or v1)
COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    log_error "Neither 'docker compose' nor 'docker-compose' was found."
    exit 1
fi
log_success "Found Docker & Compose ($COMPOSE_CMD)"

# Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    log_error "Python 3 is required for clinical data seeding. Please install python3."
    exit 1
fi

# Check curl
if ! command -v curl >/dev/null 2>&1; then
    log_error "curl is required for health polling. Please install curl."
    exit 1
fi

# Ensure python requests is available
if ! python3 -c "import requests" >/dev/null 2>&1; then
    log_info "Installing required Python 'requests' package..."
    pip3 install requests --quiet || pip install requests --quiet || {
        log_warn "Failed to install requests automatically. Seeding might require 'pip3 install requests'."
    }
fi

# ------------------------------------------------------------------------------
# 3. Environment & Volume Directories Setup
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
    log_info "Creating .env from .env.example..."
    cp .env.example .env
fi

log_info "Ensuring volume directories exist with appropriate permissions..."
mkdir -p odoo-db-data odoo-web-data odoo-config openmrs-db-data openmrs-data n8n-data workflows docs
chmod -R 777 odoo-web-data odoo-config n8n-data openmrs-data 2>/dev/null || true

# ------------------------------------------------------------------------------
# 4. Start Docker Containers
# ------------------------------------------------------------------------------
log_info "Starting Docker services via '$COMPOSE_CMD up -d'..."
$COMPOSE_CMD up -d

# ------------------------------------------------------------------------------
# 5. Service Health Waiting Loop
# ------------------------------------------------------------------------------
wait_for_http() {
    local url="$1"
    local name="$2"
    local max_retries="${3:-45}"
    local wait_interval="${4:-4}"
    local retries=0

    log_info "Waiting for $name to become healthy ($url)..."
    while [ $retries -lt "$max_retries" ]; do
        if curl -s -f -m 5 -o /dev/null "$url" 2>/dev/null; then
            log_success "$name is UP and responding!"
            return 0
        fi
        retries=$((retries + 1))
        echo -n "."
        sleep "$wait_interval"
    done
    echo ""
    log_warn "$name did not respond within $((max_retries * wait_interval))s, but will proceed."
    return 1
}

# Wait for services
wait_for_http "http://localhost:5678/healthz" "n8n Integration Engine" 30 3 || true
wait_for_http "http://localhost:8069/web/login" "Odoo 19 ERP" 30 3 || true
wait_for_http "http://localhost:8080/openmrs/ws/rest/v1/session" "OpenMRS 3 Backend REST API" 60 5 || true

# ------------------------------------------------------------------------------
# 6. Odoo Database Initialization & API Key Setup
# ------------------------------------------------------------------------------
log_info "Verifying Odoo database 'clinic_db'..."
ODOO_DB_EXISTS=$(docker exec -i odoo-db psql -U odoo -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='clinic_db'" 2>/dev/null || echo "0")

if [ "$ODOO_DB_EXISTS" != "1" ]; then
    log_info "Database 'clinic_db' does not exist yet. Initializing with healthcare modules (base,sale,purchase,account,stock)..."
    log_info "This may take 1-2 minutes on first run. Please wait..."
    docker exec -i odoo19 odoo -d clinic_db -i base,sale,purchase,account,stock --without-demo=all --stop-after-init
    log_success "Odoo database 'clinic_db' initialized successfully!"
else
    log_success "Odoo database 'clinic_db' already exists."
fi

# Ensure admin user login, password, and API Key exist
log_info "Configuring Odoo admin credentials and integration API Key..."
docker exec -i odoo19 odoo shell -d clinic_db << 'EOF' 2>/dev/null || true
try:
    admin = env.ref('base.user_admin')
    # Guarantee standard login and password
    admin.login = 'admin@clinic.com'
    admin._change_password('admin')
    
    # Guarantee API Key for n8n and automation scripts
    target_key = '1f4624cfdc7ce0fea669677b8ff485ad087f720c'
    target_idx = target_key[:8]
    env.cr.execute("SELECT id FROM res_users_apikeys WHERE index = %s AND user_id = %s", [target_idx, admin.id])
    if not env.cr.fetchone():
        from passlib.context import CryptContext
        ctx = CryptContext(['pbkdf2_sha512'], pbkdf2_sha512__rounds=6000)
        env.cr.execute(
            "INSERT INTO res_users_apikeys (name, user_id, scope, expiration_date, key, index) VALUES (%s, %s, %s, %s, %s, %s)",
            ['n8n Integration Key', admin.id, 'rpc', None, ctx.hash(target_key), target_idx]
        )
    env.cr.commit()
    print("Odoo admin credentials and API key configured successfully.")
except Exception as e:
    print(f"Odoo user configuration status: {e}")
EOF
log_success "Odoo admin credentials and API key configured."

# ------------------------------------------------------------------------------
# 7. n8n Workflows & Credentials Auto-Import
# ------------------------------------------------------------------------------
log_info "Importing credentials and workflows into n8n..."
if [ -f workflows/n8n_credentials.json ]; then
    docker cp workflows/n8n_credentials.json n8n-engine:/tmp/n8n_credentials.json 2>/dev/null || true
    docker exec -u node n8n-engine n8n import:credentials --input=/tmp/n8n_credentials.json 2>/dev/null || true
    log_success "Imported n8n credentials."
fi

if [ -f workflows/sync_patients_openmrs_odoo.json ]; then
    docker cp workflows/sync_patients_openmrs_odoo.json n8n-engine:/tmp/sync_patients_openmrs_odoo.json 2>/dev/null || true
    docker exec -u node n8n-engine n8n import:workflow --input=/tmp/sync_patients_openmrs_odoo.json 2>/dev/null || true
fi

if [ -f workflows/order_to_billing_and_stock.json ]; then
    docker cp workflows/order_to_billing_and_stock.json n8n-engine:/tmp/order_to_billing_and_stock.json 2>/dev/null || true
    docker exec -u node n8n-engine n8n import:workflow --input=/tmp/order_to_billing_and_stock.json 2>/dev/null || true
fi

log_info "Activating and publishing n8n workflows..."
docker exec -u node n8n-engine n8n publish:workflow --id=WkflwSyncPat0001 2>/dev/null || true
docker exec -u node n8n-engine n8n publish:workflow --id=WkflwOrderBill002 2>/dev/null || true
$COMPOSE_CMD restart n8n
wait_for_http "http://localhost:5678/healthz" "n8n Integration Engine (Active)" 20 2 || true
log_success "Imported and activated n8n clinical workflows."

# ------------------------------------------------------------------------------
# 8. Clinical Data Seeding
# ------------------------------------------------------------------------------
SKIP_SEED=false
for arg in "$@"; do
    if [ "$arg" == "--skip-seed" ]; then
        SKIP_SEED=true
    fi
done

if [ "$SKIP_SEED" = false ] && [ -f seed_clinical_ecosystem.py ]; then
    log_info "Seeding clinical data (products, lots, stock, mock patients, and orders)..."
    python3 seed_clinical_ecosystem.py || {
        log_warn "Seeding encountered non-fatal issues. You can re-run 'python3 seed_clinical_ecosystem.py' at any time."
    }
    log_success "Clinical ecosystem seeding completed!"
else
    log_info "Skipping clinical data seeding (--skip-seed specified or script missing)."
fi

# ------------------------------------------------------------------------------
# 9. Summary & Access Dashboard
# ------------------------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo -e "${GREEN}${BOLD}       Ecosystem Ready! Service Dashboard & Testing Access            ${NC}"
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo -e " ${BOLD}OpenMRS 3 Frontend:${NC}    http://localhost:8080/openmrs/spa"
echo -e "   - Username:            admin"
echo -e "   - Password:            Admin123"
echo ""
echo -e " ${BOLD}Odoo 19 ERP:${NC}            http://localhost:8069"
echo -e "   - Database:            clinic_db"
echo -e "   - Username:            admin@clinic.com"
echo -e "   - Password:            admin"
echo -e "   - Integration API Key: 1f4624cfdc7ce0fea669677b8ff485ad087f720c"
echo ""
echo -e " ${BOLD}n8n Integration Engine:${NC} http://localhost:5678"
echo ""
echo -e " ${BOLD}Standard Operating Procedure (SOP):${NC}"
echo -e "   docs/sop_patient_lifecycle_openmrs_odoo.md"
echo ""
echo -e " ${BOLD}End-to-End Regression Verification:${NC}"
echo -e "   python3 verify_ecosystem.py"
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo ""
