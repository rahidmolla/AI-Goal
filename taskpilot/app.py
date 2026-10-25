import json
import os
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


def make_handler(data_dir, store, forge=None, orchestrator=None, auth=None):
    data_dir = Path(data_dir).resolve()
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    static = Path(__file__).resolve().parent.parent / "static"
    pending_overwrites = {}
    # Agent37 terminates HTTPS at the gateway. This exact deployment origin is
    # trusted explicitly; operators may override it with one configured origin.
    public_origin = os.environ.get("GOALFORGE_PUBLIC_ORIGIN", "https://41d7f17c26aa56d601e9.agent37.app").strip() or None
    if public_origin:
        try:
            parsed_public = urlsplit(public_origin)
            if (parsed_public.scheme.lower() not in {"http", "https"} or not parsed_public.hostname
                    or parsed_public.port is not None or parsed_public.path or parsed_public.query
                    or parsed_public.fragment or parsed_public.username is not None or parsed_public.password is not None):
                raise ValueError("GOALFORGE_PUBLIC_ORIGIN must be an origin only, without a path or port.")
            public_origin = f"{parsed_public.scheme.lower()}://{parsed_public.hostname.lower().rstrip('.')}"
        except ValueError as exc:
            raise ValueError("GOALFORGE_PUBLIC_ORIGIN is invalid.") from exc

    local_origins = {"http://localhost", "http://127.0.0.1", "http://[::1]"}

    class Handler(BaseHTTPRequestHandler):

        server_version = "TaskPilot/1.0"

        def log_message(self, fmt, *args):
            # Avoid copying task contents or potentially sensitive request data into logs.
            return

        def _session_token(self):
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                key, sep, value = part.strip().partition("=")
                if sep and key == "goalforge_session":
                    return value
            return ""

        def _owner_authorized(self):
            return auth is not None and auth.authorized(self._session_token())

        def _private_path(self, path):
            # Deny-by-default for application/private endpoints; only auth/bootstrap and health are public.
            return path.startswith("/api/") and path not in {"/api/health", "/api/auth/login", "/api/auth/session"}

        def _gate_workspace(self, path):
            return not self._private_path(path) or self._require_owner()

        def _unauthorized(self):
            status = 503 if auth is None or not auth.enabled else 401
            message = "Owner authentication is not configured; workspace is locked." if status == 503 else "Owner login required."
            self._json(status, {"error": message})
            return False

        def _require_owner(self):
            if self._owner_authorized():
                return True
            return self._unauthorized()

        def _serve_login_asset(self, name):
            if name not in {"login.js", "goalforge.css"}:
                self._json(404, {"error": "Not found."})
                return
            if name == "goalforge.css":
                return self._static("/static/goalforge.css")
            raw = (static / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _handle_auth(self, path, payload):
            if path == "/api/auth/login":
                if auth is None or not auth.enabled:
                    self._unauthorized()
                    return
                if not auth.verify_password(payload.get("password")):
                    self._json(401, {"error": "Invalid owner credentials."})
                    return
                token = auth.create_session()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Set-Cookie", f"goalforge_session={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={auth.SESSION_SECONDS}")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(b'{"authenticated":true}')
                return
            if path == "/api/auth/logout":
                if auth is not None:
                    auth.revoke(self._session_token())
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Set-Cookie", "goalforge_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0")
                self.end_headers()
                self.wfile.write(b'{"authenticated":false}')
                return
            self._json(404, {"error": "Not found."})

        def _serve_private_page(self):
            raw = (static / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _serve_login(self):
            page = static / "landing.html"
            raw = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def _page(self):
            if auth is not None and not self._owner_authorized():
                return self._serve_login()
            return self._serve_private_page()

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
            """Validate browser mutation Origin against operator-configured origins.

            Host and forwarded headers are diagnostic routing metadata, not trust
            anchors. Public proxy requests must match the exact configured origin;
            local requests may match their actual Host and scheme.
            """
            origin = self.headers.get("Origin")
            if origin is None:
                # Preserve command-line compatibility only when no browser session
                # is being used; cookies without an Origin are always rejected.
                if self._session_token():
                    self._json(403, {"error": "Missing request origin."})
                    return False
                return True
            try:
                parsed = urlsplit(origin)
                host = parsed.hostname
                port = parsed.port  # Reject malformed port / bracket syntax.
            except (TypeError, ValueError):
                self._json(403, {"error": "Untrusted request origin."})
                return False
            if (parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not host
                    or parsed.path or parsed.query or parsed.fragment or parsed.username is not None
                    or parsed.password is not None or parsed.netloc.endswith(":") or any(ch in origin for ch in (",", "\\", "%"))
                    or any(ch.isspace() or ord(ch) < 33 for ch in origin)):
                self._json(403, {"error": "Untrusted request origin."})
                return False
            scheme = parsed.scheme.lower()
            host = host.lower().rstrip(".")
            effective_port = port or (443 if scheme == "https" else 80)
            request_origin = (scheme, host, effective_port)

            def parse_allowed(value):
                try:
                    candidate = urlsplit(value)
                    candidate_host = candidate.hostname
                    candidate_port = candidate.port
                    if (candidate.scheme.lower() not in {"http", "https"} or not candidate.netloc or not candidate_host
                            or candidate.path or candidate.query or candidate.fragment
                            or candidate.username is not None or candidate.password is not None):
                        return None
                    candidate_scheme = candidate.scheme.lower()
                    return (candidate_scheme, candidate_host.lower().rstrip("."), candidate_port or (443 if candidate_scheme == "https" else 80))
                except (TypeError, ValueError):
                    return None

            allowed = {parse_allowed(public_origin)} - {None}
            allowed.update(parse_allowed(value) for value in local_origins)
            host_header = self.headers.get("Host", "")
            try:
                direct = urlsplit("//" + host_header)
                _ = direct.port
                direct_host = direct.hostname
                import ipaddress
                local_host = direct_host == "localhost" or (direct_host is not None and ipaddress.ip_address(direct_host).is_loopback)
                if local_host and direct_host:
                    allowed.add((scheme, direct_host.lower().rstrip("."), direct.port or (443 if scheme == "https" else 80)))
            except (ValueError, TypeError):
                pass
            if request_origin not in allowed:
                self._json(403, {"error": "Untrusted request origin."})
                return False
            return True

        def _goalforge_get(self, path):
            try:
                if path == "/api/goals":
                    self._json(200, {"goals": forge.goals()})
                elif path == "/api/memory":
                    self._json(200, forge.memory())
                elif path == "/api/activity":
                    self._json(200, {"activity": forge.timeline()})
                elif path == "/api/settings":
                    self._json(200, forge.settings())
                elif path == "/api/checkin":
                    self._json(200, {"checkin": forge.checkin(False)})
                else:
                    self._json(404, {"error": "Not found."})
            except Exception:
                self._json(500, {"error": "Could not load GoalForge data."})

        def do_GET(self):
            """Route all GETs, enforcing owner auth before private data dispatch."""
            path = urlsplit(self.path).path
            if path.startswith("/api/") and path not in {"/api/health", "/api/auth/session"}:
                if not self._require_owner():
                    return
            if path == "/api/auth/session":
                self._json(200, {"authenticated": self._owner_authorized(), "configured": bool(auth and auth.enabled)})
                return
            if path in {"/api/auth/login", "/api/auth/logout"}:
                self._json(405, {"error": "Method not allowed."})
                return
            if path == "/":
                try:
                    self._page()
                except OSError:
                    self._json(500, {"error": "UI unavailable."})
                return
            if path.startswith("/static/") and not self._owner_authorized():
                name = unquote(path.removeprefix("/static/"))
                return self._serve_login_asset(name)
            if path == "/favicon.ico" and not self._owner_authorized():
                self._json(404, {"error": "Not found."})
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # Existing GET API dispatch continues below; all private API paths
            # passed through _require_owner above.
            if path == "/api/health":
                self._json(200, {"status": "ok", "name": "GoalForge", "ai_provider": "deterministic fallback", "database": "SQLite"})
            elif path.startswith("/api/") and forge is not None and path not in {"/api/tasks", "/api/demo"} and not re.fullmatch(r"/api/tasks/[0-9a-f]{32}", path):
                self._goalforge_get(path)
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
            if name not in {"app.js", "styles.css", "goalforge.css", "arch.svg"}:
                self._json(404, {"error": "Not found."})
                return
            target = static / name
            try:
                raw = target.read_bytes()
            except OSError:
                self._json(404, {"error": "Not found."})
                return
            mime = "text/javascript; charset=utf-8" if name.endswith(".js") else ("image/svg+xml" if name.endswith(".svg") else "text/css; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _goalforge_post(self, path, payload):
            try:
                if path == "/api/goals":
                    self._json(201, {"goal": forge.create_goal(payload)})
                elif path == "/api/plan-goal":
                    title = payload.get("title")
                    if not isinstance(title, str) or not title.strip():
                        raise ValueError("Enter a goal to plan.")
                    lessons = forge.memory()["lessons"]
                    relevant = [x["text"] for x in lessons if x["verified"] and any(w.lower() in x["text"].lower() for w in title.split() if len(w) > 4)][:3]
                    plan = {"source": "deterministic fallback", "title": title.strip(), "milestones": [{"title": "Prepare", "tasks": [{"title": f"Define success criteria for {title.strip()}", "minutes": 15, "priority": "high", "verification": "Outcome and done criteria are written down.", "dependencies": []}, {"title": "Complete the first focused work block", "minutes": max(15, min(60, payload.get("available_minutes", 60))), "priority": "high", "verification": "A concrete deliverable exists for review.", "dependencies": []}]}, {"title": "Verify and adapt", "tasks": [{"title": "Review the deliverable against the target outcome", "minutes": 20, "priority": "medium", "verification": "Compare the artifact to the stated success criteria.", "dependencies": [0]}, {"title": "Capture evidence and next step", "minutes": 10, "priority": "medium", "verification": "Evidence and one next action are recorded.", "dependencies": [1]}]}], "lessons_used": relevant, "ai_available": False}
                    if payload.get("save") is True:
                        goal = forge.create_goal({"title": title.strip(), "description": "Generated structured plan using " + plan["source"], "target": title.strip(), "deadline": payload.get("deadline") or None, "available_minutes": max(1, min(1440, int(payload.get("available_minutes", 60))))})
                        task_ids = []
                        for i, milestone in enumerate(plan["milestones"]):
                            saved = forge.add_milestone(goal["id"], milestone["title"], i)
                            for item in milestone["tasks"]:
                                dependencies = [task_ids[n] for n in item.get("dependencies", []) if isinstance(n, int) and 0 <= n < len(task_ids)]
                                created = forge.add_task(goal["id"], {"title": item["title"], "description": milestone["title"], "minutes": item["minutes"], "priority": item["priority"], "verification": item["verification"], "milestone_id": saved["id"], "dependencies": dependencies})
                                task_ids.append(created["id"])
                        self._json(201, {"plan": plan, "saved": True, "goal": next(g for g in forge.goals() if g["id"] == goal["id"])})
                    else:
                        self._json(200, {"plan": plan, "saved": False})
                elif re.fullmatch(r"/api/runs/[0-9a-f]{32}/cancel", path) and orchestrator is not None:
                    self._json(200, {"cancelled": orchestrator.cancel_run(path.split("/")[-2])})
                elif re.fullmatch(r"/api/runs/[0-9a-f]{32}", path) and orchestrator is not None:
                    self._json(200, orchestrator.run_adaptive_demo(path.rsplit("/", 1)[-1]))
                elif re.fullmatch(r"/api/goals/[0-9a-f]{32}/execute-next", path) and orchestrator is not None:
                    self._json(200, orchestrator.execute_next(path.split("/")[-2], payload.get("run_id")))
                elif path == "/api/demo":
                    if payload.get("adaptive") is True:
                        if payload.get("reset") is True:
                            self._json(200, forge.reset_adaptive_demo())
                        elif orchestrator is not None:
                            self._json(200, orchestrator.run_adaptive_demo(payload.get("run_id")))
                        else:
                            self._json(200, forge.adaptive_demo())
                    elif payload.get("cancel_run_id") and orchestrator is not None:
                        self._json(200, {"cancelled": orchestrator.cancel_run(payload.get("cancel_run_id"))})
                    else:
                        self._json(200, forge.demo(bool(payload.get("reset", False))))

                elif path == "/api/checkin":
                    self._json(200, {"checkin": forge.checkin(bool(payload.get("accept", False)))})
                elif path == "/api/settings":
                    self._json(200, {"settings": forge.settings(payload)})
                elif path == "/api/lessons":
                    self._json(201, {"id": forge.lesson(payload.get("text"), payload.get("source", "user"), payload.get("confidence", 0.6), payload.get("verified", False))})
                elif re.fullmatch(r"/api/lessons/[0-9a-f]{32}", path):
                    if not forge.delete_lesson(path.rsplit("/", 1)[-1]):
                        self._json(404, {"error": "Lesson not found."})
                    else:
                        self._json(200, {"deleted": True})
                elif re.fullmatch(r"/api/goals/[0-9a-f]{32}", path):
                    goal = forge.update_goal(path.rsplit("/", 1)[-1], payload)
                    self._json(200, {"goal": goal})
                elif re.fullmatch(r"/api/goals/[0-9a-f]{32}/tasks", path):
                    self._json(201, {"task": forge.add_task(path.split("/")[-2], payload)})
                elif re.fullmatch(r"/api/tasks/[0-9a-f]{32}", path):
                    self._json(200, {"task": forge.update_task(path.rsplit("/", 1)[-1], payload)})
                else:
                    self._json(404, {"error": "Not found."})
            except KeyError:
                self._json(404, {"error": "Record not found."})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(500, {"error": "GoalForge request failed safely."})

        def do_POST(self):
            path = urlsplit(self.path).path
            # Login is the only public POST; all other operations require owner auth.
            if path != "/api/auth/login" and not self._require_owner():
                return
            if path in {"/api/auth/login", "/api/auth/logout"}:
                try:
                    payload = self._body()
                except (ValueError, TypeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                if not self._check_origin():
                    return
                if path == "/api/auth/logout" and not self._require_owner():
                    return
                self._handle_auth(path, payload)
                return
            try:
                payload = self._body()
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            # Missing Origin is rejected for browser-cookie workspace operations,
            # and all present origins pass the explicit allowlist check.
            if not self._check_origin():
                return
            if not self._require_owner():
                return
            if path not in {"/api/plan", "/api/tasks", "/api/auth/login", "/api/auth/logout"} and not path.startswith("/api/"):
                self._json(404, {"error": "Not found."})
                return
            if urlsplit(self.path).query and (path in {"/api/plan", "/api/tasks"} or re.fullmatch(r"/api/tasks/[0-9a-f]{32}/approve", path)):
                self._json(400, {"error": "Query parameters are not accepted on task submission routes."})
                return
            if forge is not None and path.startswith("/api/"):
                self._goalforge_post(path, payload)
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

        def do_DELETE(self):
            if not self._require_owner():
                return
            if not self._check_origin():
                return
            self._json(405, {"error": "Method not allowed."})

        def do_OPTIONS(self):
            self._json(405, {"error": "Method not allowed."})

    return Handler