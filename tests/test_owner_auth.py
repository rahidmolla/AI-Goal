import http.cookiejar
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from unittest.mock import patch

from taskpilot.app import make_handler
from taskpilot.goalforge import GoalForgeStore
from taskpilot.orchestrator import Orchestrator
from taskpilot.store import TaskStore
from taskpilot.auth import OwnerAuth

OWNER_PASSWORD = "unit-test-owner-password-not-for-production"
SESSION_SECRET = "unit-test-session-secret-0123456789abcdef"
PUBLIC_ORIGIN = "https://41d7f17c26aa56d601e9.agent37.app"


class OwnerAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.owner_env = patch.dict(os.environ, {"GOALFORGE_OWNER_PASSWORD": OWNER_PASSWORD,
                                                "GOALFORGE_SESSION_SECRET": SESSION_SECRET,
                                                "GOALFORGE_PUBLIC_ORIGIN": PUBLIC_ORIGIN})
        self.owner_env.start()
        self.forge = GoalForgeStore(self.root / "forge.sqlite3", self.root / "workspace")
        self.auth = OwnerAuth(self.root / "forge.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            self.root, TaskStore(self.root / "tasks.sqlite3"), self.forge, Orchestrator(self.forge), self.auth))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.cookies = http.cookiejar.CookieJar()
        self.client = build_opener(HTTPCookieProcessor(self.cookies))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.owner_env.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, origin=None, client=None):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin is not None:
            headers["Origin"] = origin
        cookie = next((item.value for item in self.cookies if item.name == "goalforge_session"), None)
        if client is not None and cookie:
            headers["Cookie"] = f"goalforge_session={cookie}"
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      method=method, headers=headers)
        return client.open(req) if client else urlopen(req)

    def login(self, password=OWNER_PASSWORD, origin=None):
        return self.request("/api/auth/login", "POST", {"password": password},
                            origin or f"http://127.0.0.1:{self.server.server_port}", self.client)

    def test_anonymous_cannot_read_workspace_or_change_state(self):
        private_gets = ("/api/goals", "/api/memory", "/api/activity", "/api/settings", "/api/checkin", "/api/tasks")
        for asset in ("/static/app.js", "/static/styles.css", "/static/arch.svg"):
            with self.subTest(asset=asset), self.assertRaises(HTTPError) as error:
                self.request(asset)
            self.assertEqual(error.exception.code, 404)
        for path in private_gets:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 401)
        mutations = (("/api/goals", {"title": "Anonymous"}),
                     ("/api/demo", {"adaptive": True}),
                     ("/api/demo", {"adaptive": True, "reset": True}),
                     ("/api/settings", {"daily_minutes": 1}),
                     ("/api/lessons", {"text": "anonymous"}))
        for path, body in mutations:
            with self.subTest(path=path, body=body), self.assertRaises(HTTPError) as error:
                self.request(path, "POST", body, f"http://127.0.0.1:{self.server.server_port}")
            self.assertEqual(error.exception.code, 401)
        self.assertEqual(self.forge.goals(), [])
        self.assertEqual(self.forge.memory()["lessons"], [])

    def test_public_root_is_landing_not_private_app_until_login(self):
        with urlopen(self.base + "/") as response:
            page = response.read().decode()
        self.assertIn("GoalForge", page)
        self.assertIn("id=\"login-form\"", page)
        self.assertNotIn("id=\"view-dashboard\"", page)

    def test_owner_login_grants_access_and_logout_revokes_it(self):
        with self.login() as response:
            self.assertEqual(response.status, 200)
        goals_url=self.base+"/api/goals"
        cookie=next(item.value for item in self.cookies if item.name=="goalforge_session")
        with urlopen(Request(goals_url,headers={"Cookie":f"goalforge_session={cookie}"})) as response:
            self.assertEqual(json.loads(response.read())["goals"], [])
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/not-a-route")
        self.assertEqual(error.exception.code,401)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/not-a-route", headers={"Cookie":f"goalforge_session={cookie}"}))
        self.assertEqual(error.exception.code,404)
        request=Request(self.base+"/api/auth/logout",data=b'{}',method="POST",headers={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{self.server.server_port}","Cookie":f"goalforge_session={cookie}"})
        with urlopen(request) as response:
            self.assertEqual(response.status,200)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(goals_url,headers={"Cookie":f"goalforge_session={cookie}"}))
        self.assertEqual(error.exception.code,401)

    def test_authenticated_mutation_rejects_missing_origin(self):
        token=self.auth.create_session()
        req=Request(self.base+"/api/goals",data=b'{"title":"blocked"}',method="POST",headers={"Content-Type":"application/json","Cookie":f"goalforge_session={token}"})
        with self.assertRaises(HTTPError) as error:
            urlopen(req)
        self.assertEqual(error.exception.code,403)

    def test_bad_password_and_foreign_origin_cannot_authenticate(self):
        with self.assertRaises(HTTPError) as error:
            self.login("wrong-password")
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.login(origin="https://evil.example")
        self.assertEqual(error.exception.code, 403)

    def test_cookie_mutations_require_valid_origin_and_keep_approval_gate(self):
        self.login().close()
        cookie=next(item.value for item in self.cookies if item.name=="goalforge_session")
        request=Request(self.base+"/api/goals",data=json.dumps({"title":"CSRF"}).encode(),method="POST",headers={"Content-Type":"application/json","Origin":"https://evil.example","Cookie":f"goalforge_session={cookie}"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code,403)
        with self.request("/api/goals", "POST", {"title": "Owner goal"}, f"http://127.0.0.1:{self.server.server_port}", self.client) as response:
            self.assertEqual(response.status, 201)
        self.assertEqual(len(self.forge.goals()), 1)

    def test_authentication_unavailable_fails_closed_without_owner_configuration(self):
        with patch.dict(os.environ, {"GOALFORGE_OWNER_PASSWORD": "", "GOALFORGE_SESSION_SECRET": ""}):
            auth = OwnerAuth(self.root / "other.sqlite3")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.root, TaskStore(self.root / "other.sqlite3"), self.forge, auth=auth))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(url + "/") as response:
                    self.assertIn("GoalForge", response.read().decode())
                req = Request(url + "/api/auth/login", data=b'{"password":"guess"}', method="POST",
                              headers={"Content-Type": "application/json", "Origin": url})
                with self.assertRaises(HTTPError) as error:
                    urlopen(req)
                self.assertEqual(error.exception.code, 503)
                req = Request(url + "/api/goals")
                with self.assertRaises(HTTPError) as error:
                    urlopen(req)
                self.assertEqual(error.exception.code, 503)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
