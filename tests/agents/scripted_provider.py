"""Deterministic scripted model for contract tests of the agent engine.

Every response is marked ``is_mock=True``. It exercises the engine's real code
paths (parsing, validation, sandbox writes, process execution, verification),
but it is NOT evidence that any live LLM can do the work. Results produced with
it are labelled SIMULATED by the engine and TESTED_WITH_STUB in the register.
"""
import json

from packages.contracts import ModelResponse, ModelUsage, ProviderName

PLAN = {
    "summary": "Build an in-memory notes service with a JSON HTTP API and a responsive index page.",
    "deliverable": "Python package app/ with store and HTTP server, unit tests and acceptance tests",
    "tasks": [
        {"id": "implement_store_and_api", "role": "engineer", "title": "Implement notes store and HTTP API",
         "instructions": "Create app/store.py (NotesStore CRUD) and app/server.py (make_server) plus unit tests.",
         "depends_on": []},
        {"id": "acceptance_tests", "role": "qa", "title": "Write black-box acceptance tests",
         "instructions": "Write qa_tests/ that drive the HTTP API end to end, including delete and errors.",
         "depends_on": []},
        {"id": "code_review", "role": "reviewer", "title": "Review implementation",
         "instructions": "Review app/ for defects and report findings.",
         "depends_on": ["implement_store_and_api"]},
    ],
    "clarifications_needed": [],
}

STORE_TEMPLATE = '''"""In-memory notes store."""
import itertools
import threading


class NotesStore:
    def __init__(self):
        self._notes = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    @staticmethod
    def _check_title(title):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("title must be 1-200 characters")

    def create(self, title, body=""):
        self._check_title(title)
        with self._lock:
            note = {"id": next(self._ids), "title": title.strip(), "body": str(body)}
            self._notes[note["id"]] = note
            return dict(note)

    def get(self, note_id):
        return dict(self._notes[note_id])

    def list(self):
        return [dict(n) for n in sorted(self._notes.values(), key=lambda n: n["id"])]

    def update(self, note_id, title=None, body=None):
        with self._lock:
            note = self._notes[note_id]
            if title is not None:
                self._check_title(title)
                note["title"] = title.strip()
            if body is not None:
                note["body"] = str(body)
            return dict(note)

    def delete(self, note_id):
        with self._lock:
{DELETE_BODY}
'''

BUGGY_DELETE = '''            note = self._notes[note_id]
            return dict(note)  # BUG: the note is never removed'''
FIXED_DELETE = '''            return dict(self._notes.pop(note_id))'''

SERVER = '''"""Minimal JSON HTTP API and responsive index page for the notes store."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.store import NotesStore

INDEX = (b"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
         b"<meta name='viewport' content='width=device-width, initial-scale=1'>"
         b"<title>Notes</title><style>main{max-width:40rem;margin:auto;padding:1rem}</style></head>"
         b"<body><main><h1>Notes</h1><ul id='notes'></ul></main></body></html>")


def make_server(store=None, port=0):
    store = store or NotesStore()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload=None, content_type="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _note_id(self):
            try:
                return int(self.path.rsplit("/", 1)[1])
            except ValueError:
                return None

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path == "/":
                return self._send(200, INDEX, "text/html; charset=utf-8")
            if self.path == "/notes":
                return self._send(200, store.list())
            if self.path.startswith("/notes/"):
                try:
                    return self._send(200, store.get(self._note_id()))
                except KeyError:
                    return self._send(404, {"error": "not found"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/notes":
                return self._send(404, {"error": "not found"})
            try:
                data = self._body()
                return self._send(201, store.create(data.get("title"), data.get("body", "")))
            except (ValueError, AttributeError):
                return self._send(400, {"error": "invalid note"})

        def do_PUT(self):
            try:
                data = self._body()
                return self._send(200, store.update(self._note_id(), data.get("title"), data.get("body")))
            except KeyError:
                return self._send(404, {"error": "not found"})
            except (ValueError, AttributeError):
                return self._send(400, {"error": "invalid note"})

        def do_DELETE(self):
            try:
                return self._send(200, store.delete(self._note_id()))
            except KeyError:
                return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
'''

UNIT_TESTS = '''from app.store import NotesStore


def test_create_and_get():
    store = NotesStore()
    note = store.create("First", "hello")
    assert store.get(note["id"])["body"] == "hello"


def test_list_sorted():
    store = NotesStore()
    store.create("a")
    store.create("b")
    assert [n["title"] for n in store.list()] == ["a", "b"]
'''

QA_TESTS = '''import json
import threading
import urllib.error
import urllib.request

import pytest

from app.server import make_server


@pytest.fixture
def base():
    server = make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def call(base, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_crud_round_trip(base):
    status, body = call(base, "POST", "/notes", {"title": "Groceries", "body": "milk"})
    assert status == 201
    note = json.loads(body)
    assert call(base, "GET", "/notes/%d" % note["id"])[0] == 200
    status, body = call(base, "PUT", "/notes/%d" % note["id"], {"body": "milk, eggs"})
    assert status == 200 and json.loads(body)["body"] == "milk, eggs"
    assert call(base, "DELETE", "/notes/%d" % note["id"])[0] == 200
    assert call(base, "GET", "/notes/%d" % note["id"])[0] == 404
    assert json.loads(call(base, "GET", "/notes")[1]) == []


def test_validation_and_missing(base):
    assert call(base, "POST", "/notes", {"title": ""})[0] == 400
    assert call(base, "DELETE", "/notes/999")[0] == 404


def test_index_is_responsive(base):
    status, body = call(base, "GET", "/")
    assert status == 200 and b"name='viewport'" in body
'''


def engineer_files(fixed: bool):
    store = STORE_TEMPLATE.replace("{DELETE_BODY}", FIXED_DELETE if fixed else BUGGY_DELETE)
    return [{"path": "app/__init__.py", "content": ""},
            {"path": "app/store.py", "content": store},
            {"path": "app/server.py", "content": SERVER},
            {"path": "tests/test_store.py", "content": UNIT_TESTS}]


class ScriptedModel:
    """Callable used as the engine's invoke function. Records every request."""

    def __init__(self, *, fix_on_repair=True, plan=None, overrides=None):
        self.fix_on_repair = fix_on_repair
        self.plan = plan or PLAN
        self.overrides = overrides or {}
        self.requests = []

    def text_for(self, request):
        role = request.agent
        if role in self.overrides:
            value = self.overrides[role]
            return value(request) if callable(value) else value
        if role == "planner":
            return json.dumps(self.plan)
        if role == "engineer":
            repairing = "INDEPENDENT VERIFIER FAILURE REPORT" in request.prompt
            return json.dumps({"files": engineer_files(fixed=repairing and self.fix_on_repair),
                               "notes": "store and API implemented", "uncertainty": ""})
        if role == "qa":
            return json.dumps({"files": [{"path": "qa_tests/__init__.py", "content": ""},
                                         {"path": "qa_tests/test_acceptance.py", "content": QA_TESTS}],
                               "notes": "black-box HTTP tests", "uncertainty": ""})
        if role == "reviewer":
            return json.dumps({"findings": [{"severity": "low", "path": "app/server.py",
                                             "message": "No request size limit on JSON bodies."}],
                               "notes": "advisory"})
        raise AssertionError("unexpected agent " + str(role))

    def __call__(self, request):
        self.requests.append(request)
        return ModelResponse(text=self.text_for(request), provider=ProviderName.MOCK, model_name="scripted-v1",
                             usage=ModelUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                             latency_ms=1, is_mock=True)
