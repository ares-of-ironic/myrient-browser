"""NAS verification module - compare Myrient index with files on NAS."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .indexer import FileIndex

logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    """Information about a file."""
    path: str
    size: int


@dataclass
class VerifyResult:
    """Result of NAS verification."""
    
    # Path that was verified
    myrient_path: str
    
    # Files found in Myrient index
    index_files: list[FileInfo]
    
    # Files found on NAS
    nas_files: dict[str, int]  # path -> size
    
    # Missing files (in index but not on NAS)
    missing_files: list[FileInfo]
    
    # Size mismatch files (exists but different size)
    size_mismatch_files: list[tuple[FileInfo, int]]  # (index_file, nas_size)
    
    # Extra files on NAS (not in index)
    extra_files: list[FileInfo]
    
    @property
    def total_missing_size(self) -> int:
        """Total size of missing files."""
        return sum(f.size for f in self.missing_files)
    
    @property
    def total_mismatch_size(self) -> int:
        """Total size of files with size mismatch (index size)."""
        return sum(f.size for f, _ in self.size_mismatch_files)
    
    @property
    def is_complete(self) -> bool:
        """True if all files are present with correct sizes."""
        return len(self.missing_files) == 0 and len(self.size_mismatch_files) == 0


class NASVerifier:
    """Verifies files on NAS against Myrient index."""
    
    def __init__(self, config: Config, index: FileIndex):
        self.config = config
        self.index = index
    
    def _build_ssh_cmd(self) -> list[str]:
        """Build SSH command with proper options."""
        nas = self.config.nas
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={nas.timeout}"]
        
        if nas.port != 22:
            cmd.extend(["-p", str(nas.port)])
        
        if nas.ssh_key:
            cmd.extend(["-i", nas.ssh_key])
        
        cmd.append(f"{nas.user}@{nas.host}")
        return cmd
    
    def test_connection(self) -> tuple[bool, str]:
        """Test SSH connection to NAS.
        
        Returns:
            (success, message)
        """
        try:
            ssh_cmd = self._build_ssh_cmd()
            ssh_cmd.append("echo 'OK'")
            
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=self.config.nas.timeout + 5,
            )
            
            if result.returncode == 0 and "OK" in result.stdout:
                return True, "Connection successful"
            else:
                return False, f"Connection failed: {result.stderr.strip()}"
        
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection error: {e}"
    
    def list_nas_files(self, remote_subpath: str) -> dict[str, int]:
        """List files on NAS with their sizes.
        
        Args:
            remote_subpath: Subpath relative to nas.remote_path (e.g., "TOSEC/Commodore/C64")
        
        Returns:
            Dictionary mapping relative paths to file sizes
        """
        nas = self.config.nas
        full_remote_path = f"{nas.remote_path.rstrip('/')}/{remote_subpath.strip('/')}"
        
        ssh_cmd = self._build_ssh_cmd()
        
        # Use find piped to while loop with stat
        # This handles filenames with spaces correctly
        # Format: SIZE PATH (space-separated, size is always first number)
        find_cmd = f'''cd "{full_remote_path}" 2>/dev/null && find . -type f | while IFS= read -r f; do stat -c "%s %n" "$f" 2>/dev/null; done'''
        ssh_cmd.append(find_cmd)
        
        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes for large directories
            )
            
            # Check if command produced output (ignore stderr warnings)
            if not result.stdout.strip():
                if result.returncode != 0:
                    logger.warning(f"Failed to list NAS files (exit {result.returncode})")
                return {}
            
            files: dict[str, int] = {}
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                
                # Split on first space - size is first, path is rest
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    try:
                        size = int(parts[0])
                        path = parts[1]
                        # Normalize path (remove ./ prefix)
                        if path.startswith("./"):
                            path = path[2:]
                        files[path] = size
                    except ValueError:
                        continue
            
            return files
        
        except subprocess.TimeoutExpired:
            logger.error("Timeout while listing NAS files")
            return {}
        except Exception as e:
            logger.error(f"Error listing NAS files: {e}")
            return {}
    
    def verify(self, myrient_path: str, progress_callback=None) -> VerifyResult:
        """Verify files for a given Myrient path.
        
        Args:
            myrient_path: Path in Myrient index (e.g., "TOSEC/Commodore/C64/Games")
            progress_callback: Optional callback(stage, current, total) for progress
        
        Returns:
            VerifyResult with comparison details
        """
        if progress_callback:
            progress_callback("index", 0, 0)
        
        # Get files from index
        index_files = self._get_index_files(myrient_path)
        
        if progress_callback:
            progress_callback("nas", 0, len(index_files))
        
        # Get files from NAS
        nas_files = self.list_nas_files(myrient_path)
        
        if progress_callback:
            progress_callback("compare", 0, len(index_files))
        
        # Compare
        missing_files: list[FileInfo] = []
        size_mismatch_files: list[tuple[FileInfo, int]] = []
        
        index_paths = set()
        for idx_file in index_files:
            index_paths.add(idx_file.path)
            
            if idx_file.path not in nas_files:
                missing_files.append(idx_file)
            elif self.config.nas.verify_sizes and idx_file.size > 0:
                nas_size = nas_files[idx_file.path]
                if nas_size != idx_file.size:
                    size_mismatch_files.append((idx_file, nas_size))
        
        # Find extra files on NAS
        extra_files: list[FileInfo] = []
        for nas_path, nas_size in nas_files.items():
            if nas_path not in index_paths:
                extra_files.append(FileInfo(nas_path, nas_size))
        
        return VerifyResult(
            myrient_path=myrient_path,
            index_files=index_files,
            nas_files=nas_files,
            missing_files=missing_files,
            size_mismatch_files=size_mismatch_files,
            extra_files=extra_files,
        )
    
    def _get_index_files(self, myrient_path: str) -> list[FileInfo]:
        """Get all files under a path from the index."""
        # Normalize path
        myrient_path = myrient_path.strip("/")
        prefix_with_slash = myrient_path + "/"
        
        files: list[FileInfo] = []
        
        # Iterate through all paths in the index
        for path in self.index.all_paths:
            # Check if this path is under our target directory
            if not path.startswith(prefix_with_slash):
                continue
            
            # Get file info
            is_dir, size = self.index._path_info.get(path, (False, -1))
            
            # Skip directories
            if is_dir:
                continue
            
            # Get relative path
            rel_path = path[len(prefix_with_slash):]
            if rel_path:
                files.append(FileInfo(rel_path, max(0, size)))
        
        return files


def format_size(size: int) -> str:
    """Format size in human-readable form."""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    elif size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    elif size >= 1024:
        return f"{size / 1024:.2f} KB"
    else:
        return f"{size} B"
