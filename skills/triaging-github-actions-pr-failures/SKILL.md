---
name: triaging-github-actions-pr-failures
description: Use when GitHub PR checks/actions fail and you need a fast, repeatable way to identify the failing job, extract the first real error from logs, and produce actionable next steps without relying on opening the web UI.
---

# Triaging GitHub Actions PR Failures

## Overview
目标：给一个 PR 链接，快速得到：失败的 check/job、首个真实错误信号（root cause 线索）、以及 2–5 条下一步建议。

核心原则：**不要停在“Job failed/exit code”**，要找到日志里**第一处真正的错误**（编译错误/断言失败/异常堆栈/权限拒绝）。

## When to Use
- PR 的 GitHub Actions/Checks 挂了，需要在 5–10 分钟内定位失败点。
- `check_run.output.summary/text` 为空，必须下载 job logs 才能看到关键报错。
- 想要可脚本化/可复用的 triage 流程（不用点网页）。

## Inputs
- PR URL：`https://github.com/<owner>/<repo>/pull/<num>`
- Token（可选但强烈建议）：`GITHUB_TOKEN`
  - fine-grained PAT 通常以 `github_pat_` 开头，HTTP 头通常用 `Authorization: Bearer <token>`
  - classic PAT 通常以 `ghp_` 开头，常见是 `Authorization: token <token>`
  - 需要能读 Actions logs：通常至少 `Actions: Read` + `Contents: Read`

## Quick Reference

**优先路径（使用本 skill 自带 triage 工具）**
- 生成报告：
  - `python3 skills/triaging-github-actions-pr-failures/scripts/bolt-pr-triage --pr <PR_URL> --out /tmp/triage.md`

**通用路径（纯 API/curl）**
- 取 PR head sha：
  - `curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo>/pulls/<num>`
- 找失败 check-runs（基于 head sha）：
  - `curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo>/commits/<sha>/check-runs`
- 下载某个 job 的 logs（跟随 302）：
  - `curl -L -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo>/actions/jobs/<job_id>/logs -o /tmp/job.log_or_zip`

## Workflow

### 1) Identify failing jobs
1. 用 PR API 拿到 `head.sha`
2. 用 `commits/<sha>/check-runs` 过滤 `conclusion=failure`
3. 对每个 failed check：
   - 记录 `name`, `details_url`, `id`
   - 如果 `details_url` 含 `/actions/runs/<run_id>/job/<job_id>`，优先直接取 `job_id`

### 2) Fetch logs (critical)
对每个 `job_id`：
- 拉取 `actions/jobs/<job_id>/logs`
- 注意：返回常见是 302 重定向；有时下载到的是 zip，有时是纯文本。

**判断是 zip 还是文本**
- zip 一般以 `PK` 开头；文本往往直接是 runner 输出。

### 3) Extract “first real error”
建议搜索顺序（从最可能的 root cause 到噪音）：
- `##[error]`
- `CMake Error` / `ninja: build stopped` / `make: ***` / `fatal error:` / `error:`
- 单测：`[  FAILED  ]` / `AssertionError` / `RuntimeError` / `Exception:`
- 权限：`401` / `403` / `Resource not accessible` / `denied`

定位方法：
- 找到第一条命中的高信号行后，向上回溯 30–200 行看上下文（是哪一步执行了什么命令）。

### 4) Produce next actions
输出格式建议：
- 失败 check/job：1 行
- Top signal：1 行（贴原始错误）
- Root cause 假设：1–2 行（解释为什么这是 root cause）
- Next actions：2–5 条（可执行命令优先）

## Common Mistakes
- 只看最后的 `exit code`，不去找第一条编译/断言/异常报错。
- 只依赖 `check_run.output.summary/text`（它经常为空）。
- 忽略 token 类型：fine-grained PAT 与 classic PAT 在 Authorization scheme 上经常不同。
- 将 token 粘贴到聊天/日志/命令历史中（应立即 revoke 并重新生成）。

## Example
- 运行：`python3 skills/triaging-github-actions-pr-failures/scripts/bolt-pr-triage --pr https://github.com/bytedance/bolt/pull/334 --out /tmp/bolt-pr-triage-334.md`
- 若报告里缺少 signals：用 `details_url` 提取 job_id，下载 logs 后搜 `[  FAILED  ]`/`error:` 即可定位具体失败用例。

