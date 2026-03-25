"""CLI entrypoint for the Bolt PR triage tool."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
from typing import Sequence

from scripts.bolt_pr_triage.analyzer import analyze_case, build_case_bundle
from scripts.bolt_pr_triage.code_context import build_code_context
from scripts.bolt_pr_triage.github_client import (
    GitHubAuthError,
    create_client,
    parse_actions_job_id,
)
from scripts.bolt_pr_triage.models import TriageResult
from scripts.bolt_pr_triage.report import (
    render_markdown_report,
    render_terminal_summary,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bolt-pr-triage",
        description="Generate a triage report for a Bolt GitHub pull request failure.",
    )
    parser.add_argument("--pr", required=True, help="GitHub pull request URL")
    parser.add_argument("--out", help="Path to write the markdown report")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print evidence collection progress",
    )
    parser.add_argument(
        "--fixture-mode",
        action="store_true",
        help="Use local fixtures instead of live GitHub and LLM backends",
    )
    return parser.parse_args(argv)


class _FixtureGitHubClient:
    def fetch_pr_context(self, pr_url: str):
        del pr_url
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "pr_context.json"
        payload = json.loads(fixture_path.read_text())
        from scripts.bolt_pr_triage.github_client import parse_pr_context
        from scripts.bolt_pr_triage.models import RepoRef

        return parse_pr_context(RepoRef(owner="bytedance", name="bolt"), payload)

    def fetch_failed_checks(self, repo_ref, pr_number):
        del repo_ref, pr_number
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "check_runs.json"
        payload = json.loads(fixture_path.read_text())
        from scripts.bolt_pr_triage.github_client import parse_failed_checks

        return parse_failed_checks(payload)

    def fetch_file_content(self, repo_ref, path, ref):
        del repo_ref, ref
        fixture_path = (
            Path(__file__).resolve().parent / "fixtures" / "file_content.json"
        )
        payload = json.loads(fixture_path.read_text())
        return {
            "path": path,
            "content": payload["content"],
            "ref": "fixture-ref",
            "repo": "bytedance/bolt",
        }


class _FixtureLlmClient:
    def analyze(self, prompt: str) -> dict[str, object]:
        del prompt
        fixture_path = (
            Path(__file__).resolve().parent / "fixtures" / "llm_response.json"
        )
        return json.loads(fixture_path.read_text())


_FLAKY_HINT_RE = re.compile(r"\bflaky\b|\brerun\b|\bretry\b", re.IGNORECASE)
_CRASH_HINT_RE = re.compile(
    r"segmentation fault|sigsegv|addresssanitizer|heap-buffer-overflow|use-after-free",
    re.IGNORECASE,
)


class _HeuristicLlmClient:
    def __init__(self, failures_summary: str) -> None:
        self._failures_summary = failures_summary

    def analyze(self, prompt: str) -> dict[str, object]:
        del prompt

        summary = self._failures_summary
        verdict = "likely_regression"
        confidence = "low"

        if not summary.strip():
            verdict = "insufficient_evidence"
            confidence = "low"
        elif _CRASH_HINT_RE.search(summary):
            verdict = "likely_regression"
            confidence = "high"
        elif _FLAKY_HINT_RE.search(summary):
            verdict = "likely_flaky"
            confidence = "medium"
        else:
            confidence = "medium"

        next_actions = [
            "Open the failing check details URL and inspect the job logs around the first error signal.",
            "If the failure is a unit test, rerun the test locally and compare stack traces.",
        ]
        if verdict == "likely_flaky":
            next_actions.insert(
                0,
                "Rerun the failed job/test to confirm flakiness and check recent flaky history.",
            )

        return {
            "verdict": verdict,
            "confidence": confidence,
            "summary": summary[:8000],
            "root_causes": ["Heuristic analysis only (no LLM backend configured)."],
            "next_actions": next_actions,
        }


def _summarize_failures(failures) -> str:
    parts: list[str] = []
    for failure in failures:
        if failure.check_name:
            parts.append(f"check={failure.check_name}")
        if failure.failed_tests:
            parts.append(f"failed_tests={', '.join(failure.failed_tests[:10])}")
        if failure.error_signals:
            parts.append(f"top_error={failure.error_signals[0]}")
    return "\n".join(parts)


def _build_check_log_text(client, pr_context, check_run: dict[str, object]) -> str:
    output = check_run.get("output") if isinstance(check_run, dict) else None
    if isinstance(output, dict):
        text_parts = [
            str(output.get("title", "")),
            str(output.get("summary", "")),
            str(output.get("text", "")),
        ]
    else:
        text_parts = []

    details_url = str(check_run.get("details_url", ""))
    job_id = parse_actions_job_id(details_url)
    if job_id is not None:
        try:
            text_parts.append(
                client.fetch_actions_job_log_text(pr_context.repo, job_id)
            )
        except Exception as exc:
            text_parts.append(f"[bolt-pr-triage] Failed to fetch Actions logs: {exc}")
    return "\n".join(part for part in text_parts if part)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.fixture_mode:
            client = _FixtureGitHubClient()
        else:
            client = create_client()
        pr_context = client.fetch_pr_context(args.pr)
        failed_checks = client.fetch_failed_checks(pr_context.repo, pr_context.number)

        from scripts.bolt_pr_triage.evidence import build_failure_evidence

        if args.fixture_mode:
            log_text = (
                Path(__file__).resolve().parent / "fixtures" / "job_log.txt"
            ).read_text()
            failures = [
                build_failure_evidence(check_run, log_text)
                for check_run in failed_checks
            ]
        else:
            failures = []
            for check_run in failed_checks:
                log_text = _build_check_log_text(client, pr_context, check_run)
                failures.append(build_failure_evidence(check_run, log_text))

        code_context = build_code_context(pr_context, failures, client, local_repo=None)
        bundle = build_case_bundle(pr_context, failures, code_context)
        if args.fixture_mode:
            triage_result: TriageResult = analyze_case(bundle, _FixtureLlmClient())
        else:
            triage_result = analyze_case(
                bundle,
                _HeuristicLlmClient(_summarize_failures(failures)),
            )

        report_path = args.out or "bolt-pr-triage-report.md"
        markdown = render_markdown_report(pr_context, triage_result, failures, code_context)
        Path(report_path).write_text(markdown)
        print(render_terminal_summary(triage_result, report_path, failures))
        return 0
    except GitHubAuthError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("Bolt PR triage failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

