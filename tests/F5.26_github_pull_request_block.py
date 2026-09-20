#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Role: Verifies GitHub Pull Request block behavior.
# File Name: F5.26_github_pull_request_block.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-06-02
# -----------------------------------------------------------------------------

"""F5.26 - GitHub Pull Request block.

The test runs against a local GitHub-compatible Pull Request API endpoint and
verifies dry-run planning, token guards, branch checks, existing PR lookup,
creation, structured GitHub errors, UI rendering, and both centralized and
zeromq_active runtime modes.
"""

# Test cases:
# - FB1/FB2/FB3 - Dry-run emits a planned PR request without contacting GitHub or requiring a token.
# - FB2 - Missing token in write mode returns a blocked structured report.
# - FB4 - Missing remote branch returns a blocked structured report.
# - FB5 - Existing open PR is reused instead of creating a new one.
# - FB6 - Missing existing PR creates a draft PR through the REST API.
# - FB7 - GitHub 401/403/404 responses return structured failed reports without crashing.
# - FB8 - Token values are masked from logs, metadata, and outputs.
# - FB7 - The block runs through the public run API in both centralized and zeromq_active modes.
# - FB7 - The block renders block-owned modal, inspector, and node-card UI.

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any
from urllib import parse as urlparse
import json
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS_ROOT = ROOT / "tests"
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from blocs.github_pull_request.block import GitHubPullRequestBlock
from bloxsmith_app.block_runtime import BlockRuntimeContext
from bloxsmith_app.block_ui import render_block_inspector_panel, render_block_modal, render_block_node_card
from ui_smoke_common import (
    create_run_api,
    data_edge,
    display_node,
    expect,
    graph_payload,
    isolated_server,
    text_node,
    wait_for_run_terminal,
)
from urllib.parse import quote
from block_test_packages import install_test_package, release_key, surface_payload


def _runtime_node_id_for_kind(run: dict[str, Any], kind: str) -> str:
    """Return the runtime node id for a block kind after graph id normalization."""

    for node_id, result in (run.get("results") or {}).items():
        bindings = result.get("bindings") if isinstance(result, dict) else {}
        if isinstance(bindings, dict) and bindings.get("kind") == kind:
            return str(node_id)
    return ""


SECRET = "ghp_test_github_pull_request_secret"
OWNER = "acme"
REPO = "project"
FULL_REPO = f"{OWNER}/{REPO}"
GOOD_BRANCH = "feature/issue-3"
EXISTING_BRANCH = "feature/existing-pr"
MISSING_BRANCH = "feature/missing-branch"
PULLS_404_BRANCH = "feature/pulls-404"
UNAUTHORIZED_TOKEN = "ghp_unauthorized"
FORBIDDEN_TOKEN = "ghp_forbidden"


class FakeGitHubPullRequestHttpServer(ThreadingHTTPServer):
    """Small GitHub Pull Request-compatible HTTP server used by block tests."""

    requests_log: list[dict[str, Any]]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeGitHubPullRequestHandler)
        self.requests_log = []

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


class FakeGitHubPullRequestHandler(BaseHTTPRequestHandler):
    server: FakeGitHubPullRequestHttpServer

    def do_GET(self) -> None:
        """Handle fake branch verification and existing PR lookup requests."""

        parsed = urlparse.urlparse(self.path)
        self._record(parsed=parsed)
        auth = self.headers.get("Authorization") or ""
        if UNAUTHORIZED_TOKEN in auth:
            self._send_json(401, {"message": f"bad token {UNAUTHORIZED_TOKEN}"})
            return
        if FORBIDDEN_TOKEN in auth:
            self._send_json(403, {"message": f"blocked token {FORBIDDEN_TOKEN}"})
            return
        branch_prefix = f"/repos/{OWNER}/{REPO}/branches/"
        if parsed.path.startswith(branch_prefix):
            branch = urlparse.unquote(parsed.path[len(branch_prefix):])
            if branch == MISSING_BRANCH:
                self._send_json(404, {"message": "branch not found"})
                return
            self._send_json(200, {"name": branch, "commit": {"sha": "branch-sha"}})
            return
        if parsed.path == f"/repos/{OWNER}/{REPO}/pulls":
            query = urlparse.parse_qs(parsed.query)
            head = (query.get("head") or [""])[0]
            if PULLS_404_BRANCH in head:
                self._send_json(404, {"message": "repo pulls not found"})
                return
            if EXISTING_BRANCH in head:
                self._send_json(
                    200,
                    [
                        {
                            "number": 77,
                            "title": "Existing PR",
                            "html_url": f"https://github.com/{FULL_REPO}/pull/77",
                        }
                    ],
                )
                return
            self._send_json(200, [])
            return
        self._send_json(404, {"message": "not found"})

    def do_POST(self) -> None:
        """Handle fake Pull Request creation requests."""

        parsed = urlparse.urlparse(self.path)
        payload = self._read_payload()
        self._record(parsed=parsed, payload=payload)
        auth = self.headers.get("Authorization") or ""
        if UNAUTHORIZED_TOKEN in auth:
            self._send_json(401, {"message": f"bad token {UNAUTHORIZED_TOKEN}"})
            return
        if FORBIDDEN_TOKEN in auth:
            self._send_json(403, {"message": f"blocked token {FORBIDDEN_TOKEN}"})
            return
        if parsed.path == f"/repos/{OWNER}/{REPO}/pulls":
            self._send_json(
                201,
                {
                    "number": 88,
                    "title": payload.get("title"),
                    "head": payload.get("head"),
                    "base": payload.get("base"),
                    "draft": payload.get("draft"),
                    "html_url": f"https://github.com/{FULL_REPO}/pull/88",
                },
            )
            return
        self._send_json(404, {"message": "not found"})

    def _read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _record(self, *, parsed: urlparse.ParseResult, payload: dict[str, Any] | None = None) -> None:
        self.server.requests_log.append(
            {
                "method": self.command,
                "path": parsed.path,
                "query": urlparse.parse_qs(parsed.query),
                "authorization": self.headers.get("Authorization") or "",
                "api_version": self.headers.get("X-GitHub-Api-Version") or "",
                "payload": payload or {},
            }
        )

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class FakeGitHubServer:
    """Context manager for the local fake GitHub Pull Request API server."""

    def __enter__(self) -> FakeGitHubPullRequestHttpServer:
        self.server = FakeGitHubPullRequestHttpServer()
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def github_pull_request_node(api_base_url: str, *, branch_name: str = GOOD_BRANCH, dry_run: bool = False, token: str = SECRET) -> dict[str, Any]:
    """Build a graph node payload for the GitHub Pull Request block."""

    return {
        "id": "github-pr-1",
        "kind": "github_pull_request",
        "title": "Creation PR test",
        "position": {"x": 580, "y": 120},
        "inputs": [
            {"id": 1, "name": "issue", "title": "Issue", "accepts": ["application/json", "text/plain", "message/*"], "multiplicity": "many", "required": True},
            {"id": 2, "name": "branche", "title": "Branche", "accepts": ["application/json", "text/plain", "message/*"], "multiplicity": "many", "required": True},
            {"id": 3, "name": "rapport_dev", "title": "Rapport dev", "accepts": ["application/json", "text/plain", "message/*"], "multiplicity": "many", "required": False},
        ],
        "outputs": [
            {"id": 1, "name": "rapport_pr", "title": "Rapport PR", "emits": ["application/json", "message/*"], "multiplicity": "many"},
            {"id": 2, "name": "summary", "title": "Summary", "emits": ["text/plain", "message/*"], "multiplicity": "many"},
        ],
        "config": {
            "api_base_url": api_base_url,
            "token": token,
            "repo": FULL_REPO,
            "base_branch": "main",
            "draft": True,
            "dry_run": dry_run,
            "timeout_sec": 10,
            "title_template": "",
            "body_template": "",
        },
    }


def runtime_graph(api_base_url: str, *, runtime_branch: str = GOOD_BRANCH, dry_run: bool = False) -> dict[str, Any]:
    """Build issue + branch + dev report -> GitHub Pull Request -> display graph."""

    issue_payload = json.dumps({"number": 3, "title": "Brush icon is unclear", "html_url": f"https://github.com/{FULL_REPO}/issues/3"})
    branch_payload = json.dumps({"branch_name": runtime_branch})
    report_payload = json.dumps(
        {
            "commit_sha": "abc123",
            "commit_message": "fix(frontend-ui): clarify brush icon",
            "remote_branch": runtime_branch,
            "pushed": True,
            "summary": "Brush icon and label adjusted.",
            "tests_run": ["node --check frontend/app.js"],
            "tests_status": "passed",
        },
        ensure_ascii=False,
    )
    return graph_payload(
        "F5 GitHub Pull Request",
        [
            text_node("issue-1", "Issue", issue_payload, 80, 80),
            text_node("branch-1", "Branche", branch_payload, 80, 220),
            text_node("report-1", "Rapport dev", report_payload, 80, 360),
            github_pull_request_node(api_base_url, branch_name=runtime_branch, dry_run=dry_run),
            display_node("display-1", "Affichage PR", 940, 120),
        ],
        [
            data_edge("edge-issue-pr", "issue-1", 1, "github-pr-1", 1),
            data_edge("edge-branch-pr", "branch-1", 1, "github-pr-1", 2),
            data_edge("edge-report-pr", "report-1", 1, "github-pr-1", 3),
            data_edge("edge-pr-display", "github-pr-1", 1, "display-1", 1),
        ],
    )


def unit_context(*, config: dict[str, Any], branch_name: str = GOOD_BRANCH, token: str | None = SECRET, issue_text: str | None = None) -> BlockRuntimeContext:
    """Build a direct runtime context for unit-level PR block tests."""

    if token is not None:
        config = {**config, "token": token}
    issue_payload = issue_text if issue_text is not None else json.dumps({"number": 3, "title": "Brush icon is unclear", "html_url": f"https://github.com/{FULL_REPO}/issues/3"})
    branch_payload = json.dumps({"branch_name": branch_name})
    report_payload = json.dumps({"commit_sha": "abc123", "commit_message": "commit msg", "summary": "dev summary", "tests_status": "passed"})
    return BlockRuntimeContext(
        run_id="unit-run",
        node_id="github-pr-unit",
        kind="github_pull_request",
        title="Creation PR unit",
        config=config,
        inputs={"issue": issue_payload, "branche": branch_payload, "rapport_dev": report_payload},
        input_content_types={"issue": "application/json", "branche": "application/json", "rapport_dev": "application/json"},
        input_message="",
        input_ports=(SimpleNamespace(id=1, name="issue"), SimpleNamespace(id=2, name="branche"), SimpleNamespace(id=3, name="rapport_dev")),
        output_ports=(
            SimpleNamespace(id=1, name="rapport_pr", emits=("application/json", "message/*")),
            SimpleNamespace(id=2, name="summary", emits=("text/plain", "message/*")),
        ),
        root_dir=ROOT,
        run_dir=ROOT,
    )


def base_config(api_base_url: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Return a normalized-like config used by unit tests."""

    return {
        "api_base_url": api_base_url,
        "repo": FULL_REPO,
        "base_branch": "main",
        "draft": True,
        "dry_run": dry_run,
        "timeout_sec": 10,
        "title_template": "",
        "body_template": "",
    }


def result_from_runtime(block_result) -> dict[str, Any]:
    """Decode the first JSON output of a direct block runtime result."""

    return json.loads(block_result.outputs[0].value)


def test_dry_run(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC1 - Dry-run plans PR creation without network calls or token requirement."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=True), token="")
    result = block.execute_runtime(context)
    report = result_from_runtime(result)
    expect(result.status == "success", "A dry run must be a runtime success.")
    expect(report.get("status") == "dry_run", "Dry-run doit retourner status=dry_run.")
    expect(report.get("planned_request", {}).get("method") == "POST", "A dry run must return the planned request.")
    expect(report.get("planned_request", {}).get("payload", {}).get("head") == GOOD_BRANCH, "The planned head branch must be kept.")
    expect(len(fake_server.requests_log) == 0, "The dry-run must not contact GitHub.")


def test_missing_token_blocked(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC2 - Write mode without token returns a blocked report."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=False), token="")
    result = block.execute_runtime(context)
    report = result_from_runtime(result)
    expect(result.status == "failed", "A missing token must fail at runtime.")
    expect(report.get("status") == "blocked", "Token absent doit retourner status=blocked.")
    expect(report.get("blocked") is True, "The report must be marked as blocked.")
    expect("token" in " ".join(report.get("blockers", [])).lower(), "The blocker must explain the missing token.")
    expect(len(fake_server.requests_log) == 0, "A missing token must not contact GitHub.")


def test_missing_branch_blocked(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC3 - Missing remote branch returns a blocked report."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=False), branch_name=MISSING_BRANCH)
    result = block.execute_runtime(context)
    report = result_from_runtime(result)
    expect(result.status == "failed", "A missing branch must fail at runtime.")
    expect(report.get("status") == "blocked", "Branche absente doit retourner status=blocked.")
    expect(report.get("branch_name") == MISSING_BRANCH, "The branch context must be kept in the report.")
    expect("Branche distante introuvable" in " ".join(report.get("blockers", [])), "The blocker must explain the missing remote branch.")


def test_existing_pr(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC4 - Existing open PR is returned without POST creation."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=False), branch_name=EXISTING_BRANCH)
    result = block.execute_runtime(context)
    report = result_from_runtime(result)
    post_requests = [item for item in fake_server.requests_log if item["method"] == "POST"]
    expect(result.status == "success", "An existing PR must be a runtime success.")
    expect(report.get("status") == "existing", "An existing PR must return status=existing.")
    expect(report.get("pr_number") == 77, "The existing PR number must be reported.")
    expect(report.get("existing") is True and report.get("created") is False, "The report must distinguish existing from created.")
    expect(not post_requests, "An existing PR must not trigger a POST.")


def test_create_pr(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC5 - No existing PR creates a draft Pull Request."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=False), branch_name=GOOD_BRANCH)
    result = block.execute_runtime(context)
    report = result_from_runtime(result)
    post_requests = [item for item in fake_server.requests_log if item["method"] == "POST"]
    expect(result.status == "success", "Creating a PR must be a runtime success.")
    expect(report.get("status") == "created", "Creation reussie doit retourner status=created.")
    expect(report.get("pr_number") == 88, "The created PR number must be reported.")
    expect(report.get("created") is True and report.get("existing") is False, "The report must distinguish created from existing.")
    expect(len(post_requests) == 1, "A creation must issue a single POST.")
    payload = post_requests[0]["payload"]
    expect(payload.get("head") == GOOD_BRANCH, "The POST payload must contain the head branch.")
    expect(payload.get("base") == "main", "The POST payload must contain the base branch.")
    expect(payload.get("draft") is True, "The POST payload must honor draft=true.")
    expect("Refs #3" in payload.get("body", ""), "The default body must reference the issue.")


def test_github_http_errors(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC6 - GitHub 401/403/404 responses are structured and do not crash."""

    block = GitHubPullRequestBlock()
    cases = [
        (UNAUTHORIZED_TOKEN, GOOD_BRANCH, 401, "failed"),
        (FORBIDDEN_TOKEN, GOOD_BRANCH, 403, "failed"),
        (SECRET, PULLS_404_BRANCH, 404, "failed"),
    ]
    for token, branch, http_status, expected_status in cases:
        context = unit_context(config=base_config(fake_server.base_url, dry_run=False), branch_name=branch, token=token)
        result = block.execute_runtime(context)
        report = result_from_runtime(result)
        text = json.dumps(report, ensure_ascii=False) + "\n" + "\n".join(result.logs) + "\n" + json.dumps(result.metadata, ensure_ascii=False)
        expect(result.status == "failed", f"HTTP {http_status} must fail at runtime.")
        expect(report.get("status") == expected_status, f"HTTP {http_status} doit retourner status={expected_status}.")
        expect(report.get("http_status") == http_status, f"HTTP {http_status} must be reported in the report.")
        expect(token not in text, f"The token must not appear in the HTTP {http_status} diagnostics.")


def test_token_masked(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC7 - Token is masked from logs, metadata, and outputs."""

    block = GitHubPullRequestBlock()
    context = unit_context(config=base_config(fake_server.base_url, dry_run=False), branch_name=GOOD_BRANCH)
    result = block.execute_runtime(context)
    combined = json.dumps([output.value for output in result.outputs], ensure_ascii=False)
    combined += "\n" + "\n".join(result.logs)
    combined += "\n" + json.dumps(result.metadata, ensure_ascii=False)
    expect(SECRET not in combined, "The token must not appear in outputs, logs or metadata.")
    expect(any(SECRET in item.get("authorization", "") for item in fake_server.requests_log), "The fake server must actually receive the token over HTTP.")


def run_runtime_case(runtime_mode: str, fake_server: FakeGitHubPullRequestHttpServer) -> dict[str, Any]:
    """Run the block through the public run API in one runtime mode."""

    with isolated_server() as server:
        # Surfaces are release assets: a bundled kind serves none of them.
        model = install_test_package(server, "github_pull_request")
        key = quote(release_key(model), safe="")
        served = lambda payload, suffix: next(
            asset["path"] for asset in payload["assets"] if asset["path"].endswith(suffix))
        created = create_run_api(server, runtime_graph(fake_server.base_url), runtime_mode=runtime_mode)
        run = wait_for_run_terminal(server, str(created.get("run_id") or ""), timeout_sec=25)

    logs = "\n".join(run.get("logs", []))
    github_node_id = _runtime_node_id_for_kind(run, "github_pull_request")
    node_logs = "\n".join(run.get("node_logs", {}).get(github_node_id, []))
    raw_result = run.get("output_values", {}).get(f"{github_node_id}:1", {}).get("value") or "{}"
    report = json.loads(raw_result)
    expect(run.get("status") == "success", f"The GitHub Pull Request {runtime_mode} run must succeed.")
    expect(report.get("status") == "created", "The run must create a PR through the fake server.")
    expect(report.get("pr_number") == 88, "The run must propagate the created PR number.")
    expect(SECRET not in logs and SECRET not in node_logs and SECRET not in raw_result, "The token must not appear in the run.")
    expect("fallback centralized" not in logs, "The run must not fall back to the centralized engine.")
    if runtime_mode == "zeromq_active":
        expect(
            run.get("results", {}).get(github_node_id, {}).get("transport") == "zeromq_active",
            "github_pull_request must run through zeromq_active.",
        )
    return run


def test_runtime_modes(fake_server: FakeGitHubPullRequestHttpServer) -> None:
    """TC8 - The block runs in centralized and zeromq_active modes."""

    for runtime_mode in ("centralized", "zeromq_active"):
        run_runtime_case(runtime_mode, fake_server)


def test_ui_rendering() -> None:
    """TC9 - Modal, inspector, and node-card are block-owned and hide token values."""

    node = github_pull_request_node("https://api.github.example", dry_run=True, token=SECRET)
    modal = render_block_modal("github_pull_request", {"node": node})
    inspector = render_block_inspector_panel("github_pull_request", {"node": node})
    card = render_block_node_card("github_pull_request", {"node": node})
    html = modal["html"] + inspector["html"] + card["html"]
    expect("github-pull-request-modal" in modal["html"], "The modal must come from the github_pull_request block.")
    expect('data-block-runtime-refresh="autonomous"' in modal["html"], "The GitHub Pull Request modal must own its runtime refresh.")
    expect("data-block-config-field=\"repo\"" in html, "The rendering must expose the repo field.")
    expect("data-block-config-field=\"base_branch\"" in html, "The rendering must expose the base_branch field.")
    expect("data-block-config-field=\"dry_run\"" in html, "The rendering must expose the dry_run field.")
    expect(SECRET not in html, "The configured token must not be hydrated into the HTML.")
    # Rendered in process: the release declaration is checked on the block manifest.
    declared = {asset["path"] for assets in json.loads(
        (Path(__file__).resolve().parents[1] / "model.json").read_text(encoding="utf-8")
    )["ui_assets"].values() for asset in assets}
    expect("assets/css/block_modal.css" in declared, "The modal must declare its block CSS.")
    expect("assets/js/block_modal.js" in declared, "The modal must declare its block-owned JS.")


def main() -> int:
    """Run all GitHub Pull Request block tests."""

    with FakeGitHubServer() as fake_server:
        test_dry_run(fake_server)
        test_missing_token_blocked(fake_server)
        test_missing_branch_blocked(fake_server)
        test_existing_pr(fake_server)
        test_create_pr(fake_server)
        test_github_http_errors(fake_server)
        test_token_masked(fake_server)
        test_runtime_modes(fake_server)
    test_ui_rendering()
    print("[ok] F5.26 GitHub Pull Request block")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
