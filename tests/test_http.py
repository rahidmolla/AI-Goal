import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from taskpilot.app import make_handler
from taskpilot.store import TaskStore


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.root, TaskStore(self.root / "tasks.sqlite3")))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.base + path, data=body, method=method, headers={"Content-Type": "application/json"})
        with urlopen(req) as response:
            return response.status, json.loads(response.read())

    def test_public_warning_markup_is_not_forced_on_localhost(self):
        with urlopen(self.base + "/") as response:
            page = response.read().decode()
        self.assertIn('id="public-warning" hidden', page)

    def test_static_client_shows_public_warning_for_agent37_domains(self):
        with urlopen(self.base + "/static/app.js") as response:
            script = response.read().decode()
        self.assertIn("window.location.hostname.endsWith('.agent37.app')", script)

    def test_health_and_create_approval_flow(self):
        status, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        _, planned = self.request("/api/plan", "POST", {"request": "Create file a.txt with content: hello"})
        self.assertEqual(planned["status"], "ready")
        _, created = self.request("/api/tasks", "POST", {"request": "Create file a.txt with content: hello"})
        self.assertEqual(created["status"], "completed")
        self.assertEqual((self.root / "workspace/a.txt").read_text(), "hello")

    def test_overwrite_is_not_performed_without_approval(self):
        (self.root / "workspace").mkdir(exist_ok=True)
        (self.root / "workspace/a.txt").write_text("old")
        _, created = self.request("/api/tasks", "POST", {"request": "Create file a.txt with content: new"})
        self.assertEqual(created["status"], "approval_required")
        self.assertEqual((self.root / "workspace/a.txt").read_text(), "old")
        _, approved = self.request(f"/api/tasks/{created['id']}/approve", "POST", {"approve": True})
        self.assertEqual(approved["status"], "completed")
        self.assertEqual((self.root / "workspace/a.txt").read_text(), "new")

    def test_rejects_untrusted_origins(self):
        body = json.dumps({"request": "Read file a.txt"}).encode()
        req = Request(self.base + "/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_allows_same_origin_post_through_agent37_reverse_proxy(self):
        body = json.dumps({"request": "Read file missing.txt"}).encode()
        req = Request(self.base + "/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": "https://taskpilot.example", "X-Forwarded-Host": "taskpilot.example", "X-Forwarded-Proto": "https"})
        with urlopen(req) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "failed")

    def test_rejects_malformed_origin_even_when_hostname_matches_proxy_host(self):
        for origin in ("https://taskpilot.example/path", "https://taskpilot.example?query", "https://user@taskpilot.example", "null", "https://taskpilot%2eexample", "https://taskpilot.example:99999"):
            with self.subTest(origin=origin):
                body = json.dumps({"request": "Read file missing.txt"}).encode()
                req = Request(self.base + "/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": origin, "X-Forwarded-Host": "taskpilot.example", "X-Forwarded-Proto": "https"})
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req)
                self.assertEqual(ctx.exception.code, 403)

    def test_rejects_bad_proxy_host_or_mismatched_proxy_scheme(self):
        cases = [("taskpilot.example,evil.example", "https"), ("taskpilot.example/path", "https"), ("taskpilot.example:443", "https"), ("https://taskpilot.example", "https"), ("taskpilot.example", "http"), ("taskpilot.example", "https,http")]
        for host, proto in cases:
            with self.subTest(host=host, proto=proto):
                body = json.dumps({"request": "Read file missing.txt"}).encode()
                req = Request(self.base + "/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": "https://taskpilot.example", "X-Forwarded-Host": host, "X-Forwarded-Proto": proto})
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req)
                self.assertEqual(ctx.exception.code, 403)

    def test_approved_same_origin_creation_runs_through_proxy_headers(self):
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "a.txt").write_text("before")
        _, pending = self.request("/api/tasks", "POST", {"request": "Create file a.txt with content: after"})
        request = Request(self.base + f"/api/tasks/{pending['id']}/approve", data=json.dumps({"approve": True}).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": "https://taskpilot.example", "X-Forwarded-Host": "taskpilot.example", "X-Forwarded-Proto": "https"})
        with urlopen(request) as response:
            result = json.loads(response.read())
        self.assertEqual(result["status"], "completed")
        self.assertEqual((workspace / "a.txt").read_text(), "after")

    def test_untrusted_proxy_origin_cannot_approve_pending_action(self):
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        target = workspace / "a.txt"
        target.write_text("before")
        _, pending = self.request("/api/tasks", "POST", {"request": "Create file a.txt with content: after"})
        request = Request(self.base + f"/api/tasks/{pending['id']}/approve", data=json.dumps({"approve": True}).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": "https://evil.example", "X-Forwarded-Host": "taskpilot.example", "X-Forwarded-Proto": "https"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(request)
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(target.read_text(), "before")

    def test_approval_cannot_turn_a_stale_overwrite_into_create(self):
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        target = workspace / "a.txt"
        target.write_text("original")
        _, task = self.request("/api/tasks", "POST", {"request": "Create file a.txt with content: new"})
        self.assertEqual(task["status"], "approval_required")
        target.unlink()
        with self.assertRaises(HTTPError) as ctx:
            request = Request(self.base + f"/api/tasks/{task['id']}/approve", data=json.dumps({"approve": True}).encode(), method="POST", headers={"Content-Type": "application/json"})
            urlopen(request)
        self.assertEqual(ctx.exception.code, 409)
        self.assertFalse(target.exists())

    def test_task_history_does_not_persist_file_contents_or_paths(self):
        secret = "demo-secret-abc123"
        request = {"request": f"Create file private-name.txt with content: {secret}"}
        _, created = self.request("/api/tasks", "POST", request)
        self.assertEqual(created["status"], "completed")
        db_bytes = (self.root / "tasks.sqlite3").read_bytes()
        self.assertNotIn(secret.encode(), db_bytes)
        self.assertNotIn(b"private-name.txt", db_bytes)
        _, history = self.request("/api/tasks")
        self.assertNotIn(secret, json.dumps(history))
        self.assertNotIn("private-name.txt", json.dumps(history))
        self.assertNotIn("private-name.txt", (self.root / "tasks.sqlite3").read_bytes().decode("latin1"))

    def test_read_result_is_returned_live_but_not_persisted_in_history(self):
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "private.txt").write_text("private-secret-xyz789")
        _, result = self.request("/api/tasks", "POST", {"request": "Read file private.txt"})
        self.assertEqual(result["result"]["evidence"][0]["content"], "private-secret-xyz789")
        db_bytes = (self.root / "tasks.sqlite3").read_bytes()
        self.assertNotIn(b"private-secret-xyz789", db_bytes)
        _, history = self.request("/api/tasks")
        self.assertNotIn("private-secret-xyz789", json.dumps(history))

    def test_rejects_query_params_for_all_mutating_api_routes(self):
        for path, payload in (
            ("/api/plan?request=Read%20file%20x", {"request": "Read file absent.txt"}),
            ("/api/tasks?request=Delete%20file%20x", {"request": "Create file a.txt with content: blocked"}),
        ):
            with self.subTest(path=path):
                body = json.dumps(payload).encode()
                req = Request(self.base + path, data=body, method="POST", headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req)
                self.assertEqual(ctx.exception.code, 400)
        self.assertFalse((self.root / "workspace/a.txt").exists())

    def test_rejects_invalid_and_oversized_requests(self):
        body = json.dumps({"request": 5}).encode()
        req = Request(self.base + "/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        req = Request(self.base + "/api/plan", data=b"{}", method="POST", headers={"Content-Type": "text/plain"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
