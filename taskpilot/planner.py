import re


_CREATE = re.compile(r"^create\s+file\s+(.+?)\s+with\s+content:\s*(.*)$", re.IGNORECASE | re.DOTALL)
_READ = re.compile(r"^read\s+file\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)
_DELETE = re.compile(r"^delete\s+file\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)


def plan_request(request):
    """Translate a deliberately small, auditable file-task grammar into a plan."""
    if not isinstance(request, str) or not request.strip():
        return {"status": "invalid", "summary": "Enter a file task to get started.", "actions": [], "requires_approval": False}
    if len(request) > 12000:
        return {"status": "invalid", "summary": "Task is too long (maximum 12,000 characters).", "actions": [], "requires_approval": False}
    text = request.strip()
    for pattern, operation in ((_CREATE, "create_file"), (_READ, "read_file"), (_DELETE, "delete_file")):
        match = pattern.fullmatch(text)
        if not match:
            continue
        path = match.group(1).strip()
        action = {"operation": operation, "path": path}
        if operation == "create_file":
            action["content"] = match.group(2)
        approval = operation == "delete_file"
        summary = {"create_file": f"Create {path}", "read_file": f"Read {path}", "delete_file": f"Delete {path}"}[operation]
        return {"status": "ready", "summary": summary, "actions": [action], "requires_approval": approval}
    return {"status": "invalid", "summary": "I can plan only: ‘Create file PATH with content: TEXT’, ‘Read file PATH’, or ‘Delete file PATH’. No action was taken.", "actions": [], "requires_approval": False}
