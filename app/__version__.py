"""
Version information for MMM Trainer application.

This module reads the version from the VERSION file at the repository root.
"""

import os
from pathlib import Path

# Read version from VERSION file
_version_file = Path(__file__).parent.parent / "VERSION"
if _version_file.exists():
    __version__ = _version_file.read_text().strip()
else:
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
