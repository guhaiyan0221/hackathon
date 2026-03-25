"""GitHub helpers for Bolt PR triage."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib import request
from urllib.error import HTTPError
from urllib.parse import urlparse

from scripts.bolt_pr_triage.models import PullRequestContext, RepoRef


class GitHubAuthError(RuntimeError):
    """Raised when GitHub authentication is unavailable."""


@dataclass(frozen=True)
class GitHubClient:
    headers: dict[str, str]

    def _get_json(self, url: str) -> Any:
        req = request.Request(url, headers=self.headers)
        with request.urlopen(req) as resp:
            return json.load(resp)

    def _get_bytes(self, url: str) -> bytes:
        req = request.Request(url, headers=self.headers)
        with request.urlopen(req) as resp:
            return resp.read()

    def fetch_pr_context(self, pr_url: str) -> PullRequestContext:
        repo_ref, pr_number = parse_pr_url(pr_url)
        pr_api_url = (
            f"https://api.github.com/repos/{repo_ref.owner}/{repo_ref.name}/pulls/{pr_number}"
        )
        files_api_url = f"{pr_api_url}/files"
        pr_payload = self._get_json(pr_api_url)
        files_payload = self._get_json(files_api_url)
        pr_payload = dict(pr_payload)
        pr_payload["files"] = files_payload
        return parse_pr_context(repo_ref, pr_payload)

    def fetch_failed_checks(self, repo_ref: RepoRef, pr_number: int) -> list[dict[str, Any]]:
        pulls_api_url = (
            f"https://api.github.com/repos/{repo_ref.owner}/{repo_ref.name}/pulls/{pr_number}"
        )
        pr_payload = self._get_json(pulls_api_url)
        head_sha = pr_payload["head"]["sha"]
        checks_api_url = (
            f"https://api.github.com/repos/{repo_ref.owner}/{repo_ref.name}"
            f"/commits/{head_sha}/check-runs"
        )
        checks_payload = self._get_json(checks_api_url)
        return parse_failed_checks(checks_payload)

    def fetch_file_content(self, repo_ref: RepoRef, path: str, ref: str) -> dict[str, str]:
        contents_api_url = (
            f"https://api.github.com/repos/{repo_ref.owner}/{repo_ref.name}"
            f"/contents/{path}?ref={ref}"
        )
        payload = self._get_json(contents_api_url)
        decoded_content = decode_github_file_content(payload)
        return {
            "path": path,
            "content": decoded_content,
            "ref": ref,
            "repo": f"{repo_ref.owner}/{repo_ref.name}",
        }

    def fetch_actions_job_log_text(self, repo_ref: RepoRef, job_id: int) -> str:
        """Fetch a GitHub Actions job log and return it as plain text."""

        logs_url = (
            f"https://api.github.com/repos/{repo_ref.owner}/{repo_ref.name}"
            f"/actions/jobs/{job_id}/logs"
        )
        try:
            data = self._get_bytes(logs_url)
        except HTTPError as exc:
            raise RuntimeError(
                f"Failed to fetch Actions job logs for job_id={job_id}: {exc}"
            )

        if not data:
            return ""

        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return data.decode("utf-8", errors="replace")

        parts: list[str] = []
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            try:
                parts.append(archive.read(name).decode("utf-8", errors="replace"))
            except KeyError:
                continue
        return "\n\n".join(parts)


def parse_pr_url(pr_url: str) -> tuple[RepoRef, int]:
    parsed = urlparse(pr_url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc != "github.com" or len(parts) < 4 or parts[2] != "pull":
        raise ValueError(f"Unsupported GitHub PR URL: {pr_url}")
    return RepoRef(owner=parts[0], name=parts[1]), int(parts[3])


def build_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        scheme = "token"
        if token.startswith("github_pat_"):
            scheme = "Bearer"
        headers["Authorization"] = f"{scheme} {token}"
    return headers


def parse_pr_context(repo_ref: RepoRef, payload: dict[str, Any]) -> PullRequestContext:
    return PullRequestContext(
        repo=repo_ref,
        number=payload["number"],
        title=payload.get("title", ""),
        url=payload.get("html_url", ""),
        base_sha=payload.get("base", {}).get("sha", ""),
        head_sha=payload.get("head", {}).get("sha", ""),
        changed_files=[item["filename"] for item in payload.get("files", [])],
    )


def parse_failed_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    failed = []
    for check_run in payload.get("check_runs", []):
        if check_run.get("conclusion") == "failure":
            failed.append(check_run)
    return failed


def create_client() -> GitHubClient:
    return GitHubClient(headers=build_headers())


_ACTIONS_JOB_URL_RE = re.compile(r"/actions/runs/(?P<run_id>\d+)/job/(?P<job_id>\d+)")


def parse_actions_job_id(details_url: str) -> int | None:
    if not details_url:
        return None
    match = _ACTIONS_JOB_URL_RE.search(details_url)
    if not match:
        return None
    try:
        return int(match.group("job_id"))
    except ValueError:
        return None


def decode_github_file_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not content:
        return ""
    encoding = payload.get("encoding")
    if encoding == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(content, str):
        return content
    return str(content)

