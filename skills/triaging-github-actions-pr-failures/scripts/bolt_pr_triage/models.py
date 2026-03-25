"""Shared data models for the Bolt PR triage tool."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str


@dataclass
class PullRequestContext:
    repo: RepoRef
    number: int
    title: str = ""
    url: str = ""
    base_sha: str = ""
    head_sha: str = ""
    changed_files: list[str] = field(default_factory=list)


@dataclass
class FailureEvidence:
    check_name: str
    job_name: str = ""
    failed_tests: list[str] = field(default_factory=list)
    error_signals: list[str] = field(default_factory=list)
    log_snippets: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


@dataclass
class CodeContext:
    related_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    snippets: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CaseBundle:
    pr: PullRequestContext
    failures: list[FailureEvidence] = field(default_factory=list)
    code_context: CodeContext = field(default_factory=CodeContext)


@dataclass
class TriageResult:
    verdict: str
    summary: str = ""
    root_causes: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    confidence: str = "low"

