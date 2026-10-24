import json
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .executor import execute_plan
from .planner import plan_request


MAX_BODY_BYTES = 32_000


def _history_safe(plan, result=None):
    """Keep task history useful without retaining request/file contents."""
    operations = {"create_file": "Create", "read_file": "Read", "delete_file": "Delete"}
    actions = []
    for action in plan.get("actions", []):
        safe_action = {"operation": action.get("operation"), "path": action.get("path")}
        actions.append(safe_action)
    safe_plan = {**plan, "actions": actions}
    safe_plan["summary"] = "Supported file task (details withheld)" if actions else "Unsupported or invalid request (details withheld)"
    safe_plan["actions"] = [{"operation": action.get("operation")} for action in actions]
    safe_request = f"{operations.get(actions[0]['operation'], 'Task')} file task (details withheld)" if actions else "Unsupported or invalid request (details withheld)"
    safe_result = None
    if result is not None:
        safe_result = {"status": result.get("status"), "summary": "Task result (details withheld)", "evidence": [{"verified": item.get("verified"), "error": "Operation failed (details withheld)" if item.get("error") else None} for item in result.get("evidence", [])]}
        safe_result["evidence"] = [{key: value for key, value in item.items() if value is not None} for item in safe_result["evidence"]]
    return safe_request, safe_plan, safe_result


def make_handler(data_dir, store):
    data_dir = Path(data_dir).resolve()
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    static = Path(__file__).resolve().parent.parent / "static"
    pending_overwrites = {}

    class Handler(BaseHTTPRequestHandler):

        server_version = "TaskPilot/1.0"

        def log_message(self, fmt, *args):
            # Avoid copying task contents or potentially sensitive request data into logs.
            return

        def _json(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _page(self):
            page = static / "index.html"
            raw = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            if "Transfer-Encoding" in self.headers:
                raise ValueError("Chunked request bodies are not supported.")
            if "Content-Length" not in self.headers:
                raise ValueError("Content-Length is required.")
            try:
                length = int(self.headers["Content-Length"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid Content-Length.") from exc
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("Request body must be between 1 byte and 32 KB.")
            raw = self.rfile.read(length)
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise ValueError("Send a JSON request body.")
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("Invalid JSON request body.") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object.")
            return data

        def _check_origin(self):
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != self.headers.get("Host", "").lower():
                    self._json(403, {"error": "Untrusted request origin."})
                    return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                try:
                    self._page()
                except OSError:
                    self._json(500, {"error": "UI unavailable."})
            elif path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/api/health":
                self._json(200, {"status": "ok", "name": "TaskPilot"})
            elif path == "/api/tasks":
                safe_tasks = []
                for task in store.list():
                    if task["status"] == "approval_required":
                        pending = pending_overwrites.get(task["id"])
                        if pending:
                            safe_request, safe_plan, _ = _history_safe(pending)
                            task["request"] = f"{safe_request} (awaiting explicit approval)"
                            task["plan"] = safe_plan
                    safe_tasks.append(task)
                self._json(200, {"tasks": safe_tasks})
            elif re.fullmatch(r"/api/tasks/[0-9a-f]{32}", path):
                task_id = path.rsplit("/", 1)[-1]
                record = store.get(task_id)
                if record:
                    self._json(200, record)
                else:
                    self._json(404, {"error": "Task not found."})
            elif path.startswith("/static/"):
                self._static(path)
            else:
                self._json(404, {"error": "Not found."})

        def _static(self, path):
            name = unquote(path.removeprefix("/static/"))
            if name not in {"app.js", "styles.css"}:
                self._json(404, {"error": "Not found."})
                return
            target = static / name
            try:
                raw = target.read_bytes()
            except OSError:
                self._json(404, {"error": "Not found."})
                return
            mime = "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if not self._check_origin():
                return
            path = urlsplit(self.path).path
            try:
                payload = self._body()
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            if urlsplit(self.path).query and (path in {"/api/plan", "/api/tasks"} or re.fullmatch(r"/api/tasks/[0-9a-f]{32}/approve", path)):
                self._json(400, {"error": "Query parameters are not accepted on task submission routes."})
                return
            if path == "/api/plan":
                request = payload.get("request")
                if not isinstance(request, str):
                    self._json(400, {"error": "‘request’ must be a string."})
                    return
                self._json(200, plan_request(request))
                return
            if path == "/api/tasks":
                request = payload.get("request")
                if not isinstance(request, str):
                    self._json(400, {"error": "‘request’ must be a string."})
                    return
                plan = plan_request(request)
                if plan["status"] != "ready":
                    safe_request, safe_plan, safe_result = _history_safe(plan, {"status": "failed", "summary": plan["summary"], "evidence": []})
                    record = store.create(safe_request, safe_plan)
                    store.update(record["id"], "failed", safe_result)
                    self._json(422, {**store.get(record["id"]), "request": request[:12000], "plan": plan, "result": safe_result, **safe_result})
                    return
                safe_request, safe_plan, _ = _history_safe(plan)
                record = store.create(safe_request, safe_plan)
                result = execute_plan(workspace, plan, approved=False)
                safe_request, safe_plan, safe_result = _history_safe(plan, result)
                if result["status"] == "approval_required":
                    pending_overwrites[record["id"]] = plan
                    safe_request = f"{safe_request} (awaiting explicit approval)"
                store.update(record["id"], result["status"], safe_result)
                status = 202 if result["status"] == "approval_required" else 200
                self._json(status, {**store.get(record["id"]), "request": request, "plan": plan, "result": result, **result})
                return
            match = re.fullmatch(r"/api/tasks/([0-9a-f]{32})/approve", path)
            if match:
                task_id = match.group(1)
                record = store.get(task_id)
                if not record:
                    self._json(404, {"error": "Task not found."})
                    return
                if record["status"] != "approval_required":
                    self._json(409, {"error": "This task is not awaiting approval."})
                    return
                if payload.get("approve") is not True:
                    self._json(400, {"error": "Explicit approval is required; send {\"approve\": true}."})
                    return
                approved_plan = pending_overwrites.get(task_id)
                if not approved_plan:
                    self._json(409, {"error": "This approval is no longer available in this server session. Inspect the file and submit a new task."})
                    return
                operation = approved_plan.get("actions", [{}])[0].get("operation")
                if operation == "create_file":
                    try:
                        from .security import resolve_path
                        candidate = resolve_path(workspace, approved_plan["actions"][0]["path"])
                        if not candidate.exists():
                            result = {"status": "failed", "summary": "The existing file disappeared while waiting for approval. Approval was not applied to create a new file.", "evidence": [{"path": approved_plan["actions"][0]["path"], "error": "Target no longer exists."}]}
                            _, _, safe_result = _history_safe(approved_plan, result)
                            store.update(task_id, "failed", safe_result)
                            pending_overwrites.pop(task_id, None)
                            self._json(409, {**store.get(task_id), **result})
                            return
                    except (ValueError, OSError, IndexError) as exc:
                        result = {"status": "failed", "summary": str(exc), "evidence": []}
                        _, _, safe_result = _history_safe(approved_plan, result)
                        store.update(task_id, "failed", safe_result)
                        pending_overwrites.pop(task_id, None)
                        self._json(409, {**store.get(task_id), **result})
                        return
                result = execute_plan(workspace, approved_plan, approved=True)
                _, _, safe_result = _history_safe(approved_plan, result)
                store.update(task_id, result["status"], safe_result)
                pending_overwrites.pop(task_id, None)
                self._json(200, {**store.get(task_id), "plan": approved_plan, "result": result, **result})
                return
            self._json(404, {"error": "Not found."})

        def do_OPTIONS(self):
            self._json(405, {"error": "Method not allowed."})

    return Handler
