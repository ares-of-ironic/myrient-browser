#!/bin/bash
#
# sync_to_nas.sh - Synchronize downloaded files to NAS via rsync over SSH
#
# Usage: ./sync_to_nas.sh [options]
#
# Options:
#   -n, --dry-run       Show what would be transferred without actually doing it
#   -d, --delete        Delete files on NAS that don't exist locally
#   -v, --verbose       Increase verbosity
#   -q, --quiet         Suppress non-error messages
#   -h, --help          Show this help message
#

set -euo pipefail

# =============================================================================
# CONFIGURATION - Edit these values to match your setup
# =============================================================================

# SSH Connection
NAS_HOST="192.168.100.120"
NAS_USER="admin"
NAS_PORT="22"
SSH_KEY=""  # Leave empty to use default key from ssh-agent, or set path: ~/.ssh/id_rsa

# Paths
LOCAL_DIR="$HOME/projekty/myrient.erista.me/downloads/"
REMOTE_DIR="/share/Archiwum/MYRIENT"

# Rsync Performance Options
BANDWIDTH_LIMIT=""          # Limit bandwidth in KB/s (empty = unlimited), e.g., "10000" for 10 MB/s
COMPRESS="no"               # Compress during transfer: "yes" or "no" (disable for fast LAN)
CHECKSUM="no"               # Use checksum instead of mod-time+size: "yes" or "no" (slower but more accurate)
PARTIAL="yes"               # Keep partial files for resume: "yes" or "no"
PROGRESS="yes"              # Show progress during transfer: "yes" or "no"

# Rsync Behavior
DELETE_REMOTE="no"          # Delete files on NAS not present locally: "yes" or "no"
DRY_RUN="no"                # Simulate transfer without changes: "yes" or "no"
VERBOSE="no"                # Verbose output: "yes" or "no"

# File Size Filters
MIN_SIZE=""                 # Minimum file size to sync (e.g., "1M", "500K", "1G") - empty = no minimum
MAX_SIZE=""                 # Maximum file size to sync (e.g., "4G", "700M") - empty = no maximum

# File Exclusions - patterns for incomplete downloads
EXCLUDE_PATTERNS=(
    "*.part"                # Incomplete download files
    "*.tmp"                 # Temporary files
    "*.seg*"                # Segment files from parallel downloads
    ".DS_Store"             # macOS metadata
    "._*"                   # macOS resource forks
    "Thumbs.db"             # Windows thumbnails
)

# SSH Options - keep minimal for NAS compatibility
SSH_EXTRA_OPTS=""

# =============================================================================
# END OF CONFIGURATION
# =============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored message
log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Show help
show_help() {
    cat << EOF
Usage: $(basename "$0") [options]

Synchronize local downloads folder to NAS via rsync over SSH.

Options:
  -n, --dry-run       Show what would be transferred without actually doing it
  -d, --delete        Delete files on NAS that don't exist locally
  -v, --verbose       Increase verbosity
  -q, --quiet         Suppress non-error messages
  --min-size SIZE     Minimum file size to sync (e.g., 1M, 500K, 1G)
  --max-size SIZE     Maximum file size to sync (e.g., 4G, 700M)
  -h, --help          Show this help message

Current configuration:
  Local:  $LOCAL_DIR
  Remote: $NAS_USER@$NAS_HOST:$REMOTE_DIR

Size filters:
  Min size: ${MIN_SIZE:-none}
  Max size: ${MAX_SIZE:-none}

Excluded patterns:
$(printf '  - %s\n' "${EXCLUDE_PATTERNS[@]}")

EOF
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -n|--dry-run)
                DRY_RUN="yes"
                shift
                ;;
            -d|--delete)
                DELETE_REMOTE="yes"
                shift
                ;;
            -v|--verbose)
                VERBOSE="yes"
                shift
                ;;
            -q|--quiet)
                PROGRESS="no"
                VERBOSE="no"
                shift
                ;;
            --min-size)
                MIN_SIZE="$2"
                shift 2
                ;;
            --max-size)
                MAX_SIZE="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

# Build rsync command
build_rsync_cmd() {
    local cmd=(rsync)
    
    # Base options (compatible with older rsync versions on NAS)
    cmd+=(-a)                    # Archive mode (preserves permissions, times, etc.)
    cmd+=(-h)                    # Human-readable sizes
    cmd+=(--stats)               # Show transfer statistics at end
    
    # Optional features
    [[ "$COMPRESS" == "yes" ]] && cmd+=(-z)
    [[ "$CHECKSUM" == "yes" ]] && cmd+=(-c)
    [[ "$PARTIAL" == "yes" ]] && cmd+=(--partial)
    [[ "$PROGRESS" == "yes" ]] && cmd+=(--progress)
    [[ "$DELETE_REMOTE" == "yes" ]] && cmd+=(--delete)
    [[ "$DRY_RUN" == "yes" ]] && cmd+=(-n)
    [[ "$VERBOSE" == "yes" ]] && cmd+=(-v)
    [[ -n "$BANDWIDTH_LIMIT" ]] && cmd+=(--bwlimit="$BANDWIDTH_LIMIT")
    
    # Size filters
    [[ -n "$MIN_SIZE" ]] && cmd+=(--min-size="$MIN_SIZE")
    [[ -n "$MAX_SIZE" ]] && cmd+=(--max-size="$MAX_SIZE")
    
    # Exclusions
    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        cmd+=(--exclude="$pattern")
    done
    
    # SSH command with options
    local ssh_cmd="ssh -p $NAS_PORT"
    [[ -n "$SSH_EXTRA_OPTS" ]] && ssh_cmd+=" $SSH_EXTRA_OPTS"
    [[ -n "$SSH_KEY" ]] && ssh_cmd+=" -i $SSH_KEY"
    cmd+=(-e "$ssh_cmd")
    
    # Source and destination (trailing slash on source = sync contents)
    cmd+=("${LOCAL_DIR}/")
    cmd+=("${NAS_USER}@${NAS_HOST}:${REMOTE_DIR}/")
    
    echo "${cmd[@]}"
}

# Check prerequisites
check_prerequisites() {
    # Check rsync
    if ! command -v rsync &> /dev/null; then
        log_error "rsync is not installed. Please install it first."
        exit 1
    fi
    
    # Check local directory exists
    if [[ ! -d "$LOCAL_DIR" ]]; then
        log_error "Local directory does not exist: $LOCAL_DIR"
        exit 1
    fi
    
    # Check SSH connectivity
    log_info "Testing SSH connection to $NAS_USER@$NAS_HOST..."
    local ssh_test_cmd="ssh -p $NAS_PORT"
    [[ -n "$SSH_EXTRA_OPTS" ]] && ssh_test_cmd+=" $SSH_EXTRA_OPTS"
    [[ -n "$SSH_KEY" ]] && ssh_test_cmd+=" -i $SSH_KEY"
    ssh_test_cmd+=" $NAS_USER@$NAS_HOST echo 'Connection OK'"
    
    if ! eval "$ssh_test_cmd" &> /dev/null; then
        log_error "Cannot connect to NAS via SSH. Please check:"
        log_error "  - Host: $NAS_HOST"
        log_error "  - User: $NAS_USER"
        log_error "  - Port: $NAS_PORT"
        log_error "  - SSH key authentication"
        exit 1
    fi
    log_ok "SSH connection successful"
}

# Show sync summary
show_summary() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Sync Configuration"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Local:     $LOCAL_DIR"
    echo "  Remote:    $NAS_USER@$NAS_HOST:$REMOTE_DIR"
    echo "  Port:      $NAS_PORT"
    echo ""
    echo "  Options:"
    echo "    Dry run:     $DRY_RUN"
    echo "    Delete:      $DELETE_REMOTE"
    echo "    Compress:    $COMPRESS"
    echo "    Checksum:    $CHECKSUM"
    echo "    Bandwidth:   ${BANDWIDTH_LIMIT:-unlimited}"
    echo "    Min size:    ${MIN_SIZE:-none}"
    echo "    Max size:    ${MAX_SIZE:-none}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# Count local files (excluding patterns)
count_local_files() {
    local count=0
    local exclude_args=()
    
    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        exclude_args+=(! -name "$pattern")
    done
    
    count=$(find "$LOCAL_DIR" -type f "${exclude_args[@]}" 2>/dev/null | wc -l | tr -d ' ')
    echo "$count"
}

# Main function
main() {
    parse_args "$@"
    
    echo ""
    log_info "Myrient NAS Sync"
    echo ""
    
    check_prerequisites
    show_summary
    
    # Count files
    local file_count
    file_count=$(count_local_files)
    log_info "Found $file_count files to sync (excluding incomplete downloads)"
    
    if [[ "$DRY_RUN" == "yes" ]]; then
        log_warn "DRY RUN MODE - No files will be transferred"
    fi
    
    echo ""
    log_info "Starting rsync..."
    echo ""
    
    # Build and execute rsync command
    local rsync_cmd
    rsync_cmd=$(build_rsync_cmd)
    
    if [[ "$VERBOSE" == "yes" ]]; then
        log_info "Command: $rsync_cmd"
        echo ""
    fi
    
    # Execute rsync
    local start_time
    start_time=$(date +%s)
    
    if eval "$rsync_cmd"; then
        local end_time
        end_time=$(date +%s)
        local duration=$((end_time - start_time))
        
        echo ""
        log_ok "Sync completed successfully in ${duration}s"
    else
        local exit_code=$?
        echo ""
        log_error "Sync failed with exit code: $exit_code"
        exit $exit_code
    fi
}

# Run main function
main "$@"
