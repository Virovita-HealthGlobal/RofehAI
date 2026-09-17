"""
Test configuration: point the app at an isolated, temporary SQLite file
(instead of the default ./rofehai.db) so test runs don't pollute or
depend on any real local database, and explicitly create tables so tests
don't depend on FastAPI's startup-event timing.
"""

import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db", prefix="rofehai-test-")
os.environ["ROFEHAI_DATABASE_URL"] = f"sqlite:///{_path}"

from app.core import db  # noqa: E402  (must import after setting the env var above)

db.init_db()
