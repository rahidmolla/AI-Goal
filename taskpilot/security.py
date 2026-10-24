import os
import re
from pathlib import Path

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


def resolve_path(root, relative_path):
    """Resolve a relative workspace path and reject symlinks/hard links."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("A relative workspace path is required.")
    if "\\" in relative_path or relative_path.startswith("~") or _WINDOWS_ABSOLUTE.match(relative_path):
        raise ValueError("Only normalized, relative POSIX workspace paths are allowed.")
    path = Path(relative_path)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Absolute paths and dot-segments are not allowed.")
    base = Path(root).resolve(strict=True)
    candidate = base / path
    # Do not allow existing symlink components, even if they resolve back inside.
    current = base
    for part in path.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed in workspace paths.")
        except OSError as exc:
            raise ValueError("Could not inspect workspace path.") from exc
    try:
        candidate.resolve(strict=False).relative_to(base)
    except ValueError as exc:
        raise ValueError("Path escapes the TaskPilot workspace.") from exc
    if candidate == base:
        raise ValueError("The workspace root itself is not a file.")
    # Existing hard-linked files can alias another path outside the sandbox.
    try:
        if candidate.exists() and os.stat(candidate, follow_symlinks=False).st_nlink > 1:
            raise ValueError("Hard-linked files are not allowed in workspace paths.")
    except OSError as exc:
        raise ValueError("Could not inspect workspace file.") from exc
    return candidate
