# hackathon_code_repo

Local skill repository for Codex.

## Repository Layout

- `skills/triaging-github-actions-pr-failures`
  - Triages failing GitHub Actions / PR checks without opening the web UI.

## Install a Skill

Copy the skill directory into `~/.codex/skills`.

```bash
cp -R skills/triaging-github-actions-pr-failures ~/.codex/skills/
```

Or use an absolute path from this repo:

```bash
cp -R /home/guhaiyan/hackathon/hackathon_code_repo/skills/triaging-github-actions-pr-failures ~/.codex/skills/
```

## Verify Installation

```bash
ls ~/.codex/skills/triaging-github-actions-pr-failures
```

## Installed Skill

- `triaging-github-actions-pr-failures`
  - Use when GitHub PR checks or Actions fail and you need a repeatable CLI workflow to find the failing job, extract the first real error, and propose next actions.

## Note

Restart Codex to pick up newly installed skills.
