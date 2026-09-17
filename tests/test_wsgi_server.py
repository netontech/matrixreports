"""The app must run under a production WSGI server, not just Flask's.

Windows matters here: gunicorn forks, so it cannot run there at all, and the
deployment uses waitress instead. That makes "does it work under waitress" a
property worth testing rather than assuming, since the alternative is finding
out on a customer's server.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

waitress = pytest.importorskip("waitress")

from webapp.app import app as flask_app          # noqa: E402


@pytest.fixture
def served(web_config_file):
    """Run the real app under waitress on a spare port."""
    flask_app.config.update(MATRIX_CONFIG=str(web_config_file))
    server = waitress.create_server(flask_app, host="127.0.0.1", port=0, threads=2)
    port = server.effective_port
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.status, response.read()


def test_reports_render_under_waitress(served):
    status, body = get(f"{served}/?report=daily&date=2026-06-01")
    assert status == 200
    assert b"Employee Daily Attendance" in body


def test_spreadsheets_download_under_waitress(served):
    """The exporter writes to a temp file and streams it - worth proving."""
    status, body = get(f"{served}/download.xlsx?report=daily&date=2026-06-01")
    assert status == 200
    assert body[:2] == b"PK"           # a real xlsx is a zip


def test_the_layout_still_grows_under_waitress(served):
    import re
    _status, body = get(f"{served}/?report=daily&date=2026-06-01")
    assert len(re.findall(rb"<th[^>]*>MINS<", body)) == 11
