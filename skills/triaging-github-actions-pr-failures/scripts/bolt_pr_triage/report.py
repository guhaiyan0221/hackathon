"""Report rendering helpers for Bolt PR triage."""

from __future__ import annotations

from scripts.bolt_pr_triage.models import (
    CodeContext,
    FailureEvidence,
    PullRequestContext,
    TriageResult,
)


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

    return "\n".join(
        [
            "# PR Triage Report",
            "",
            "## Verdict",
            triage_result.verdict,
            "",
            "## Why This Matters",
            triage_result.summary or "No summary available.",
            "",
            "## Failed Checks",
            ", ".join(failure.check_name for failure in failures)
            or "No failed checks captured.",
            "",
            "## Key Failure Signals",
            *(
                [f"- {signal}" for signal in failure_signals]
                or ["- No high-signal errors extracted."]
            ),
            "",
            "## Likely Root Causes",
            *([f"- {cause}" for cause in root_causes]),
            "",
            "## Recommended Next Actions",
            *([f"- {action}" for action in next_actions]),
            "",
            "## Relevant Code Context",
            f"- PR: #{pr_context.number} {pr_context.title}",
            *(
                [f"- Related file: {path}" for path in code_context.related_files]
                or ["- No related files captured."]
            ),
            "",
            "## Limits",
            f"- Confidence: {triage_result.confidence}",
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

