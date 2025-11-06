#!/bin/bash
# Bootstrap script for Open Chat Studio development environment
# This script installs and configures all necessary dependencies

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Install UV if not present
install_uv() {
    step "Checking UV installation..."
    if command_exists uv; then
        info "UV is already installed ($(uv --version))"
    else
        info "Installing UV..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # Add to PATH for current session
        export PATH="$HOME/.cargo/bin:$PATH"
        if command_exists uv; then
            info "UV installed successfully ($(uv --version))"
        else
            error "UV installation failed"
            exit 1
        fi
    fi
}

# Check Node.js and npm
check_node() {
    step "Checking Node.js installation..."
    if command_exists node; then
        NODE_VERSION=$(node --version)
        info "Node.js is installed ($NODE_VERSION)"

        # Check if version is 18 or higher
        NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d'.' -f1 | sed 's/v//')
        if [ "$NODE_MAJOR" -lt 18 ]; then
            warn "Node.js version $NODE_VERSION is installed, but version 18+ is recommended"
        fi
    else
        error "Node.js is not installed. Please install Node.js 18+ before running this script."
        error "Visit: https://nodejs.org/ or use a version manager like nvm"
        exit 1
    fi

    if command_exists npm; then
        info "npm is installed ($(npm --version))"
    else
        error "npm is not installed. Please install npm before running this script."
        exit 1
    fi
}

# Check Python version
check_python() {
    step "Checking Python installation..."
    if command_exists python3; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
        info "Python is installed (version $PYTHON_VERSION)"

        # Check if version is 3.13 or higher (project requires 3.13+)
        PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f1)
        PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f2)
        if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 13 ]; then
            warn "Python $PYTHON_VERSION is installed, but version 3.13+ is recommended"
        fi
    else
        error "Python 3 is not installed. Please install Python 3.13+ before running this script."
        exit 1
    fi
}

# Check Docker (optional but recommended)
check_docker() {
    step "Checking Docker installation..."
    if command_exists docker; then
        info "Docker is installed ($(docker --version))"
        # Check if Docker is running
        if docker info >/dev/null 2>&1; then
            info "Docker daemon is running"
        else
            warn "Docker is installed but daemon is not running"
            warn "Start it with: sudo systemctl start docker (Linux) or start Docker Desktop (Mac/Windows)"
        fi
    else
        warn "Docker is not installed (optional)"
        warn "Without Docker, you'll need PostgreSQL and Redis installed locally"
        warn "Install Docker from: https://docs.docker.com/get-docker/"
    fi
}

# Check PostgreSQL (if not using Docker)
check_postgres() {
    step "Checking PostgreSQL..."
    if command_exists psql; then
        info "PostgreSQL client is installed ($(psql --version))"
    elif ! command_exists docker; then
        warn "PostgreSQL client not found and Docker not available"
        warn "You'll need PostgreSQL 12+ with pgvector extension"
    fi
}

# Check Redis (if not using Docker)
check_redis() {
    step "Checking Redis..."
    if command_exists redis-cli; then
        info "Redis client is installed"
    elif ! command_exists docker; then
        warn "Redis client not found and Docker not available"
        warn "You'll need Redis for Celery task queue"
    fi
}

# Install Python dependencies
install_python_deps() {
    step "Installing Python dependencies..."
    info "Running: uv sync --frozen --dev"
    uv sync --frozen --dev
    info "Python dependencies installed successfully"
}

# Install Node.js dependencies
install_node_deps() {
    step "Installing Node.js dependencies..."
    info "Running: npm install"
    npm install
    info "Node.js dependencies installed successfully"
}

# Check for .env file
check_env_file() {
    step "Checking environment configuration..."
    if [ -f ".env" ]; then
        info ".env file exists"
    else
        warn ".env file not found"
        if [ -f ".env.example" ]; then
            warn "Copy .env.example to .env and configure it: cp .env.example .env"
        fi
    fi
}

# Print next steps
print_next_steps() {
    echo ""
    info "Bootstrap complete! 🚀"
    echo ""
    echo "Next steps:"
    echo ""

    if [ ! -f ".env" ]; then
        echo "  1. Configure environment:"
        echo "     cp .env.example .env"
        echo "     # Edit .env with your settings"
        echo ""
    fi

    echo "  2. Start services (PostgreSQL & Redis):"
    echo "     inv up"
    echo ""

    echo "  3. Run database migrations:"
    echo "     python manage.py migrate"
    echo ""

    echo "  4. Start development server:"
    echo "     inv runserver"
    echo ""

    echo "  5. In another terminal, build frontend assets:"
    echo "     npm run dev-watch"
    echo ""

    echo "See CLAUDE.md for more development commands and information."
    echo ""
}

# Main installation flow
main() {
    echo ""
    info "Starting Open Chat Studio development environment bootstrap..."
    echo ""

    # Check prerequisites
    check_python
    check_node
    check_docker
    check_postgres
    check_redis

    echo ""

    # Install UV (checks first)
    install_uv

    echo ""

    # Install dependencies (always run)
    install_python_deps
    install_node_deps

    echo ""

    # Check environment
    check_env_file

    # Print next steps
    print_next_steps
}

# Run main function
main
