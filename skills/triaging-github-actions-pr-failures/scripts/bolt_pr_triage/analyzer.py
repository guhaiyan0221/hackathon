"""Bundle assembly and analyzer interface for Bolt PR triage."""

from __future__ import annotations

from typing import Any

from scripts.bolt_pr_triage.models import (
    CaseBundle,
    CodeContext,
    FailureEvidence,
    PullRequestContext,
    TriageResult,
)


ALLOWED_VERDICTS = {"likely_flaky", "likely_regression", "insufficient_evidence"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


def build_case_bundle(
    pr_context: PullRequestContext,
    failures: list[FailureEvidence],
    code_context: CodeContext,
) -> CaseBundle:
    return CaseBundle(pr=pr_context, failures=failures, code_context=code_context)


def build_analysis_prompt(case_bundle: CaseBundle) -> str:
    return (
        "Analyze this Bolt PR failure case.\n"
        "Allowed verdicts: likely_flaky, likely_regression, insufficient_evidence.\n"
        f"PR: #{case_bundle.pr.number} {case_bundle.pr.title}\n"
        f"Changed files: {case_bundle.pr.changed_files}\n"
        f"Failures: {[failure.check_name for failure in case_bundle.failures]}\n"
        f"Signals: {[failure.error_signals for failure in case_bundle.failures]}\n"
        f"Related files: {case_bundle.code_context.related_files}\n"
    )


def parse_triage_result(response_json: dict[str, Any]) -> TriageResult:
    verdict = str(response_json.get("verdict", ""))
    if verdict not in ALLOWED_VERDICTS:
        raise ValueError(f"Unsupported verdict: {verdict}")

    confidence = str(response_json.get("confidence", "low"))
    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError(f"Unsupported confidence: {confidence}")

    return TriageResult(
        verdict=verdict,
        summary=str(response_json.get("summary", "")),
        root_causes=[str(item) for item in response_json.get("root_causes", [])],
        next_actions=[str(item) for item in response_json.get("next_actions", [])],
        confidence=confidence,
    )


def analyze_case(case_bundle: CaseBundle, llm_client: object) -> TriageResult:
    prompt = build_analysis_prompt(case_bundle)
    response_json = llm_client.analyze(prompt)
    return parse_triage_result(response_json)

