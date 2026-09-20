# -----------------------------------------------------------------------------
# Role: Creates or finds GitHub Pull Requests from workflow runtime data.
# File Name: block.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-06-02
# -----------------------------------------------------------------------------

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import json
import os
import re
import time

from bloxsmith_app.block_api import (
    APPLICATION_JSON,
    BlockDefinition,
    BlockRuntimeContext,
    BlockRuntimeOutput,
    BlockRuntimeResult,
    render_inspector_template,
    TEXT_PLAIN,
)


DEFAULT_GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 300
GITHUB_API_VERSION = "2022-11-28"
RESULT_STAGE = "creation_pr"
TOKEN_REDACTION = "***"


class GitHubPullRequestBlockError(ValueError):
    """Structured exception used to convert GitHub failures into safe reports.

    Args:
        message: Human-readable error without secrets.
        status: Output status, usually `blocked` or `failed`.
        http_status: Optional HTTP status returned by GitHub.
    """

    def __init__(self, message: str, *, status: str = "failed", http_status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.http_status = http_status


# Functional behavior:
# FB1 - Normalize issue, branch, and development report inputs.
# FB2 - Validate repository, branch, base branch, and token requirements.
# FB3 - Emit a safe dry-run plan without creating a PR.
# FB4 - Verify remote branch existence through GitHub REST.
# FB5 - Reuse an existing open PR for the same head/base pair when present.
# FB6 - Create a draft or normal PR when no existing PR is found.
# FB7 - Return structured reports and short summaries in both runtime modes.
# FB8 - Keep token values out of outputs, logs, and metadata.
class GitHubPullRequestBlock(BlockDefinition):
    """Autonomous block that creates or reuses GitHub Pull Requests.

    Functional behaviors:
    - FB1: Normalize issue, branch, and development report inputs.
    - FB2: Validate repository, branch, base branch, and token requirements.
    - FB3: Emit a safe dry-run plan without creating a PR.
    - FB4: Verify remote branch existence through GitHub REST.
    - FB5: Reuse an existing open PR for the same head/base pair when present.
    - FB6: Create a draft or normal PR when no existing PR is found.
    - FB7: Return structured reports and short summaries in both runtime modes.
    - FB8: Keep token values out of outputs, logs, and metadata.
    """

    kind = "github_pull_request"
    directory = Path(__file__).resolve().parent

    def execute_runtime(self, context: BlockRuntimeContext) -> BlockRuntimeResult:
        """Create or retrieve a Pull Request for the current workflow item.

        Args:
            context: Runtime context with issue, branch, report inputs and durable block config.

        Returns:
            BlockRuntimeResult containing `rapport_pr` JSON and `summary` outputs.
        """

        started = time.perf_counter()
        config = self.normalize_config(context.config)
        logs: list[str] = []
        try:
            issue = self._payload_from_input(context, "issue", 1)
            branch_payload = self._payload_from_input(context, "branche", 2)
            dev_report = self._payload_from_input(context, "rapport_dev", 3)
            result = self._run_pr_workflow(config, issue, branch_payload, dev_report)
        except GitHubPullRequestBlockError as exc:
            result = self._failure_result(config=config, message=str(exc), status=exc.status, http_status=exc.http_status)
        except Exception as exc:  # noqa: BLE001 - runtime must return structured diagnostics instead of crashing.
            result = self._failure_result(config=config, message=f"Erreur GitHub Pull Request inattendue: {exc}", status="failed")

        result["duration_sec"] = round(time.perf_counter() - started, 3)
        summary = str(result.get("summary") or self._summary(result))
        result["summary"] = summary
        safe_json = json.dumps(result, ensure_ascii=False, indent=2)
        outputs = self._runtime_outputs(result_json=safe_json, summary=summary)
        status = "success" if result.get("ok") else "failed"
        logs.append(
            f"[github-pr] {context.node_id}: repo={result.get('repo') or config['repo'] or '-'} "
            f"branch={result.get('branch_name') or '-'} status={result.get('status') or '-'}."
        )
        if not result.get("ok"):
            logs.append(f"[github-pr-error] {context.node_id}: {summary}")
        else:
            logs.append(f"[done] GitHub Pull Request {context.node_id}: {summary}")
        return BlockRuntimeResult(
            status=status,
            outputs=outputs,
            logs=logs,
            error="" if result.get("ok") else summary,
            exit_code=0 if result.get("ok") else 1,
            last_message=summary,
            content_type=APPLICATION_JSON,
            worker_received=summary,
            metadata={"github_pull_request": self._metadata(result)},
        )

    def normalize_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        """Return safe runtime configuration from raw node config.

        Args:
            config: Raw graph configuration stored on the node.
        """

        raw = config if isinstance(config, dict) else {}
        return {
            "api_base_url": self._normalize_base_url(raw.get("api_base_url")),
            "token": str(raw.get("token") or os.getenv("GITHUB_TOKEN") or "").strip(),
            "repo": self._normalize_repo(raw.get("repo")),
            "base_branch": str(raw.get("base_branch") or "main").strip() or "main",
            "draft": self._bool(raw.get("draft", True)),
            "dry_run": self._bool(raw.get("dry_run", True)),
            "timeout_sec": self._normalize_int(raw.get("timeout_sec"), default=DEFAULT_TIMEOUT_SEC, minimum=1, maximum=MAX_TIMEOUT_SEC),
            "title_template": str(raw.get("title_template") or "").strip(),
            "body_template": str(raw.get("body_template") or "").strip(),
        }

    def render_node_card(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render compact canvas card content for the PR block.

        Args:
            node: Serialized graph node.
            payload: Optional UI payload, unused by this card.
        """

        config = self._ui_config(node)
        template = (self.directory / "node_card.html").read_text(encoding="utf-8")
        mode = "dry-run" if config["dry_run"] else "write"
        html = (
            template.replace("__title__", escape(str(node.get("title") or self.default_title())))
            .replace("__repo__", escape(config["repo"] or "owner/repo"))
            .replace("__base__", escape(config["base_branch"]))
            .replace("__mode__", escape(mode))
        )
        return {"html": html, "context": {"node_classes": ["github-pull-request-node"]}}

    def render_inspector_panel(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render the block-owned inspector panel with generic field bindings.

        Args:
            node: Serialized graph node.
            payload: Optional runtime/selection payload supplied by the frontend.
        """

        config = self._ui_config(node)
        template = (self.directory / "inspector_panel.html").read_text(encoding="utf-8")
        html = render_inspector_template(
            template=(
                template.replace("{{ repo }}", escape(config["repo"], quote=True))
                .replace("{{ api_base_url }}", escape(config["api_base_url"], quote=True))
                .replace("{{ base_branch }}", escape(config["base_branch"], quote=True))
                .replace("{{ token_placeholder }}", "Token configure" if config["token"] else "github_pat_...")
                .replace("{{ timeout_sec }}", str(config["timeout_sec"]))
                .replace("{{ draft_checked }}", "checked" if config["draft"] else "")
                .replace("{{ dry_run_checked }}", "checked" if config["dry_run"] else "")
                .replace("{{ title_template }}", escape(config["title_template"], quote=True))
                .replace("{{ body_template }}", escape(config["body_template"]))
            ),
            node={**node, "type": self.kind, "kind": self.kind},
            payload=payload,
        )
        return {"html": html, "context": {"node_id": str(node.get("id") or ""), "token_configured": bool(config["token"]), "full_panel": True}}

    def render_modal(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render a wide modal containing PR settings, templates, ports, and runtime state.

        Args:
            node: Serialized graph node.
            payload: Optional modal payload with latest runtime state.
        """

        body = self._render_modal_body(node=node, payload=payload)
        html = self._render_generic_modal_template(
            template=(self.directory / "block_modal.html").read_text(encoding="utf-8").replace("{{ modal_body_html }}", body),
            node={**node, "type": self.kind, "kind": self.kind},
            payload=payload or {},
        )
        return {"html": html, "context": {"node_id": str(node.get("id") or ""), "token_configured": bool(self._ui_config(node)["token"])}}

    def _run_pr_workflow(self, config: dict[str, Any], issue: dict[str, Any], branch_payload: dict[str, Any], dev_report: dict[str, Any]) -> dict[str, Any]:
        """Execute the PR workflow and return the normalized report.

        Args:
            config: Normalized durable block configuration.
            issue: Parsed issue input.
            branch_payload: Parsed branch input.
            dev_report: Parsed development report input.
        """

        issue_number = self._extract_issue_number(issue)
        branch_name = self._extract_branch_name(branch_payload, dev_report)
        commit_sha = self._extract_text(dev_report, ("commit_sha", "sha"))
        owner = self._repo_owner(config["repo"])
        title = self._format_title(config, issue=issue, branch_name=branch_name, commit_sha=commit_sha, dev_report=dev_report)
        body = self._format_body(config, issue=issue, branch_name=branch_name, commit_sha=commit_sha, dev_report=dev_report)
        result = self._base_result(
            config=config,
            issue_number=issue_number,
            branch_name=branch_name,
            commit_sha=commit_sha,
            pr_title=title,
        )
        blockers = self._validation_blockers(config=config, branch_name=branch_name)
        if blockers:
            result.update({"ok": False, "status": "blocked", "blocked": True, "blockers": blockers})
            result["summary"] = self._summary(result)
            return result

        request_payload = {"title": title, "head": branch_name, "base": config["base_branch"], "body": body, "draft": config["draft"]}
        result["planned_request"] = {
            "method": "POST",
            "path": f"/repos/{config['repo']}/pulls",
            "payload": request_payload,
        }
        if config["dry_run"]:
            result.update({"ok": True, "status": "dry_run", "actions_run": ["plan_create_pr"], "warnings": ["dry_run: aucune verification distante ni creation PR effectuee."]})
            result["summary"] = self._summary(result)
            return result

        try:
            self._verify_branch(config, branch_name)
            existing = self._find_existing_pr(config, owner=owner, branch_name=branch_name)
            if existing:
                pr_number = self._normalize_int(existing.get("number"), default=0, minimum=0, maximum=999999999)
                pr_url = str(existing.get("html_url") or existing.get("url") or "")
                pr_title = str(existing.get("title") or title)
                result.update({
                    "ok": True,
                    "status": "existing",
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "pr_title": pr_title,
                    "created": False,
                    "existing": True,
                    "actions_run": ["verify_branch", "find_existing_pr"],
                })
                result["summary"] = self._summary(result)
                return result

            created = self._create_pr(config, request_payload)
            pr_number = self._normalize_int(created.get("number"), default=0, minimum=0, maximum=999999999)
            pr_url = str(created.get("html_url") or created.get("url") or "")
            if not pr_number or not pr_url:
                raise GitHubPullRequestBlockError("Reponse GitHub invalide: numero ou URL PR manquant.", status="failed")
            result.update({
                "ok": True,
                "status": "created",
                "pr_number": pr_number,
                "pr_url": pr_url,
                "pr_title": str(created.get("title") or title),
                "created": True,
                "existing": False,
                "actions_run": ["verify_branch", "find_existing_pr", "create_pr"],
            })
            result["summary"] = self._summary(result)
            return result
        except GitHubPullRequestBlockError as exc:
            message = self._mask_secret(str(exc), config.get("token", ""))
            result.update({
                "ok": False,
                "status": exc.status,
                "blocked": exc.status == "blocked",
                "blockers": [message] if exc.status == "blocked" else [],
                "error": message,
                "http_status": exc.http_status,
            })
            result["summary"] = self._summary(result)
            return result

    def _validation_blockers(self, *, config: dict[str, Any], branch_name: str) -> list[str]:
        """Return local blockers that prevent PR lookup or creation.

        Args:
            config: Normalized PR block config.
            branch_name: Derived head branch name.
        """

        blockers: list[str] = []
        if not config["repo"]:
            blockers.append("repo GitHub manquant. Format attendu: owner/repo.")
        elif not self._is_valid_repo(config["repo"]):
            blockers.append(f"repo GitHub invalide: {config['repo']}. Format attendu: owner/repo.")
        if not branch_name:
            blockers.append("branche head manquante. Fournir branche.branch_name ou rapport_dev.remote_branch.")
        if not config["base_branch"]:
            blockers.append("base_branch manquante.")
        if not config["dry_run"] and not config["token"]:
            blockers.append("token GitHub requis pour creer ou retrouver une PR en mode ecriture.")
        return blockers

    def _verify_branch(self, config: dict[str, Any], branch_name: str) -> None:
        """Verify that the remote head branch exists through GitHub REST.

        Args:
            config: Normalized PR block config.
            branch_name: Head branch to validate.
        """

        try:
            self._api_request(config, "GET", f"/repos/{config['repo']}/branches/{quote(branch_name, safe='')}")
        except GitHubPullRequestBlockError as exc:
            if exc.http_status == 404:
                raise GitHubPullRequestBlockError(f"Branche distante introuvable: {branch_name}.", status="blocked", http_status=404) from exc
            raise

    def _find_existing_pr(self, config: dict[str, Any], *, owner: str, branch_name: str) -> dict[str, Any] | None:
        """Return the first open PR matching head owner/branch and configured base.

        Args:
            config: Normalized PR block config.
            owner: Repository owner used by GitHub's head filter.
            branch_name: Head branch name.
        """

        query = urlencode({"state": "open", "head": f"{owner}:{branch_name}", "base": config["base_branch"]})
        data = self._api_request(config, "GET", f"/repos/{config['repo']}/pulls?{query}")
        if not isinstance(data, list):
            raise GitHubPullRequestBlockError("Reponse GitHub invalide: liste PR attendue.", status="failed")
        return data[0] if data else None

    def _create_pr(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Create a Pull Request via GitHub REST.

        Args:
            config: Normalized PR block config.
            payload: GitHub PR creation payload.
        """

        data = self._api_request(config, "POST", f"/repos/{config['repo']}/pulls", payload=payload)
        if not isinstance(data, dict):
            raise GitHubPullRequestBlockError("Reponse GitHub invalide: objet PR attendu.", status="failed")
        return data

    def _api_request(self, config: dict[str, Any], method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        """Execute a GitHub REST request and return decoded JSON.

        Args:
            config: Normalized GitHub connection settings.
            method: HTTP method.
            path: Absolute API path beginning with `/`.
            payload: Optional JSON body for write requests.
        """

        base_url = config["api_base_url"].rstrip("/")
        url = f"{base_url}{path if path.startswith('/') else '/' + path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "bloxsmith-github-pull-request-block",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if config.get("token"):
            headers["Authorization"] = f"Bearer {config['token']}"
        request = Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=float(config["timeout_sec"])) as response:  # noqa: S310 - URL is user-configured API endpoint.
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            message = self._mask_secret(f"GitHub HTTP {exc.code}: {raw_error or exc.reason}", config.get("token", ""))
            raise GitHubPullRequestBlockError(message, status="failed", http_status=int(exc.code)) from exc
        except OSError as exc:
            message = self._mask_secret(f"Erreur reseau GitHub: {exc}", config.get("token", ""))
            raise GitHubPullRequestBlockError(message, status="failed") from exc
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubPullRequestBlockError("Reponse GitHub invalide: JSON attendu.", status="failed") from exc

    def _payload_from_input(self, context: BlockRuntimeContext, name: str, port_id: int) -> dict[str, Any]:
        """Parse one named runtime input as JSON object or plain text wrapper.

        Args:
            context: Runtime context containing latest input values.
            name: Canonical input port name.
            port_id: Stable input port id fallback.
        """

        text = ""
        for key in (name, str(port_id)):
            value = context.input_value(key)
            if value not in (None, ""):
                text = str(value)
                break
        text = text.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_plain_text": text}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed, "_plain_text": text}

    def _extract_issue_number(self, issue: dict[str, Any]) -> int | None:
        """Extract the GitHub issue number from JSON fields or text.

        Args:
            issue: Parsed issue payload.
        """

        for key in ("number", "issue_number"):
            value = issue.get(key)
            number = self._normalize_int(value, default=0, minimum=0, maximum=999999999)
            if number:
                return number
        text = str(issue.get("_plain_text") or issue.get("html_url") or "")
        match = re.search(r"(?:issues/|#)?(\d+)", text)
        return int(match.group(1)) if match else None

    def _extract_branch_name(self, branch_payload: dict[str, Any], dev_report: dict[str, Any]) -> str:
        """Extract the head branch, preferring the dedicated branch input.

        Args:
            branch_payload: Parsed branch input.
            dev_report: Parsed commit/push report input.
        """

        branch = self._extract_text(branch_payload, ("branch_name", "branche", "branch", "head_branch", "remote_branch"))
        if branch:
            return branch
        return self._extract_text(dev_report, ("remote_branch", "branch_name", "branch", "head_branch"))

    def _extract_text(self, payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        """Extract the first non-empty string-like value from a payload.

        Args:
            payload: Parsed input payload.
            keys: Candidate field names in priority order.
        """

        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip()
        if payload.get("_plain_text") and any(key in {"branch", "branche", "branch_name", "head_branch"} for key in keys):
            return str(payload["_plain_text"]).strip()
        return ""

    def _format_title(self, config: dict[str, Any], *, issue: dict[str, Any], branch_name: str, commit_sha: str, dev_report: dict[str, Any]) -> str:
        """Build the Pull Request title from a template or safe defaults.

        Args:
            config: Normalized block config.
            issue: Parsed issue payload.
            branch_name: Derived branch name.
            commit_sha: Derived commit SHA.
            dev_report: Parsed development report payload.
        """

        values = self._template_values(issue=issue, branch_name=branch_name, commit_sha=commit_sha, dev_report=dev_report)
        if config["title_template"]:
            return self._safe_format(config["title_template"], values).strip() or self._default_title(values)
        return self._default_title(values)

    def _format_body(self, config: dict[str, Any], *, issue: dict[str, Any], branch_name: str, commit_sha: str, dev_report: dict[str, Any]) -> str:
        """Build the Pull Request body from a template or safe defaults.

        Args:
            config: Normalized block config.
            issue: Parsed issue payload.
            branch_name: Derived branch name.
            commit_sha: Derived commit SHA.
            dev_report: Parsed development report payload.
        """

        values = self._template_values(issue=issue, branch_name=branch_name, commit_sha=commit_sha, dev_report=dev_report)
        values["base_branch"] = config["base_branch"]
        if config["body_template"]:
            return self._safe_format(config["body_template"], values).strip()
        refs = f"Refs #{values['issue_number']}" if values.get("issue_number") else "Refs: issue non renseignee"
        tests_run = values.get("tests_run") or "-"
        if isinstance(tests_run, list):
            tests_run = ", ".join(str(item) for item in tests_run)
        return (
            f"{refs}\n\n"
            f"Branche: {branch_name or '-'}\n"
            f"Base: {config['base_branch']}\n"
            f"Commit: {commit_sha or '-'}\n\n"
            f"Resume dev:\n{values.get('summary') or values.get('commit_message') or '-'}\n\n"
            f"Tests: {values.get('tests_status') or '-'}\n"
            f"Commandes: {tests_run}\n"
        )

    def _template_values(self, *, issue: dict[str, Any], branch_name: str, commit_sha: str, dev_report: dict[str, Any]) -> dict[str, Any]:
        """Return variables available to PR title/body templates.

        Args:
            issue: Parsed issue payload.
            branch_name: Derived branch name.
            commit_sha: Derived commit SHA.
            dev_report: Parsed development report payload.
        """

        issue_number = self._extract_issue_number(issue)
        return {
            "issue_number": issue_number or "",
            "issue_title": str(issue.get("title") or "").strip(),
            "issue_body": str(issue.get("body") or "").strip(),
            "issue_url": str(issue.get("html_url") or "").strip(),
            "branch_name": branch_name,
            "commit_sha": commit_sha,
            "commit_message": str(dev_report.get("commit_message") or "").strip(),
            "summary": str(dev_report.get("summary") or dev_report.get("_plain_text") or "").strip(),
            "tests_run": dev_report.get("tests_run") or "",
            "tests_status": str(dev_report.get("tests_status") or "").strip(),
            "pushed": dev_report.get("pushed"),
        }

    def _safe_format(self, template: str, values: dict[str, Any]) -> str:
        """Format a user template while keeping the raw template on format errors.

        Args:
            template: Python-format template from block config.
            values: Available formatting values.
        """

        try:
            return template.format(**values)
        except Exception:  # noqa: BLE001 - a bad template should not crash the block.
            return template

    def _default_title(self, values: dict[str, Any]) -> str:
        """Return a robust default PR title from issue or commit context.

        Args:
            values: Template values derived from inputs.
        """

        issue_number = values.get("issue_number")
        issue_title = str(values.get("issue_title") or "").strip()
        commit_message = str(values.get("commit_message") or "").strip()
        if issue_number and issue_title:
            return f"Issue #{issue_number} - {issue_title}"
        if issue_number:
            return f"Issue #{issue_number} - PR"
        if commit_message:
            return commit_message[:140]
        branch_name = str(values.get("branch_name") or "").strip()
        return f"Creation PR {branch_name}" if branch_name else "Creation PR"

    def _base_result(self, *, config: dict[str, Any], issue_number: int | None, branch_name: str, commit_sha: str, pr_title: str) -> dict[str, Any]:
        """Create the canonical result shape before workflow-specific fields are added.

        Args:
            config: Normalized block config.
            issue_number: Derived GitHub issue number, if any.
            branch_name: Derived head branch name.
            commit_sha: Derived commit SHA.
            pr_title: Planned or actual PR title.
        """

        return {
            "stage": RESULT_STAGE,
            "ok": False,
            "status": "failed",
            "issue_number": issue_number,
            "repo": config["repo"],
            "branch_name": branch_name,
            "base_branch": config["base_branch"],
            "pr_number": None,
            "pr_url": "",
            "pr_title": pr_title,
            "created": False,
            "existing": False,
            "draft": config["draft"],
            "commit_sha": commit_sha,
            "actions_run": [],
            "blocked": False,
            "blockers": [],
            "warnings": [],
            "summary": "",
            "next_step": "",
        }

    def _failure_result(self, *, config: dict[str, Any], message: str, status: str = "failed", http_status: int | None = None) -> dict[str, Any]:
        """Return a structured failed or blocked report.

        Args:
            config: Normalized config, used for safe repo/base metadata.
            message: Failure message already scrubbed of secrets.
            status: Report status.
            http_status: Optional HTTP status for diagnostics.
        """

        result = self._base_result(config=config, issue_number=None, branch_name="", commit_sha="", pr_title="")
        scrubbed = self._mask_secret(message, config.get("token", ""))
        result.update({
            "ok": False,
            "status": status,
            "blocked": status == "blocked",
            "blockers": [scrubbed] if status == "blocked" else [],
            "warnings": [],
            "error": scrubbed,
            "http_status": http_status,
        })
        result["summary"] = self._summary(result)
        return result

    def _runtime_outputs(self, *, result_json: str, summary: str) -> list[BlockRuntimeOutput]:
        """Build output values emitted by the block.

        Args:
            result_json: Serialized `rapport_pr` JSON.
            summary: Short text summary.
        """

        return [
            BlockRuntimeOutput(port_id=1, port_name="rapport_pr", value=result_json, content_type=APPLICATION_JSON),
            BlockRuntimeOutput(port_id=2, port_name="summary", value=summary, content_type=TEXT_PLAIN),
        ]

    def _summary(self, result: dict[str, Any]) -> str:
        """Return a concise human-readable summary from a PR report.

        Args:
            result: Normalized PR report.
        """

        status = result.get("status")
        repo = result.get("repo") or "repo inconnue"
        branch = result.get("branch_name") or "branche inconnue"
        if status == "dry_run":
            return f"Dry-run PR pour {repo}: {branch} -> {result.get('base_branch') or 'main'}."
        if status == "existing":
            return f"PR existante #{result.get('pr_number')} retrouvee pour {branch}."
        if status == "created":
            return f"PR #{result.get('pr_number')} creee pour {branch}."
        if status == "blocked":
            blockers = result.get("blockers") or []
            return "Creation PR bloquee: " + "; ".join(str(item) for item in blockers)
        error = result.get("error") or "erreur GitHub Pull Request"
        return f"Creation PR echouee: {error}"

    def _metadata(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return safe structured metadata without token values.

        Args:
            result: Normalized PR report.
        """

        return {
            "stage": result.get("stage"),
            "status": result.get("status"),
            "ok": bool(result.get("ok")),
            "repo": result.get("repo"),
            "branch_name": result.get("branch_name"),
            "base_branch": result.get("base_branch"),
            "issue_number": result.get("issue_number"),
            "pr_number": result.get("pr_number"),
            "created": bool(result.get("created")),
            "existing": bool(result.get("existing")),
            "blocked": bool(result.get("blocked")),
            "duration_sec": result.get("duration_sec"),
        }

    def _render_modal_body(self, *, node: dict[str, Any], payload: dict[str, Any] | None) -> str:
        """Render modal content for settings, templates, ports, and runtime state.

        Args:
            node: Serialized graph node.
            payload: Optional latest runtime payload supplied by the frontend.
        """

        config = self._ui_config(node)
        return (
            '<div class="github-pull-request-modal-body">'
            '<div class="github-pull-request-modal-layout">'
            '<div class="github-pull-request-modal-stack">'
            '<section class="github-pull-request-modal-section">'
            '<div class="ports-editor-header"><span class="group-label">Connexion GitHub</span></div>'
            '<div class="github-pull-request-config-grid">'
            '<div class="field-group"><label>Block name</label>'
            f'<input data-block-title-field type="text" autocomplete="off" value="{escape(str(node.get("title") or self.default_title()), quote=True)}" /></div>'
            '<div class="field-group"><label>Repository</label>'
            f'<input data-block-config-field="repo" type="text" autocomplete="off" spellcheck="false" placeholder="owner/repo" value="{escape(config["repo"], quote=True)}" /></div>'
            '<div class="field-group"><label>Base branch</label>'
            f'<input data-block-config-field="base_branch" type="text" autocomplete="off" spellcheck="false" value="{escape(config["base_branch"], quote=True)}" /></div>'
            '<div class="field-group"><label>API base URL</label>'
            f'<input data-block-config-field="api_base_url" type="text" autocomplete="off" spellcheck="false" value="{escape(config["api_base_url"], quote=True)}" /></div>'
            '<div class="field-group"><label>Timeout secondes</label>'
            f'<input data-block-config-field="timeout_sec" data-block-value-type="integer" type="number" min="1" max="{MAX_TIMEOUT_SEC}" step="1" value="{config["timeout_sec"]}" /></div>'
            '<div class="field-group"><label>Token GitHub</label>'
            f'<input data-block-config-field="token" data-block-skip-empty="true" type="password" autocomplete="off" spellcheck="false" placeholder="{ "Token configure" if config["token"] else "github_pat_..." }" /></div>'
            '</div>'
            '<label class="checkbox-line">'
            f'<input data-block-config-field="draft" data-block-value-type="boolean" type="checkbox" {"checked" if config["draft"] else ""} />'
            '<span>Creer la PR en draft</span></label>'
            '<label class="checkbox-line">'
            f'<input data-block-config-field="dry_run" data-block-value-type="boolean" type="checkbox" {"checked" if config["dry_run"] else ""} />'
            '<span>Dry run: do not create a PR</span></label>'
            '</section>'
            '<section class="github-pull-request-modal-section">'
            '<div class="ports-editor-header"><span class="group-label">Templates optionnels</span></div>'
            '<div class="field-group"><label>Title template</label>'
            f'<input data-block-config-field="title_template" type="text" autocomplete="off" spellcheck="false" placeholder="Issue #{{issue_number}} - {{issue_title}}" value="{escape(config["title_template"], quote=True)}" /></div>'
            '<div class="field-group"><label>Body template</label>'
            '<textarea data-block-config-field="body_template" rows="8" spellcheck="false" placeholder="Refs #{{issue_number}}&#10;Branche: {{branch_name}}">'
            f'{escape(config["body_template"])}'
            '</textarea></div>'
            '<p class="github-pull-request-modal-help">Variables disponibles: <code>issue_number</code>, <code>issue_title</code>, <code>branch_name</code>, <code>base_branch</code>, <code>commit_sha</code>, <code>commit_message</code>, <code>summary</code>, <code>tests_run</code>, <code>tests_status</code>.</p>'
            '</section>'
            '</div>'
            '<aside class="github-pull-request-modal-stack">'
            '<section class="github-pull-request-modal-section">'
            '<div class="ports-editor-header"><span class="group-label">Ports</span></div>'
            f'{self._render_generic_modal_ports(node)}'
            '</section>'
            '<section class="github-pull-request-modal-section">'
            '<div class="ports-editor-header"><span class="group-label">Dernier etat</span></div>'
            f'{self._render_generic_modal_runtime(payload or {})}'
            '</section>'
            '</aside>'
            '</div>'
            '</div>'
        )

    def _ui_config(self, node: dict[str, Any]) -> dict[str, Any]:
        """Return normalized config for UI rendering from a serialized node.

        Args:
            node: Serialized graph node.
        """

        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        normalized = self.normalize_config(config)
        # UI must not hydrate the password value into inputs, but it can show the configured placeholder state.
        normalized["token"] = str(config.get("token") or "").strip()
        return normalized

    def _normalize_base_url(self, value: Any) -> str:
        """Normalize a GitHub-compatible API base URL.

        Args:
            value: Raw URL value.
        """

        text = str(value or DEFAULT_GITHUB_API_BASE_URL).strip() or DEFAULT_GITHUB_API_BASE_URL
        return text.rstrip("/")

    def _normalize_repo(self, value: Any) -> str:
        """Normalize an owner/repo repository value.

        Args:
            value: Raw repository value.
        """

        return str(value or "").strip().strip("/")

    def _is_valid_repo(self, repo: str) -> bool:
        """Return whether repo uses the expected owner/repo shape.

        Args:
            repo: Repository full name.
        """

        parts = repo.split("/")
        return len(parts) == 2 and all(part.strip() for part in parts)

    def _repo_owner(self, repo: str) -> str:
        """Return owner from an owner/repo string.

        Args:
            repo: Repository full name.
        """

        return repo.split("/", 1)[0] if "/" in repo else ""

    def _normalize_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        """Clamp an integer config or payload value.

        Args:
            value: Raw value.
            default: Fallback if conversion fails.
            minimum: Minimum accepted value.
            maximum: Maximum accepted value.
        """

        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _bool(self, value: Any) -> bool:
        """Convert common frontend boolean representations to bool.

        Args:
            value: Raw bool-like value.
        """

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "checked"}

    def _mask_secret(self, text: str, secret: str) -> str:
        """Mask a secret in diagnostic text.

        Args:
            text: Diagnostic text.
            secret: Secret token to redact.
        """

        if secret:
            text = text.replace(secret, TOKEN_REDACTION)
        return text
