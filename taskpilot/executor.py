import os
import re
from pathlib import Path


MAX_FILE_BYTES = 1_000_000
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


def _safe_parts(root, relative_path):
    if not isinstance(relative_path, str) or not relative_path.strip() or "\\" in relative_path or relative_path.startswith("~") or _WINDOWS_ABSOLUTE.match(relative_path):
        raise ValueError("Only normalized relative POSIX workspace paths are allowed.")
    path = Path(relative_path)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Absolute paths and dot-segments are not allowed.")
    root = Path(root).resolve(strict=True)
    current = root
    for part in path.parts[:-1]:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        if not current.is_dir() or current.is_symlink():
            raise ValueError("Workspace path contains a link or non-directory component.")
    target = root / path
    try:
        stat = target.lstat()
    except FileNotFoundError:
        stat = None
    if stat is not None and (target.is_symlink() or stat.st_nlink > 1):
        raise ValueError("Symlink and hard-linked files are not allowed.")
    return target


def _no_follow_target(root, relative_path):
    return _safe_parts(root, relative_path)


def _create_parents_safely(root, parent):
    root = Path(root).resolve(strict=True)
    relative = Path(parent).relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ValueError("Could not safely create workspace directories.") from exc
        if current.is_symlink() or not current.is_dir():
            raise ValueError("Workspace path contains a link or non-directory component.")


def execute_plan(workspace, plan, approved=False):
    """Execute only recognized single-file actions and verify their outcomes."""
    if not isinstance(plan, dict) or plan.get("status") != "ready" or not isinstance(plan.get("actions"), list) or len(plan["actions"]) != 1:
        return {"status": "failed", "summary": "Invalid or unsupported plan; no action taken.", "evidence": []}
    action = plan["actions"][0]
    op = action.get("operation")
    path_text = action.get("path")
    if op not in {"create_file", "read_file", "delete_file"}:
        return {"status": "failed", "summary": "Unsupported operation; no action taken.", "evidence": []}
    if op == "delete_file" and not approved:
        return {"status": "approval_required", "summary": "Deletion requires explicit approval.", "evidence": [{"path": path_text, "check": "No file was deleted; approval is pending."}]}
    try:
        root = Path(workspace).resolve(strict=True)
        if op == "create_file":
            content = action.get("content")
            if not isinstance(content, str):
                raise ValueError("File content must be text.")
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError("File content exceeds the 1 MB limit.")
            # Inspect each existing component with lstat and do not follow links.
            target = _no_follow_target(root, path_text)
            if target.exists() and not approved:
                return {"status": "approval_required", "summary": "This file already exists; overwriting requires explicit approval.", "evidence": [{"path": path_text, "check": "Existing file preserved; approval is pending."}]}
            if target.exists() and not target.is_file():
                raise ValueError("Only regular files may be overwritten.")
            _create_parents_safely(root, target.parent)
            target = _no_follow_target(root, path_text)
            if approved and not target.exists():
                raise FileNotFoundError("The file awaiting overwrite approval no longer exists.")
            flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            flags |= os.O_TRUNC if approved else os.O_EXCL
            try:
                fd = os.open(target, flags, 0o600)
            except FileExistsError:
                return {"status": "approval_required", "summary": "This file appeared after planning; overwriting requires a new approval.", "evidence": [{"path": path_text, "check": "Existing file was not changed; approval is pending."}]}
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if os.stat(target, follow_symlinks=False).st_nlink > 1:
                target.unlink(missing_ok=True)
                raise ValueError("Hard-linked files are not allowed.")
            verified = target.is_file() and target.read_bytes() == encoded
            if not verified:
                return {"status": "unverified", "summary": "Write returned, but content verification did not match.", "evidence": [{"path": path_text, "check": "Expected content to match actual file bytes.", "verified": False}]}
            return {"status": "completed", "summary": f"Created and verified {path_text}.", "evidence": [{"path": path_text, "check": f"File exists; UTF-8 byte count {len(encoded)}; written bytes match.", "verified": True}]}
        target = _no_follow_target(root, path_text)
        if not target.is_file():
            raise FileNotFoundError(f"Not a regular file: {path_text}")
        if op == "read_file":
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags)
            with os.fdopen(fd, "rb") as handle:
                stat = os.fstat(handle.fileno())
                if not __import__("stat").S_ISREG(stat.st_mode) or stat.st_nlink > 1:
                    raise ValueError("File changed to an unsafe link or is not a regular file.")
                raw = handle.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("File exceeds the 1 MB read limit.")
            content = raw.decode("utf-8")
            return {"status": "completed", "summary": f"Read and verified {path_text}.", "evidence": [{"path": path_text, "check": f"Read {len(raw)} bytes and verified UTF-8 decoding.", "verified": True, "content": content}]}
        os.unlink(target)
        verified = not target.exists()
        return {"status": "completed" if verified else "unverified", "summary": f"{'Removed and verified' if verified else 'Removal unverified'} {path_text}.", "evidence": [{"path": path_text, "check": "File no longer exists." if verified else "File still exists after removal.", "verified": verified}]}
    except FileNotFoundError as exc:
        return {"status": "failed", "summary": str(exc), "evidence": [{"path": path_text, "error": "File not found."}]}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"status": "failed", "summary": str(exc), "evidence": [{"path": path_text, "error": str(exc)}]}
