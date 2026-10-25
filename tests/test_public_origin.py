import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from taskpilot.app import make_handler
from taskpilot.goalforge import GoalForgeStore
from taskpilot.orchestrator import Orchestrator
from taskpilot.auth import OwnerAuth
from taskpilot.store import TaskStore

PUBLIC = "https://41d7f17c26aa56d601e9.agent37.app"


class PublicOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.forge = GoalForgeStore(self.root / "forge.sqlite3", self.root / "workspace")
        self.orchestrator = Orchestrator(self.forge)
        self.env = patch.dict(os.environ, {"GOALFORGE_PUBLIC_ORIGIN": PUBLIC, "GOALFORGE_OWNER_PASSWORD": "origin-test-owner-password", "GOALFORGE_SESSION_SECRET": "origin-test-session-secret-0123456789abcdef"})
        self.env.start()
        self.auth = OwnerAuth(self.root / "goalforge.sqlite3")
        self.owner_token = self.auth.create_session()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.root, TaskStore(self.root / "tasks.sqlite3"), self.forge, self.orchestrator, self.auth))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/demo"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.env.stop()
        self.temp.cleanup()

    def post(self, origin, payload=None, extra_headers=None):
        body = json.dumps(payload or {"adaptive": True}).encode()
        headers = {"Content-Type": "application/json", "Cookie": f"goalforge_session={self.owner_token}"}
        if origin is not None:
            headers["Origin"] = origin
        headers.update(extra_headers or {})
        request = Request(self.url, data=body, method="POST", headers=headers)
        return urlopen(request)

    def test_anonymous_adaptive_demo_reset_is_denied(self):
        request = Request(self.url, data=b'{"adaptive":true,"reset":true}', method="POST",
                          headers={"Content-Type": "application/json", "Origin": PUBLIC})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 401)

    def test_anonymous_private_reads_are_denied(self):
        for path in ("/api/goals", "/api/memory", "/api/activity", "/api/settings", "/api/checkin"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.url.rsplit("/api/demo", 1)[0] + path)
            self.assertEqual(error.exception.code, 401)

    def test_owner_login_logout_revokes_session(self):
        base=self.url.rsplit("/api/demo",1)[0]
        login=Request(base+"/api/auth/login",data=json.dumps({"password":"origin-test-owner-password"}).encode(),method="POST",headers={"Content-Type":"application/json","Origin":PUBLIC})
        with urlopen(login) as response:
            cookie=response.headers["Set-Cookie"].split(";",1)[0]
        goals=base+"/api/goals"
        with urlopen(Request(goals,headers={"Cookie":cookie})) as response:
            self.assertEqual(json.loads(response.read())["goals"],[])
        logout=Request(base+"/api/auth/logout",data=b'{}',method="POST",headers={"Content-Type":"application/json","Origin":PUBLIC,"Cookie":cookie})
        with urlopen(logout): pass
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(goals,headers={"Cookie":cookie}))
        self.assertEqual(error.exception.code,401)

    def test_configured_public_origin_is_accepted_for_adaptive_demo(self):
        with self.post(PUBLIC) as response:
            body = json.loads(response.read())
        self.assertEqual(body["status"], "completed")
        self.assertFalse(body["lesson_retrieved"])
        self.assertTrue(body["verified"])
        self.assertIn("AssertionError", body["failure_output"])

    def test_public_origin_second_run_retrieves_verified_lesson(self):
        with self.post(PUBLIC):
            pass
        with self.post(PUBLIC) as response:
            body = json.loads(response.read())
        self.assertEqual(body["status"], "completed")
        self.assertTrue(body["lesson_retrieved"])
        self.assertIn("Applied verified lesson", body["strategy"])
        self.assertTrue(body["verified"])

    def test_foreign_origin_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.post("https://evil.example")
        self.assertEqual(error.exception.code, 403)

    def test_origin_requires_scheme_host_and_rejects_trailing_empty_port(self):
        for origin in ("https:////", "https:///41d7f17c26aa56d601e9.agent37.app", "https://41d7f17c26aa56d601e9.agent37.app:"):
            with self.subTest(origin=origin), self.assertRaises(HTTPError) as error:
                self.post(origin)
            self.assertEqual(error.exception.code, 403)

    def test_explicit_default_port_normalizes_to_configured_origin(self):
        with self.post(PUBLIC + ":443") as response:
            result = json.loads(response.read())
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['verified'])

    def test_configured_origin_cannot_be_overridden_by_untrusted_host_header(self):
        # Mismatch between browser Origin and configured allowlist remains forbidden
        # regardless of forged direct Host and reverse-proxy headers.
        request = Request(self.url, data=json.dumps({"adaptive": True}).encode(), method="POST", headers={
            "Content-Type": "application/json", "Cookie": f"goalforge_session={self.owner_token}", "Origin": "https://evil.example", "Host": "evil.example",
            "X-Forwarded-Host": "41d7f17c26aa56d601e9.agent37.app", "X-Forwarded-Proto": "https"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)


    def test_malformed_and_lookalike_origins_are_rejected(self):
        for origin in (PUBLIC + "/path", "https://41d7f17c26aa56d601e9.agent37.app.evil.example", "https://user@41d7f17c26aa56d601e9.agent37.app", "https://41d7f17c26aa56d601e9.agent37.app:99999", "https://41d7f17c26aa56d601e9.agent37.app?x=1"):
            with self.subTest(origin=origin), self.assertRaises(HTTPError) as error:
                self.post(origin)
            self.assertEqual(error.exception.code, 403)

    def test_public_origin_does_not_trust_arbitrary_forwarded_hosts(self):
        # Forwarding metadata is ignored; the public Origin must still be exact.
        with self.post(PUBLIC, extra_headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as error:
            self.post("https://evil.example", extra_headers={"X-Forwarded-Host": "41d7f17c26aa56d601e9.agent37.app", "X-Forwarded-Proto": "https"})
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
