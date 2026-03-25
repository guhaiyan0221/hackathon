"""Failure evidence extraction helpers."""

from __future__ import annotations

import re

from scripts.bolt_pr_triage.models import FailureEvidence


FAILED_TEST_PATTERN = re.compile(r"^\[\s*FAILED\s*\]\s+(?P<name>\S+)", re.MULTILINE)
ERROR_SIGNAL_PATTERNS = (
    re.compile(r"^\[bolt-pr-triage\]\s+.+", re.MULTILINE),
    re.compile(r"RuntimeError:\s+.+"),
    re.compile(r"AssertionError:\s+.+"),
    # Common CI/build failures
    re.compile(r"\bCMake Error\b.+"),
    re.compile(r"\bninja: build stopped\b.*"),
    re.compile(r"\bmake(\[\d+\])?: \*\*\*.+"),
    re.compile(r"\bfatal error:\b.+", re.IGNORECASE),
    re.compile(r"\berror:\b.+", re.IGNORECASE),
)


def extract_failed_tests(log_text: str) -> list[str]:
    return [match.group("name") for match in FAILED_TEST_PATTERN.finditer(log_text)]


def extract_error_signals(log_text: str) -> list[str]:
    signals: list[str] = []
    for line in log_text.splitlines():
        stripped = line.strip()
        for pattern in ERROR_SIGNAL_PATTERNS:
            match = pattern.search(stripped)
            if match:
                signals.append(match.group(0))
                break
    return signals


def select_log_windows(log_text: str, signals: list[str]) -> list[str]:
    if not signals:
        return []

    snippets: list[str] = []
    lines = log_text.splitlines()
    for signal in signals:
        for index, line in enumerate(lines):
            if signal in line:
                start = max(0, index - 1)
                end = min(len(lines), index + 2)
                snippets.append("\n".join(lines[start:end]))
                break
    return snippets


def build_failure_evidence(check_run: dict[str, str], log_text: str) -> FailureEvidence:
    failed_tests = extract_failed_tests(log_text)
    error_signals = extract_error_signals(log_text)
    log_snippets = select_log_windows(log_text, error_signals)
    return FailureEvidence(
        check_name=check_run.get("name", ""),
        job_name=check_run.get("name", ""),
        failed_tests=failed_tests,
        error_signals=error_signals,
        log_snippets=log_snippets,
        artifacts=[check_run.get("details_url", "")]
        if check_run.get("details_url")
        else [],
    )

