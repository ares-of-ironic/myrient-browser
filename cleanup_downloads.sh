#!/bin/bash
#
# cleanup_downloads.sh - Remove completed downloads from local folder
#
# Usage: ./cleanup_downloads.sh [options]
#
# Options:
#   -n, --dry-run       Show what would be deleted without actually doing it
#   -v, --verbose       Show each file being deleted
#   -a, --age MINUTES   Minimum file age in minutes (default: 30)
#   -h, --help          Show this help message
#

set -euo pipefail

# =============================================================================
# CONFIGURATION
# =============================================================================

# Downloads directory
DOWNLOADS_DIR="$HOME/projekty/myrient.erista.me/downloads"

# Minimum age in minutes (only delete files older than this)
MIN_AGE_MINUTES=30

# File size filters (in bytes, or use suffixes: K, M, G)
MIN_SIZE=""                 # Minimum file size to delete (e.g., "1M") - empty = no minimum
MAX_SIZE=""                 # Maximum file size to delete (e.g., "1G") - empty = no maximum

# Patterns for incomplete/partial files (will be skipped)
PARTIAL_PATTERNS=(
    "*.part"            # Incomplete download files
    "*.tmp"             # Temporary files
    "*.seg*"            # Segment files from parallel downloads
)

# =============================================================================
# END OF CONFIGURATION
# =============================================================================

# Options
DRY_RUN="no"
VERBOSE="no"

# Counters for size-skipped files
FILES_SKIPPED_SIZE=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
FILES_DELETED=0
FILES_SKIPPED_PARTIAL=0
FILES_SKIPPED_RECENT=0
BYTES_FREED=0

# Print colored message
log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_del()   { [[ "$VERBOSE" == "yes" ]] && echo -e "${RED}[DEL]${NC} $*"; return 0; }
log_skip()  { [[ "$VERBOSE" == "yes" ]] && echo -e "${YELLOW}[SKIP]${NC} $*"; return 0; }

# Show help
show_help() {
    cat << EOF
Usage: $(basename "$0") [options]

Remove completed downloads from local folder, keeping:
  - All directories (empty or not)
  - Partial/incomplete files (*.part, *.tmp, *.seg*)
  - Files newer than $MIN_AGE_MINUTES minutes
  - Files outside size range (if specified)

Options:
  -n, --dry-run        Show what would be deleted without actually doing it
  -v, --verbose        Show each file being processed
  -a, --age MINUTES    Minimum file age in minutes (default: $MIN_AGE_MINUTES)
  --min-size SIZE      Minimum file size to delete (e.g., 1M, 500K, 1G)
  --max-size SIZE      Maximum file size to delete (e.g., 4G, 700M)
  -h, --help           Show this help message

Directory: $DOWNLOADS_DIR
Size filters: min=${MIN_SIZE:-none}, max=${MAX_SIZE:-none}

EOF
}

# Format bytes to human readable
format_bytes() {
    local bytes=$1
    if (( bytes >= 1073741824 )); then
        echo "$((bytes / 1073741824)) GB"
    elif (( bytes >= 1048576 )); then
        echo "$((bytes / 1048576)) MB"
    elif (( bytes >= 1024 )); then
        echo "$((bytes / 1024)) KB"
    else
        echo "${bytes} B"
    fi
}

# Check if file matches partial patterns
is_partial_file() {
    local filename="$1"
    local basename
    basename=$(basename "$filename")
    
    for pattern in "${PARTIAL_PATTERNS[@]}"; do
        # Convert glob pattern to regex-like matching
        case "$basename" in
            $pattern) return 0 ;;
        esac
    done
    return 1
}

# Parse size string (e.g., "100M", "1G", "500K") to bytes
parse_size() {
    local size_str="$1"
    local number="${size_str%[KMGkmg]*}"
    local suffix="${size_str##*[0-9]}"
    
    case "$suffix" in
        K|k) echo $((number * 1024)) ;;
        M|m) echo $((number * 1024 * 1024)) ;;
        G|g) echo $((number * 1024 * 1024 * 1024)) ;;
        "") echo "$number" ;;
        *) echo "$number" ;;
    esac
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -n|--dry-run)
                DRY_RUN="yes"
                shift
                ;;
            -v|--verbose)
                VERBOSE="yes"
                shift
                ;;
            -a|--age)
                MIN_AGE_MINUTES="$2"
                shift 2
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

# Process files
process_files() {
    # Parse size filters to bytes
    local min_size_bytes=0
    local max_size_bytes=0
    [[ -n "$MIN_SIZE" ]] && min_size_bytes=$(parse_size "$MIN_SIZE")
    [[ -n "$MAX_SIZE" ]] && max_size_bytes=$(parse_size "$MAX_SIZE")
    
    log_info "Scanning for files older than $MIN_AGE_MINUTES minutes..."
    [[ -n "$MIN_SIZE" ]] && log_info "Min size: $MIN_SIZE ($min_size_bytes bytes)"
    [[ -n "$MAX_SIZE" ]] && log_info "Max size: $MAX_SIZE ($max_size_bytes bytes)"
    echo ""
    
    # Build find command with time filter (much faster than checking each file)
    # -mmin +N finds files modified more than N minutes ago
    local find_cmd="find \"$DOWNLOADS_DIR\" -type f -mmin +${MIN_AGE_MINUTES}"
    
    # Add size filters to find command if specified (much faster)
    if [[ -n "$MIN_SIZE" ]]; then
        find_cmd="$find_cmd -size +${MIN_SIZE}"
    fi
    if [[ -n "$MAX_SIZE" ]]; then
        find_cmd="$find_cmd -size -${MAX_SIZE}"
    fi
    
    # Exclude partial files patterns
    for pattern in "${PARTIAL_PATTERNS[@]}"; do
        find_cmd="$find_cmd ! -name \"$pattern\""
    done
    
    log_info "Finding matching files..."
    
    # Find matching files - use temp file for bash 3.2 compatibility
    local tmpfile
    tmpfile=$(mktemp)
    eval "$find_cmd" > "$tmpfile" 2>/dev/null
    
    local total_files
    total_files=$(wc -l < "$tmpfile" | tr -d ' ')
    log_info "Found $total_files files matching criteria"
    echo ""
    
    local processed=0
    while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        
        processed=$((processed + 1))
        
        # Show progress every 1000 files
        if [ $((processed % 1000)) -eq 0 ]; then
            echo -ne "\r  Processing: $processed / $total_files files...   "
        fi
        
        local file_size
        file_size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null || echo 0)
        
        # Delete or show what would be deleted
        if [ "$DRY_RUN" = "yes" ]; then
            log_del "$file ($(format_bytes "$file_size"))"
        else
            if rm -f "$file"; then
                log_del "$file ($(format_bytes "$file_size"))"
            else
                log_error "Failed to delete: $file"
                continue
            fi
        fi
        
        FILES_DELETED=$((FILES_DELETED + 1))
        BYTES_FREED=$((BYTES_FREED + file_size))
        
    done < "$tmpfile"
    
    # Clear progress line
    echo -ne "\r                                                    \r"
    
    rm -f "$tmpfile"
}

# Show summary
show_summary() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Cleanup Summary"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo -e "  ${YELLOW}DRY RUN - No files were actually deleted${NC}"
        echo ""
        echo "  Would delete:     $FILES_DELETED files"
        echo "  Would free:       $(format_bytes $BYTES_FREED)"
    else
        echo "  Files deleted:    $FILES_DELETED"
        echo "  Space freed:      $(format_bytes $BYTES_FREED)"
    fi
    
    echo ""
    echo "  Skipped (partial): $FILES_SKIPPED_PARTIAL files"
    echo "  Skipped (size):    $FILES_SKIPPED_SIZE files"
    echo "  Skipped (recent):  $FILES_SKIPPED_RECENT files"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Main function
main() {
    parse_args "$@"
    
    echo ""
    log_info "Downloads Cleanup"
    echo ""
    
    # Check directory exists
    if [[ ! -d "$DOWNLOADS_DIR" ]]; then
        log_error "Downloads directory does not exist: $DOWNLOADS_DIR"
        exit 1
    fi
    
    log_info "Directory: $DOWNLOADS_DIR"
    log_info "Min age:   $MIN_AGE_MINUTES minutes"
    
    if [[ "$DRY_RUN" == "yes" ]]; then
        log_warn "DRY RUN MODE - No files will be deleted"
    fi
    
    echo ""
    
    process_files
    show_summary
    
    if [[ "$DRY_RUN" == "no" && $FILES_DELETED -gt 0 ]]; then
        echo ""
        log_ok "Cleanup completed"
    fi
}

# Run main function
main "$@"
