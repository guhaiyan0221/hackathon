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
from scripts.bolt_pr_triage.models import PullRequestContext, TriageResult
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

_CTEST_FAILED_CASE_RE = re.compile(r"\b(?P<num>\d+)\s*-\s*(?P<name>\S+)\s*\(Failed\)")
_TYPE_UNKNOWN_RE = re.compile(
    r"Unexpected type kind UNKNOWN|type kind UNKNOWN|buildPhysicalSizeAggregators|WriterContext\.h:538",
    re.IGNORECASE,
)

_EXTRACT_UNKNOWN_LINE_RE = re.compile(r".*Unexpected type kind UNKNOWN.*", re.IGNORECASE)
_EXTRACT_BOLT_RUNTIME_ERROR_RE = re.compile(r".*BoltRuntimeError.*", re.IGNORECASE)


def _flatten_text(failures) -> str:
    parts: list[str] = []
    for failure in failures:
        parts.extend(failure.error_signals)
        parts.extend(failure.log_snippets)
    return "\n".join(parts)


def infer_root_causes(pr_context: PullRequestContext, failures) -> list[str]:
    text = _flatten_text(failures)
    causes: list[str] = []

    # Extract concrete error lines first (most useful to users).
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for regex in (_EXTRACT_UNKNOWN_LINE_RE, _EXTRACT_BOLT_RUNTIME_ERROR_RE):
        for line in lines:
            if regex.search(line):
                causes.append(f"直接原因（日志原文）：{line}")
                break
        if causes:
            break

    if _TYPE_UNKNOWN_RE.search(text):
        causes.append(
            "Mode 聚合相关用例触发 DWRF Writer 物理大小统计时遇到 UNKNOWN type，抛 INVALID_STATE（WriterContext.h:538 buildPhysicalSizeAggregators）。"
        )
        causes.append(
            "ModeAggregate 可能在某些输入/中间态（例如 unknown/null/decimal）上未正确推导输出类型或未做 UNKNOWN 兜底转换。"
        )

    # Prefer concrete gtest failures when available.
    failed_tests: list[str] = []
    for failure in failures:
        for t in failure.failed_tests:
            if t not in failed_tests:
                failed_tests.append(t)
    if failed_tests:
        shown = ", ".join(failed_tests[:5])
        suffix = f" 等 {len(failed_tests)} 个" if len(failed_tests) > 5 else ""
        causes.append(f"直接触发失败的用例：{shown}{suffix}。")

    for m in _CTEST_FAILED_CASE_RE.finditer(text):
        causes.append(f"ctest 失败用例：{m.group('name')}（编号 {m.group('num')}）。")
        break

    if not causes:
        causes.append("未能从日志中抽取到明确根因，请人工查看失败用例附近的异常/断言信息。")
    return causes


def infer_next_actions(pr_context: PullRequestContext, failures) -> list[str]:
    text = _flatten_text(failures)
    actions: list[str] = []

    # Prefer runnable repro commands.
    all_failed_tests: list[str] = []
    for failure in failures:
        for t in failure.failed_tests:
            if t not in all_failed_tests:
                all_failed_tests.append(t)

    if all_failed_tests:
        filt = ":".join(all_failed_tests[:8])
        actions.append(
            f"本地复现（gtest）：`bolt_functions_spark_aggregates_test --gtest_filter={filt}`"
        )
        actions.append(
            "若需要更详细的失败断言/异常栈：加上 `--gtest_break_on_failure` 或 `--gtest_print_time=1` 重新跑。"
        )

    m = _CTEST_FAILED_CASE_RE.search(text)
    if m:
        num = m.group("num")
        name = m.group("name")
        actions.append(f"本地复现（ctest）：`ctest -I {num},{num} --output-on-failure -V`")
        actions.append(f"或按名称跑：`ctest -R {name} --output-on-failure -V`")

    if _TYPE_UNKNOWN_RE.search(text):
        actions.append(
            "优先检查 `ModeAggregate.cpp` 的类型推导/输出类型：确保不会把 UNKNOWN type 传入 writer 侧统计；必要时对 UNKNOWN 做显式兜底（例如按 Spark 语义选择返回类型或转换为可序列化类型）。"
        )
        actions.append(
            "沿 `WriterContext.h:538 buildPhysicalSizeAggregators` 调用链回溯，确认触发该路径的写入 schema 是否包含 UNKNOWN kind（与 ModeAggregate 产出相关）。"
        )
        actions.append(
            "如果 UNKNOWN 来自某个输入列/表达式：在 ModeAggregate 的输入类型分支里加日志/断言，定位是哪一种 type kind 未覆盖。"
        )

    if not actions:
        actions.append("打开失败的 check 详情页，在首个错误信号附近查看上下文日志。")
    return actions


class _HeuristicLlmClient:
    def __init__(self, pr_context: PullRequestContext, failures_summary: str, failures) -> None:
        self._pr_context = pr_context
        self._failures_summary = failures_summary
        self._failures = failures

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

        root_causes = infer_root_causes(self._pr_context, self._failures)
        next_actions = infer_next_actions(self._pr_context, self._failures)
        if verdict == "likely_flaky":
            next_actions.insert(
                0,
                "Rerun the failed job/test to confirm flakiness and check recent flaky history.",
            )

        return {
            "verdict": verdict,
            "confidence": confidence,
            "summary": summary[:8000],
            "root_causes": root_causes,
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
                _HeuristicLlmClient(pr_context, _summarize_failures(failures), failures),
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
