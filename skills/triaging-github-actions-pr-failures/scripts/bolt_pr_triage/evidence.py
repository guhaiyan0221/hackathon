"""Failure evidence extraction helpers."""

from __future__ import annotations

import re

from scripts.bolt_pr_triage.models import FailureEvidence


FAILED_TEST_PATTERN = re.compile(r"^\[\s*FAILED\s*\]\s+(?P<name>\S+)", re.MULTILINE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Many CI logs prefix lines with timestamps; match these markers anywhere in the line.
_SUMMARY_BLOCK_START_RE = re.compile(r"\b\d+% tests passed, \d+ tests failed out of \d+\b")
_CTEST_FAILED_LIST_RE = re.compile(r"\bThe following tests FAILED:\b")
_CTEST_ERRORS_RE = re.compile(r"\bErrors while running CTest\b")
_GTEST_FAILED_LINE_RE = re.compile(r"\[\s*FAILED\s*\]\s+\S+")

# Ignore common post-failure cleanup noise.
_NOISE_TAIL_RE = re.compile(
    r"(Post job cleanup\.|Temporarily overriding HOME=|Adding repository directory to the temporary git global config|Removing SSH command configuration|Removing HTTP extra header|Removing includeIf entries|##\[command\]|\[command\]/usr/bin/git)",
    re.IGNORECASE,
)

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
    plain = strip_ansi(log_text)
    return [match.group("name") for match in FAILED_TEST_PATTERN.finditer(plain)]


def extract_error_signals(log_text: str) -> list[str]:
    plain = strip_ansi(log_text)
    signals: list[str] = []
    for line in plain.splitlines():
        stripped = line.strip()
        for pattern in ERROR_SIGNAL_PATTERNS:
            match = pattern.search(stripped)
            if match:
                signals.append(match.group(0))
                break
    return signals


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _format_log_slice(
    lines: list[str],
    start: int,
    end: int,
    match_index: int,
    matched_signals: list[str],
) -> str:
    # Render with 1-based line numbers for easier navigation.
    width = len(str(end))
    numbered = []
    for i in range(start, end):
        prefix = ">" if i == match_index else " "
        numbered.append(f"{prefix}{i+1:>{width}} | {lines[i]}")

    header = (
        f"match_line={match_index+1} window={start+1}-{end} "
        f"signals={'; '.join(matched_signals[:3])}"
    )
    return header + "\n" + "\n".join(numbered)


def select_log_windows(
    log_text: str,
    signals: list[str],
    *,
    before: int = 60,
    after: int = 20,
    max_snippets: int = 10,
) -> list[str]:
    plain = strip_ansi(log_text)
    lines = plain.splitlines()

    # Prefer concise, human-oriented summaries when present (gtest failure list and ctest summary),
    # but do NOT stop there: also include contexts for other error signals.
    summary_snippets: list[str] = []

    first_failed_idx = None
    for idx, line in enumerate(lines):
        if _GTEST_FAILED_LINE_RE.search(line):
            first_failed_idx = idx
            break
    if first_failed_idx is not None:
        start = max(0, first_failed_idx - 7)
        end = min(len(lines), first_failed_idx + 12)
        # Extend slightly to include the common "X FAILED TESTS" marker if present nearby.
        for i in range(first_failed_idx, min(len(lines), first_failed_idx + 40)):
            if "FAILED TESTS" in lines[i]:
                end = min(len(lines), i + 2)
                break
        summary_snippets.append(
            _format_log_slice(lines, start, end, first_failed_idx, ["[  FAILED  ] ..."])
        )

    summary_start = None
    for idx, line in enumerate(lines):
        if _SUMMARY_BLOCK_START_RE.search(line):
            summary_start = idx
            break
    if summary_start is not None:
        end = min(len(lines), summary_start + 20)
        for i in range(summary_start, min(len(lines), summary_start + 80)):
            if _NOISE_TAIL_RE.search(lines[i]):
                end = min(len(lines), i)
                break
            if _CTEST_ERRORS_RE.search(lines[i]):
                end = min(len(lines), i + 1)
                break
        summary_snippets.append(
            _format_log_slice(lines, summary_start, end, summary_start, ["ctest summary"])
        )

    if not signals:
        return []

    indexed: list[tuple[int, str]] = []
    for signal in signals:
        for index, line in enumerate(lines):
            if signal in line:
                indexed.append((index, signal))
                break

    if not indexed:
        return []

    indexed.sort(key=lambda item: item[0])

    # Merge overlapping windows so we don't spam the report.
    windows: list[dict[str, object]] = []
    for match_index, signal in indexed:
        start = max(0, match_index - before)
        end = min(len(lines), match_index + after + 1)
        for i in range(match_index, end):
            if _NOISE_TAIL_RE.search(lines[i]):
                end = i
                break
        if not windows:
            windows.append(
                {
                    "start": start,
                    "end": end,
                    "match_index": match_index,
                    "signals": [signal],
                }
            )
            continue

        last = windows[-1]
        last_start = int(last["start"])
        last_end = int(last["end"])

        if start <= last_end:
            last["end"] = max(last_end, end)
            last["signals"].append(signal)
        else:
            windows.append(
                {
                    "start": start,
                    "end": end,
                    "match_index": match_index,
                    "signals": [signal],
                }
            )

    snippets: list[str] = []
    for window in windows[:max_snippets]:
        snippets.append(
            _format_log_slice(
                lines,
                int(window["start"]),
                int(window["end"]),
                int(window["match_index"]),
                list(window["signals"]),
            )
        )
    # Put summaries first so users get an at-a-glance view.
    merged = summary_snippets + snippets
    return merged[:max_snippets]


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
