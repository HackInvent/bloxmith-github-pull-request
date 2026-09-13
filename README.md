# GitHub Pull Request Block

<!-- block-metadata:start -->
[![Block version: unversioned](https://img.shields.io/badge/block-unversioned-lightgrey)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->


## Role

`github_pull_request` creates or finds a GitHub Pull Request from workflow development data without using `gh`, a Codex GitHub connector, or an LLM prompt.

## Files

- `block.py`: input normalization, GitHub REST calls, PR lookup/creation, token masking, runtime outputs, and UI rendering.
- `model.json`: input/output ports, default GitHub settings, and runtime capabilities.
- `inspector_panel.html`: block-owned inspector UI for repository, token, base branch, templates, draft, and dry-run settings.
- `block_modal.html`: wide modal layout for full block editing.
- `node_card.html`: compact canvas card content.
- `assets/css/block_modal.css`: block-owned modal and card layout.
- `assets/js/block_modal.js`: modal-owned mount hook used by the framework to keep the modal stable during polling.
- `tests/F5.26_github_pull_request_block.py`: block-local functional, centralized, and zeromq_active tests.

## Ports

- Inputs:
  - `issue` (`id: 1`): JSON object or text containing issue information. Supported fields include `number`, `issue_number`, `title`, `body`, and `html_url`.
  - `branche` (`id: 2`): JSON object or text containing the head branch. Supported fields include `branch_name`, `branche`, `branch`, and `head_branch`.
  - `rapport_dev` (`id: 3`): JSON object or text from a commit/push step. Useful fields include `commit_sha`, `commit_message`, `remote_branch`, `pushed`, `summary`, `tests_run`, and `tests_status`.
- Outputs:
  - `rapport_pr` (`id: 1`): structured JSON report with `stage`, `ok`, `status`, issue, branch, PR, blockers, warnings, and next-step fields.
  - `summary` (`id: 2`): short text summary.

## Configuration

- `api_base_url`: GitHub-compatible API root. Default: `https://api.github.com`.
- `token`: GitHub token. If empty at runtime, `GITHUB_TOKEN` is used. The token is never emitted in logs, metadata, or outputs.
- `repo`: repository in `owner/repo` format.
- `base_branch`: target branch. Default: `main`.
- `draft`: creates the PR as draft when true. Default: true.
- `dry_run`: when true, no GitHub write call is made and the block emits the planned request. Default: true.
- `timeout_sec`: HTTP timeout. Default: 30.
- `title_template`: optional Python-format template for the PR title. Available variables include `issue_number`, `issue_title`, `branch_name`, `commit_sha`, `commit_message`, `tests_status`, and `summary`.
- `body_template`: optional Python-format template for the PR body using the same variables.

## Runtime Behavior

`execute_runtime()` reads `issue`, `branche`, and `rapport_dev`, normalizes them, derives `issue_number`, `branch_name`, `base_branch`, `commit_sha`, and `repo`, then executes the PR workflow:

1. Validate `repo`, `branch_name`, and `base_branch`.
2. In non-dry-run mode, require a GitHub token.
3. In dry-run mode, skip GitHub API calls and emit the planned creation request.
4. In write mode, verify that the remote branch exists with `GET /repos/{owner}/{repo}/branches/{branch_name}`.
5. Search for an existing open PR with `GET /repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch_name}&base={base_branch}`.
6. If an existing PR is found, emit `status: "existing"`.
7. If no PR exists, create one with `POST /repos/{owner}/{repo}/pulls`.
8. Emit a structured report even when the block is blocked or GitHub returns an error.

The same implementation runs in One Shot Simulation (`centralized`) and Active Runtime (`zeromq_active`) through the generic block executor.

## Output Shape

```json
{
  "stage": "creation_pr",
  "ok": true,
  "status": "created",
  "issue_number": 3,
  "repo": "HackInvent/bloxsmith",
  "branch_name": "frontend-ui/issue-3-clear-brush-icon",
  "base_branch": "main",
  "pr_number": 123,
  "pr_url": "https://github.com/HackInvent/bloxsmith/pull/123",
  "pr_title": "...",
  "created": true,
  "existing": false,
  "draft": true,
  "commit_sha": "...",
  "actions_run": [],
  "blocked": false,
  "blockers": [],
  "warnings": [],
  "summary": "...",
  "next_step": ""
}
```

## Examples

Issue input:

```json
{"number": 3, "title": "Brush icon is unclear", "html_url": "https://github.com/HackInvent/bloxsmith/issues/3"}
```

Branch input:

```json
{"branch_name": "frontend-ui/issue-3-clear-brush-icon"}
```

Development report input:

```json
{
  "commit_sha": "abc123",
  "commit_message": "fix(frontend-ui): clarify brush icon",
  "remote_branch": "frontend-ui/issue-3-clear-brush-icon",
  "pushed": true,
  "summary": "Brush label and icon adjusted.",
  "tests_run": ["node --check frontend/app.js"],
  "tests_status": "passed"
}
```

## UI Behavior

The inspector and modal expose repository, token, branch, template, draft, dry-run, and timeout settings. Sensitive token values are not echoed back in HTML. Editable fields stay local until the user clicks **Apply**; empty sensitive fields keep the existing token. The modal declares `data-block-runtime-refresh="autonomous"`, so runtime polling does not replace the open surface or reset user focus before Apply.

## Maintenance Notes

GitHub Pull Request behavior belongs in this block. Do not add PR-specific branches to the orchestrator or active runtime worker; use the generic block executor contract instead.

## Compatibility policy

[compatibility.json](compatibility.json) records HackInvent's verified BloxSmith versions and test evidence. Only the versions listed above have been verified, using the block-owned suites in a **bundled-block test installation**. This is not a certification of managed-package installation, every browser/OS, or live provider availability. Other framework versions are unverified, not necessarily incompatible.

The block-version badge follows `model.json`, not a published Git tag. `unversioned` means that no block release version is declared; no number is inferred from the framework version. The framework still uses `model.json` for its runtime/install contract; the tester-owned JSON does not replace it. Official integration tests run in the private `bloxmith-blocs` workspace. Test helpers and the proprietary framework are not bundled in this public block repository.
