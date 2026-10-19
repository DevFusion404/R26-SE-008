"""
Fixtures for the GitHub App integration tests.

No real credentials and no network: the App private key is generated for the
session, and every GitHub HTTP call goes to FakeGitHub, a stand-in for
requests.Session that answers by (method, path) and records what was sent.
"""

import hashlib
import hmac
import json
import re
import uuid

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

WEBHOOK_SECRET = "test-webhook-secret-DO-NOT-LEAK"
APP_ID = "123456"
INSTALLATION_ID = 987654
INSTALLATION_TOKEN = "ghs_TESTINSTALLATIONTOKENdoNotLeak0001"
HEAD_SHA = "a" * 40


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_key_pem(rsa_key):
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="session")
def public_key_pem(rsa_key):
    return rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


@pytest.fixture
def github_env(monkeypatch, private_key_pem):
    """The integration enabled and fully configured, with test-only values."""
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("GITHUB_APP_ID", APP_ID)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PEM", private_key_pem)
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "")
    monkeypatch.setenv("GITHUB_AUTONOMY_LEVEL", "L0")
    from services import github_job_service
    github_job_service.reset_shared_app_service()
    yield
    github_job_service.reset_shared_app_service()


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_payload(action="opened", number=7, sha=HEAD_SHA, owner="octo-org", repo="demo",
               installation_id=INSTALLATION_ID, head_repo=None):
    full_name = f"{owner}/{repo}"
    payload = {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "head": {"sha": sha, "ref": "feature",
                     "repo": {"full_name": head_repo or full_name}},
            "base": {"ref": "main", "repo": {"full_name": full_name}},
        },
        "repository": {"name": repo, "full_name": full_name, "owner": {"login": owner}},
    }
    if installation_id is not None:
        payload["installation"] = {"id": installation_id}
    return payload


@pytest.fixture
def post_webhook(client):
    """POST a signed webhook. Override any part to test a rejection."""
    def _post(payload=None, event="pull_request", delivery=None, body=None,
              signature=None, sign_body=True):
        raw = body if body is not None else json.dumps(payload or pr_payload()).encode()
        headers = {"X-GitHub-Event": event,
                   "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
                   "Content-Type": "application/json"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        elif sign_body:
            headers["X-Hub-Signature-256"] = sign(raw)
        return client.post("/api/github/webhook", data=raw, headers=headers)
    return _post


def make_response(status, body=None, headers=None, raw=None):
    response = requests.Response()
    response.status_code = status
    if raw is not None:
        response._content = raw
    else:
        response._content = b"" if body is None else json.dumps(body).encode()
    response._content_consumed = True          # lets iter_content() replay _content
    response.headers.update(headers or {})
    return response


#: What the fake PR snapshot contains: path -> source. CUQA is faked too, so
#: the contents only matter for the archive checks and the source viewer.
SNAPSHOT_FILES = {
    "src/app.py": "def long_function():\n" + "".join(f"    x{i} = {i}\n" for i in range(60)),
    "src/util.py": "def helper():\n    return 1\n",
    "README.md": "# demo\n",
}


def make_snapshot_zip(owner="octo-org", repo="demo", sha=HEAD_SHA, files=None, top=None):
    """An archive shaped like GitHub's zipball: one root named owner-repo-sha7."""
    import io
    import zipfile
    root = top if top is not None else f"{owner}-{repo}-{sha[:7]}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in (files if files is not None else SNAPSHOT_FILES).items():
            archive.writestr(f"{root}/{path}" if root else path, text)
    return buffer.getvalue()


class FakeGitHub:
    """A requests.Session stand-in routing on (METHOD, path regex).

    Pull request heads live in `pr_heads[(owner/repo, number)]`; a PR not in
    it reports the job's own SHA as head, so "still current" is the default.
    """

    def __init__(self):
        self.calls = []
        self.routes = []
        self.check_runs = {}
        self.pr_heads = {}
        self.downloads = []
        self._next_check_id = 5001
        self.on("POST", r"/app/installations/\d+/access_tokens",
                lambda call: make_response(201, {"token": INSTALLATION_TOKEN,
                                                 "expires_at": "2099-01-01T00:00:00Z"}))
        self.on("POST", r"/repos/[^/]+/[^/]+/check-runs", self._create_check)
        self.on("PATCH", r"/repos/[^/]+/[^/]+/check-runs/\d+", self._update_check)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/pulls/(\d+)", self._pull)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/zipball/([0-9a-f]+)", self._zipball)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/contents/(.+)\?ref=([0-9a-f]+)", self._contents)
        # Interactive review: PR comments, permissions, and the Git Data API.
        self.comments = {}
        self.permissions = {}                   # login -> "write" | "read" | ...
        self.pr_state = {}                      # (repo, number) -> "open" | "closed"
        self.branch = {}                        # branch name -> head sha
        self.commits = {}                       # sha -> {"message", "tree", "parents"}
        self.blobs, self.trees = {}, {}
        self._next_comment_id = 9001
        self.on("POST", r"/repos/([^/]+)/([^/]+)/issues/(\d+)/comments", self._create_comment)
        self.on("PATCH", r"/repos/([^/]+)/([^/]+)/issues/comments/(\d+)", self._update_comment)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/collaborators/([^/]+)/permission", self._permission)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/git/commits/([0-9a-f]+)", self._get_commit)
        self.on("GET", r"/repos/([^/]+)/([^/]+)/git/trees/([0-9a-z]+)\?recursive=1", self._get_tree)
        self.on("POST", r"/repos/([^/]+)/([^/]+)/git/blobs", self._create_blob)
        self.on("POST", r"/repos/([^/]+)/([^/]+)/git/trees", self._create_tree)
        self.on("POST", r"/repos/([^/]+)/([^/]+)/git/commits", self._create_commit)
        self.on("PATCH", r"/repos/([^/]+)/([^/]+)/git/refs/heads/(.+)", self._update_ref)

    def on(self, method, pattern, handler):
        """Register a handler; later registrations win."""
        self.routes.insert(0, (method, re.compile(pattern + "$"), handler))

    def request(self, method, url, headers=None, json=None, timeout=None, **extra):
        path = url.replace("https://api.github.com", "")
        call = {"method": method, "path": path, "headers": headers or {},
                "json": json, "timeout": timeout, "extra": extra}
        self.calls.append(call)
        for route_method, pattern, handler in self.routes:
            match = pattern.match(path)
            if route_method == method and match:
                call["match"] = match
                return handler(call)
        return make_response(404, {"message": "Not Found"})

    def _pull(self, call):
        owner, repo, number = call["match"].groups()
        key = (f"{owner}/{repo}", int(number))
        sha = self.branch.get("feature") or self.pr_heads.get(key, HEAD_SHA)
        return make_response(200, {"number": int(number), "state": self.pr_state.get(key, "open"),
                                   "head": {"sha": sha, "ref": "feature",
                                            "repo": {"full_name": f"{owner}/{repo}"}}})

    # ── PR comments ──
    def _create_comment(self, call):
        cid = self._next_comment_id
        self._next_comment_id += 1
        self.comments[cid] = [call["json"]["body"]]
        return make_response(201, {"id": cid, "body": call["json"]["body"]})

    def _update_comment(self, call):
        cid = int(call["match"].group(3))
        if cid not in self.comments:
            return make_response(404, {"message": "Not Found"})
        self.comments[cid].append(call["json"]["body"])
        return make_response(200, {"id": cid})

    def last_comment(self):
        assert self.comments, "no status comment was posted"
        return self.comments[max(self.comments)][-1]

    def _permission(self, call):
        login = call["match"].group(3)
        if login not in self.permissions:
            return make_response(404, {"message": "Not Found"})
        role = self.permissions[login]
        return make_response(200, {"permission": role, "role_name": role})

    # ── Git Data API (a tiny in-memory repository at HEAD_SHA) ──
    @staticmethod
    def _blob_sha(text):
        import hashlib
        data = text.encode()
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def _get_commit(self, call):
        sha = call["match"].group(3)
        if sha in self.commits:
            return make_response(200, {"sha": sha, **self.commits[sha]})
        return make_response(200, {"sha": sha, "tree": {"sha": "basetree"},
                                   "message": "base", "parents": []})

    def _get_tree(self, call):
        return make_response(200, {"sha": "basetree", "truncated": False, "tree": [
            {"path": path, "mode": "100644", "type": "blob", "sha": self._blob_sha(text)}
            for path, text in SNAPSHOT_FILES.items()]})

    def _create_blob(self, call):
        sha = self._blob_sha(call["json"]["content"])
        self.blobs[sha] = call["json"]["content"]
        return make_response(201, {"sha": sha})

    def _create_tree(self, call):
        sha = f"tree{len(self.trees) + 1:04d}"
        self.trees[sha] = call["json"]
        return make_response(201, {"sha": sha})

    def _create_commit(self, call):
        sha = f"{len(self.commits) + 1:040x}"
        self.commits[sha] = {"message": call["json"]["message"],
                             "tree": {"sha": call["json"]["tree"]},
                             "parents": [{"sha": p} for p in call["json"]["parents"]]}
        return make_response(201, {"sha": sha})

    def _update_ref(self, call):
        branch = call["match"].group(3)
        body = call["json"]
        assert body.get("force") is False, "write-back must never force-push"
        current = self.branch.get(branch, HEAD_SHA)
        parents = [p["sha"] for p in self.commits.get(body["sha"], {}).get("parents", [])]
        if current not in parents:
            return make_response(422, {"message": "Update is not a fast forward"})
        self.branch[branch] = body["sha"]
        return make_response(200, {"ref": f"refs/heads/{branch}", "object": {"sha": body["sha"]}})

    def _zipball(self, call):
        owner, repo, sha = call["match"].groups()
        self.downloads.append(sha)
        return make_response(200, raw=make_snapshot_zip(owner, repo, sha))

    def _contents(self, call):
        import base64
        path = call["match"].group(3)
        if path not in SNAPSHOT_FILES:
            return make_response(404, {"message": "Not Found"})
        return make_response(200, {"type": "file", "encoding": "base64",
                                   "sha": self._blob_sha(SNAPSHOT_FILES[path]),
                                   "content": base64.b64encode(SNAPSHOT_FILES[path].encode()).decode()})

    def _create_check(self, call):
        check_id = self._next_check_id
        self._next_check_id += 1
        self.check_runs[check_id] = [dict(call["json"])]
        return make_response(201, {"id": check_id, **call["json"]})

    def _update_check(self, call):
        check_id = int(call["path"].rsplit("/", 1)[1])
        self.check_runs.setdefault(check_id, []).append(dict(call["json"]))
        return make_response(200, {"id": check_id, **call["json"]})

    def paths(self, method=None):
        return [c["path"] for c in self.calls if method is None or c["method"] == method]


@pytest.fixture
def fake_github():
    return FakeGitHub()


@pytest.fixture
def app_service(github_env, fake_github):
    from services.github_app_service import GitHubAppService
    return GitHubAppService.from_config(session=fake_github)


# ── Fake CUQA ───────────────────────────────────────────────────────────────

def cuqa_payload(files=None, top_level_dir=None, files_found=None):
    """A CUQA /api/analyze-snapshot reply in the agent's real shape."""
    if files is None:
        files = [
            {"file": "app.py", "relative_path": "src/app.py", "language": "python",
             "metrics": {"lines_of_code": 61, "functions": 1, "classes": 0},
             "quality_score": 64.0,
             "code_smells": [
                 {"type": "LongMethod", "severity": "high", "line": 1, "start_line": 1,
                  "end_line": 61, "entity": "long_function",
                  "message": "long_function is 61 lines long", "category": "Bloaters"},
                 {"type": "MagicNumber", "severity": "low", "line": 0, "entity": None,
                  "message": "Magic number without a location"},
             ]},
            {"file": "util.py", "relative_path": "src/util.py", "language": "python",
             "metrics": {"lines_of_code": 2, "functions": 1, "classes": 0},
             "quality_score": 95.0,
             "code_smells": [
                 {"type": "DeadCode", "severity": "medium", "line": 1, "start_line": 1,
                  "end_line": 2, "entity": "helper", "message": "helper is never called"},
             ]},
        ]
    return {
        "type": "repository",
        "report": {"files": files, "repo_name": "octo-org/demo"},
        "snapshot": {"top_level_dir": top_level_dir, "files_found": files_found or len(files),
                     "files_analyzed": len(files)},
    }


class FakeCUQA:
    """Stands in for clients.cuqa_client.analyze_snapshot inside the pipeline."""

    def __init__(self):
        self.calls = []
        self.files = None            # None -> the default two-file report
        self.error = None            # a CUQAError to raise
        self.files_found = None

    def __call__(self, zip_path, filename, repo_name, timeout=300):
        import os
        import zipfile
        assert os.path.isfile(zip_path), "CUQA must receive an existing snapshot file"
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.namelist()
        self.calls.append({"zip_path": str(zip_path), "filename": filename,
                           "repo_name": repo_name, "members": members})
        if self.error is not None:
            raise self.error
        top = members[0].split("/", 1)[0]
        return cuqa_payload(self.files, top_level_dir=top, files_found=self.files_found)


@pytest.fixture
def fake_cuqa(monkeypatch):
    from services import github_pr_pipeline
    fake = FakeCUQA()
    monkeypatch.setattr(github_pr_pipeline, "analyze_snapshot", fake)
    return fake


# ── Fake Supabase (PostgREST query-builder subset) ──────────────────────────

class FakeAPIError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


UNIQUE_KEYS = {
    "github_pr_transformations": [("id",), ("workflow_id", "run_id", "file_path")],
    "github_pr_writebacks": [("id",), ("idempotency_key",)],
    "github_analysis_jobs": [("id",), ("delivery_id",)],
    "github_pr_workflows": [("id",), ("repository_full_name", "pr_number", "head_sha")],
    "workflows": [("id",)],
}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.op, self.payload, self.filters = "select", None, []
        self._order, self._limit, self._negate = None, None, False

    # verbs
    def select(self, _columns="*"):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    # filters
    def _add(self, fn):
        negate, self._negate = self._negate, False
        self.filters.append((lambda r: not fn(r)) if negate else fn)
        return self

    def eq(self, col, value):
        return self._add(lambda r: r.get(col) == value)

    def neq(self, col, value):
        return self._add(lambda r: r.get(col) != value)

    def lt(self, col, value):
        return self._add(lambda r: r.get(col) is not None and r.get(col) < value)

    def lte(self, col, value):
        return self._add(lambda r: r.get(col) is not None and r.get(col) <= value)

    def gt(self, col, value):
        return self._add(lambda r: r.get(col) is not None and r.get(col) > value)

    def is_(self, col, value):
        assert value == "null"
        return self._add(lambda r: r.get(col) is None)

    def in_(self, col, values):
        values = list(values)
        return self._add(lambda r: r.get(col) in values)

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, row, on_conflict=None, **kwargs):
        self.op, self.payload, self._conflict = "upsert", row, on_conflict
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self.store.fail:
            raise self.store.fail
        rows = self.store.tables.setdefault(self.table, [])
        if self.op == "insert":
            for key in UNIQUE_KEYS.get(self.table, []):
                if any(all(r.get(k) == self.payload.get(k) for k in key) for r in rows):
                    raise FakeAPIError("23505", "duplicate key value violates unique constraint")
            rows.append(dict(self.payload))
            return _Result([dict(self.payload)])
        if self.op == "upsert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self._conflict or "id").split(",")]
            for payload in payloads:
                existing = next((r for r in rows if all(r.get(k) == payload.get(k) for k in keys)), None)
                if existing is not None:
                    existing.update(payload)
                else:
                    rows.append(dict(payload))
            return _Result([dict(p) for p in payloads])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            self.store.tables[self.table] = [r for r in rows if r not in matched]
            return _Result([dict(r) for r in matched])
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return _Result([dict(r) for r in matched])
        if self._order:
            col, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return _Result([dict(r) for r in matched])


class FakeSupabase:
    def __init__(self):
        self.tables = {}
        self.fail = None

    def table(self, name):
        return _Query(self, name)


@pytest.fixture
def supabase_backend(monkeypatch):
    """Route every repository call through FakeSupabase instead of SQLite."""
    import config
    from db import supabase_client, supabase_repository
    fake = FakeSupabase()
    monkeypatch.setattr(config, "uses_supabase", lambda: True)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: fake)
    monkeypatch.setattr(supabase_repository, "get_supabase", lambda: fake)
    return fake


# ── Fake SCTVA (/sctva/execute) ─────────────────────────────────────────────

class FakeSCTVA:
    """Stands in for clients.sctva_client.execute_transformation.

    Returns SCTVA's real response shape. Per file, `outcomes[path]` picks the
    behaviour: "ok" (changed + all validations passed), "behavior_fail",
    "missing_behavior", "rollback", "unchanged" or "error".
    """

    def __init__(self):
        self.calls = []
        self.outcomes = {}
        self.error = None

    def __call__(self, payload, timeout=120):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        results = []
        for source in payload["source_files"]:
            path, code = source["file_name"], source["source_code"]
            outcome = self.outcomes.get(path, "ok")
            changed = outcome in ("ok", "behavior_fail", "missing_behavior")
            validation = {
                "syntax": {"name": "syntax", "passed": True, "score": 1.0, "message": "ok"},
                "structural": {"name": "structural", "passed": True, "score": 0.9, "message": "ok"},
                "behavioral": {"name": "behavioral", "passed": outcome != "behavior_fail",
                               "score": 0.8, "message": "fingerprints compared"},
            }
            if outcome == "missing_behavior":
                validation.pop("behavioral")
            results.append({
                "file_name": path,
                "refactored_code": (code.replace("x0 = 0", "x0 = ZERO") + "\n# refactored\n")
                                   if changed else code,
                "success": outcome != "error",
                "rollback_occurred": outcome == "rollback",
                "transformation_applied": changed,
                "total_replacements": 1 if changed else 0,
                "confidence_score": 0.91,
                "validation": validation,
            })
        return {"request_id": payload["request_id"], "language": payload["language"],
                "success": all(r["success"] for r in results),
                "rollback_occurred": any(r["rollback_occurred"] for r in results),
                "transformation_applied": any(r["transformation_applied"] for r in results),
                "confidence_score": 0.91, "file_results": results}


@pytest.fixture
def fake_sctva(monkeypatch):
    from services import transformation_service
    fake = FakeSCTVA()
    monkeypatch.setattr(transformation_service, "sctva_execute", fake)
    return fake


# ── Interactive review helpers ──────────────────────────────────────────────

def comment_payload(body, login="alice", number=7, action="created", sender_type="User",
                    on_pr=True, comment_id=555):
    issue = {"number": number}
    if on_pr:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/octo-org/demo/pulls/{number}"}
    return {"action": action, "issue": issue, "comment": {"id": comment_id, "body": body},
            "sender": {"login": login, "id": 4242, "type": sender_type},
            "repository": {"name": "demo", "full_name": "octo-org/demo",
                           "owner": {"login": "octo-org"}},
            "installation": {"id": INSTALLATION_ID}}


@pytest.fixture
def run_jobs(app, app_service):
    """Process every due job, as the worker would."""
    from services.github_job_service import run_pending_jobs

    def _run():
        with app.app_context():
            return run_pending_jobs(app=app_service, worker_id="test")
    return _run


@pytest.fixture
def linked_pr(app, post_webhook, app_service, fake_github, fake_cuqa, run_jobs, monkeypatch):
    """A PR analysed by the real pipeline, with 'alice' holding write access."""
    from db import github_pr_workflow_repository as prs
    from services import github_job_service, github_review_service
    monkeypatch.setattr(github_job_service, "shared_app_service", lambda: app_service)
    github_review_service.reset_permission_cache()
    fake_github.permissions.update({"alice": "write", "rita": "read"})
    assert post_webhook(payload=pr_payload()).status_code == 202
    assert run_jobs()[0][1] == "completed"
    with app.app_context():
        return prs.get_mapping("octo-org/demo", 7, HEAD_SHA)


@pytest.fixture
def command(post_webhook, run_jobs):
    """Post a /refactoriq comment and process it. Returns the webhook response."""
    import uuid as _uuid

    def _command(body, login="alice", delivery=None, process=True):
        response = post_webhook(payload=comment_payload(body, login=login), event="issue_comment",
                                delivery=delivery or str(_uuid.uuid4()))
        if process:
            run_jobs()
        return response
    return _command
