"""
Safe file I/O utilities for command line.

Handles macOS quarantine (com.apple.quarantine) extended attributes
that cause PermissionError (EPERM / errno 1) on file writes
even when the user owns the file.
"""
import os
from pathlib import Path
from typing import Optional


def safe_write_text(filepath, content: str) -> bool:
    """
    Write text to a file, with fallbacks for macOS quarantine restrictions.
    
    Strategy chain:
      1. Path.write_text() — standard, creates file if needed
      2. open() with 'w' — slightly different code path
      3. os.open() with O_WRONLY|O_TRUNC — raw fd, bypasses some layers
    
    Returns True if write succeeded, False if all attempts failed.
    """
    filepath = Path(filepath)
    
    # Ensure parent directory exists
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass

    # Attempt 1: standard pathlib write
    try:
        filepath.write_text(content)
        return True
    except (PermissionError, OSError):
        pass

    # Attempt 2: builtin open
    try:
        with open(str(filepath), 'w') as f:
            f.write(content)
        return True
    except (PermissionError, OSError):
        pass

    # Attempt 3: raw fd
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(filepath), flags, 0o644)
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
        return True
    except (PermissionError, OSError):
        pass

    return False


def safe_append_text(filepath, content: str) -> bool:
    """
    Append text to a file, with fallbacks for macOS quarantine restrictions.
    Returns True if write succeeded, False if all attempts failed.
    """
    filepath = Path(filepath)

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass

    try:
        with open(str(filepath), 'a') as f:
            f.write(content)
        return True
    except (PermissionError, OSError):
        pass

    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(str(filepath), flags, 0o644)
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
        return True
    except (PermissionError, OSError):
        pass

    return False
