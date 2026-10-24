# TaskPilot

**Work, with proof.** TaskPilot plans a small, explicit file operation, executes it only inside its own local workspace, checks the outcome, and keeps a SQLite history. No model API, third-party code, npm packages, credentials, or network dependency is required.

## Requirements

- Python 3.10 or newer (developed and tested with Python 3.11).
- No dependency installation required. The browser UI uses locally served HTML, CSS, and JavaScript. System fonts fall back if the preferred fonts are not installed.

## Quick start

```sh
cd /home/node/taskpilot
python3 server.py
```

Open <http://127.0.0.1:8765>. To change the port or data directory:

```sh
python3 server.py --host 127.0.0.1 --port 9000 --data-dir ./data
```

The HTTP API binds to localhost by default. Do not bind to `0.0.0.0` or publicly expose it as an authenticated multi-user service: it has no user accounts or login. On a trusted, isolated demo environment, Agent37's `agent37 expose PORT --label "TaskPilot"` can provide a public preview; anyone with the URL can access it. A public deployment should first add authentication and stricter access control.

## Supported requests

- `Create file notes/today.txt with content: Call Sam at 3pm`
- `Read file notes/today.txt`
- `Delete file notes/old.txt`

This deliberately small grammar is transparent and predictable; the app does not send requests to an external AI provider. Creation of a new file and reads run immediately. Overwrites and deletion require clicking **Approve & execute**. Unsupported or ambiguous requests take no action and explain the accepted forms.

## Safety and evidence

- Actions are confined to `DATA_DIR/workspace` (default: `./data/workspace`); absolute paths, `..` traversal, and resolved symlink escapes are rejected.
- Only regular UTF-8 text files are supported, up to 1 MB. Request bodies are limited to 32 KB. File and request limits are intentional.
- Deletions and overwrites are presented for approval before any mutation. Operations are restricted to the workspace; no external accounts or side effects are supported.
- Every write is read back and compared with the expected bytes. Reads return evidence of successful reading. Deletions verify absence. Failure, approval pending, and unverified outcomes are distinct statuses.
- Task history is stored in `DATA_DIR/tasks.sqlite3`, with WAL mode. It records only coarse action/status information and excludes input text, file paths/names, created-file content, and read-file content. File-reading results include the actual text in the immediate response, but not in persistent history. No credentials are collected, logged, or saved.
- Requests bearing a browser `Origin` from another host are rejected. This is a local prototype, not a replacement for authentication on a public multi-user service.

## API

- `GET /api/health` — health.
- `GET /api/tasks` — recent task history (up to 100 records).
- `GET /api/tasks/{id}` — task record.
- `POST /api/plan` — `{ "request": "..." }`; pure planning, no file change.
- `POST /api/tasks` — `{ "request": "..." }`; plan and run safe actions.
- `POST /api/tasks/{id}/approve` — `{ "approve": true }`; resume a pending overwrite or deletion.

Errors use JSON and meaningful HTTP status codes. User inputs are rendered as text, not HTML.

## Tests

Run from the project directory:

```sh
python3 -m unittest discover -s tests -v
```

The suite covers planning and input validation, sandbox path checks and symlinks, execution and approval, verification, persistent history, HTTP and malformed request handling, and untrusted origins. All tests run without installing dependencies.

## Project structure

```text
server.py              HTTP server entry point
 taskpilot/planner.py   deliberately small task grammar and structured plan
 taskpilot/security.py  workspace path enforcement
 taskpilot/executor.py  allow-listed file execution and result verification
 taskpilot/store.py     persistent SQLite task history
 taskpilot/app.py       standard-library JSON API and static file server
 static/               responsive browser UI (plain HTML/CSS/JavaScript)
 tests/                 unittest integration and behavior tests
 data/                  local persistence and workspace (created/used at runtime)
```
