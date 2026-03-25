"""Report rendering helpers for Bolt PR triage."""

from __future__ import annotations

from scripts.bolt_pr_triage.models import (
    CodeContext,
    FailureEvidence,
    PullRequestContext,
    TriageResult,
)


def render_verdict_cn(pr_number: int, verdict: str) -> str:
    if verdict == "likely_regression":
        return f"PR#{pr_number} 引发了回归测试失败（更可能是代码回归）。"
    if verdict == "likely_flaky":
        return f"PR#{pr_number} 的失败更像是偶发（flaky）问题。"
    if verdict == "insufficient_evidence":
        return f"PR#{pr_number} 的失败证据不足，需要人工查看日志。"
    return f"PR#{pr_number} 失败原因未能归类（verdict={verdict}）。"


def render_markdown_report(
    pr_context: PullRequestContext,
    triage_result: TriageResult,
    failures: list[FailureEvidence],
    code_context: CodeContext,
) -> str:
    failure_signals = [
        signal for failure in failures for signal in failure.error_signals
    ]
    root_causes = triage_result.root_causes or ["No concrete root cause identified."]
    next_actions = triage_result.next_actions or ["Inspect the job logs manually."]

    context_sections: list[str] = []
    for failure in failures:
        if not failure.log_snippets:
            continue
        context_sections.append(f"### {failure.check_name or failure.job_name or 'failed-check'}")
        for snippet in failure.log_snippets:
            context_sections.extend(["```text", snippet, "```", ""])

    related_files = list(code_context.related_files)
    # Keep this section short: show at most 5 files.
    max_related = 5
    shown_files = related_files[:max_related]
    remaining = max(0, len(related_files) - len(shown_files))

    return "\n".join(
        [
            "# PR 失败分诊报告",
            "",
            "## 结论",
            render_verdict_cn(pr_context.number, triage_result.verdict),
            f"(verdict={triage_result.verdict})",
            "",
            "## 背景",
            triage_result.summary or "No summary available.",
            "",
            "## 失败的 Checks",
            ", ".join(failure.check_name for failure in failures)
            or "No failed checks captured.",
            "",
            "## 关键错误信号",
            *(
                [f"- {signal}" for signal in failure_signals]
                or ["- No high-signal errors extracted."]
            ),
            "",
            "## 日志上下文（所有错误信号附近）",
            *(context_sections or ["- No log context captured."]),
            "",
            "## 可能原因",
            *([f"- {cause}" for cause in root_causes]),
            "",
            "## 下一步建议",
            *([f"- {action}" for action in next_actions]),
            "",
            "## 相关代码",
            f"- PR: #{pr_context.number} {pr_context.title}",
            *(
                [f"- 相关文件: {path}" for path in shown_files]
                or ["- 未捕获相关文件。"]
            ),
            *(
                [f"- 还有 {remaining} 个文件未展示。"]
                if remaining
                else []
            ),
            "",
            "## 限制",
            f"- 置信度: {triage_result.confidence}",
        ]
    )


def render_terminal_summary(
    triage_result: TriageResult,
    report_path: str,
    failures: list[FailureEvidence],
) -> str:
    top_signal = "No high-signal error extracted."
    for failure in failures:
        if failure.error_signals:
            top_signal = failure.error_signals[0]
            break

    next_action = (
        triage_result.next_actions[0]
        if triage_result.next_actions
        else "Inspect the job logs manually."
    )
    return (
        f"verdict={triage_result.verdict}\n"
        f"top_signal={top_signal}\n"
        f"next_action={next_action}\n"
        f"report_path={report_path}"
    )
