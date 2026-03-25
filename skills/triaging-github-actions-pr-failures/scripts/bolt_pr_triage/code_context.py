"""Remote-first code context resolution."""

from __future__ import annotations

from scripts.bolt_pr_triage.models import (
    CodeContext,
    FailureEvidence,
    PullRequestContext,
    RepoRef,
)


def find_related_files(
    pr_context: PullRequestContext, failures: list[FailureEvidence]
) -> list[str]:
    del failures
    return list(pr_context.changed_files)


def infer_test_files(
    pr_context: PullRequestContext, failures: list[FailureEvidence]
) -> list[str]:
    del failures
    return [path for path in pr_context.changed_files if "test" in path.lower()]


def fetch_related_snippets(
    github_client: object,
    repo_ref: RepoRef,
    related_files: list[str],
    ref: str,
) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for path in related_files:
        snippets.append(github_client.fetch_file_content(repo_ref, path, ref))
    return snippets


def build_code_context(
    pr_context: PullRequestContext,
    failures: list[FailureEvidence],
    github_client: object,
    local_repo: str | None = None,
) -> CodeContext:
    del local_repo
    related_files = find_related_files(pr_context, failures)
    test_files = infer_test_files(pr_context, failures)
    snippets = fetch_related_snippets(
        github_client, pr_context.repo, related_files, pr_context.head_sha
    )
    return CodeContext(
        related_files=related_files,
        test_files=test_files,
        snippets=snippets,
    )

