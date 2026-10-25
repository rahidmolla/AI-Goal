import argparse
import os
from http.server import ThreadingHTTPServer
from pathlib import Path

from taskpilot.app import make_handler
from taskpilot.store import TaskStore
from taskpilot.goalforge import GoalForgeStore
from taskpilot.orchestrator import Orchestrator
from taskpilot.auth import OwnerAuth


def main():
    parser = argparse.ArgumentParser(description="Run the TaskPilot local file-task assistant.")
    parser.add_argument("--host", default=os.environ.get("TASKPILOT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TASKPILOT_PORT", "8765")))
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TASKPILOT_DATA", Path(__file__).parent / "data")))
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = TaskStore(data_dir / "tasks.sqlite3")
    forge = GoalForgeStore(data_dir / "goalforge.sqlite3", data_dir / "workspace")
    orchestrator = Orchestrator(forge)
    auth = OwnerAuth(data_dir / "goalforge.sqlite3")
    if not auth.enabled:
        print("WARNING: GOALFORGE_OWNER_PASSWORD (12+ chars) and GOALFORGE_SESSION_SECRET (32+ chars) must be configured; workspace APIs are locked.")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(data_dir, store, forge, orchestrator, auth))
    print(f"GoalForge is running at http://{args.host}:{args.port}")
    print(f"Owner authentication: {'enabled' if auth.enabled else 'locked (missing configuration)'}")
    print(f"Task workspace: {data_dir / 'workspace'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down TaskPilot.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
