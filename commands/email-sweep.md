---
description: Daily end-of-day Gmail inbox sweep — auto-label the obvious, flag the rest for review, log decisions for future automation
---

You are running the user's daily `/email-sweep`. The goal: keep the inbox trending toward zero as a habit. Auto-handle the obvious, walk the user through the ambiguous, log every decision so tomorrow's obvious set is larger than today's.

## Pre-flight

1. **Load skill context**. Read the following files in parallel:
   - `~/.claude/skills/email-sweep/SKILL.md` — classification heuristics, action safety rules
   - `~/.claude/skills/email-sweep/labels.json` — canonical label taxonomy
   - `~/.claude/skills/email-sweep/standing-rules.json` — active auto-apply rules
2. **Map labels → IDs**. Call `mcp__claude_ai_Gmail__list_labels` once; cache the name→ID map for the session.
3. **Cross-check taxonomy**. If any label in `labels.json` is missing from Gmail, warn and offer `gmail-labels add "<name>"`. Do NOT proceed with sweeping until the taxonomy is intact.

## Step 1 — Parse mode + fetch threads

The command accepts one optional flag: `--all`.

| Mode | Trigger | Query | Intent |
|------|---------|-------|--------|
| Default | no args | `is:unread newer_than:1d` | Daily EOD habit — today's fresh unreads only |
| Full | `--all` | `in:inbox` | Full inbox sweep — read + unread, any age, anything still in INBOX |

Call `mcp__claude_ai_Gmail__search_threads` with:
- `query`: (per table above)
- `pageSize`: **10** (always, regardless of mode)

> **Why pageSize=10:** the Claude Gmail MCP proxy deterministically 502s at `pageSize=25` — the combined upstream Gmail API call exceeds the proxy's timeout budget. `pageSize=10` succeeds reliably. Paginate to cover the full result set.

If the first page returns 10 threads → paginate until exhausted, accumulating all thread IDs. Cap total at 100 for a single sweep (if more, tell the user and suggest running the full `email-sweep` skill's Deep Clean mode instead).

**Threshold check**:
- Default mode: if total threads > 25, note in the summary header — the daily habit slipped and full review is warranted.
- `--all` mode: if total threads > 50, note in the summary header — backlog is real; plan to follow up with another `--all` run tomorrow.

## Step 2 — Classify

For each thread, pull snippet via `mcp__claude_ai_Gmail__get_thread` with `format: MINIMAL`. Classify using the sender + subject + snippet. For each thread, decide:

- **obvious_auto** — standing rule matches OR sender/subject matches a clear pattern (e.g., `noreply@*.lever.co` = `@Reference` + `Job Search/Application`, Substack/newsletter senders = `Newsletters`, GitHub/Linear notifications = `Notifications`). Write the classification directly.
- **ambiguous** — novel sender, mixed-signal subject, or any thread where confidence isn't ~95%+. Defer to human review.

Group ambiguous threads by sender before presenting (V6 sender-grouping from the design doc — collapses decision volume).

## Step 3 — Present the summary

```
## /email-sweep[ --all] — YYYY-MM-DD

### Summary
- Mode: [default | --all]
- Threads fetched: N
- Obvious auto: A
- Ambiguous (flag for review): B
- Threshold alarm: [yes/no — see Step 1 thresholds]

### Obvious (will auto-apply)
| # | From | Subject | Labels |
|---|------|---------|--------|
...

### Ambiguous (review per sender)
[Grouped by sender — ask per-sender if same treatment as last time or fresh ruling]
```

Wait for the user's confirmation / edits on the obvious batch. Default: they'll say "go" and it runs.

## Step 4 — Review the ambiguous batch

For each sender group:
- Surface 1-2 representative threads (subject + snippet)
- Ask: "Same treatment as [last time's label]?" if prior rulings exist in `decisions.jsonl`, else "How should I label this sender's threads today?"
- Accept the answer, apply to all threads from that sender in the batch

## Step 5 — Apply

Write the combined plan (obvious + resolved-ambiguous) to `/tmp/email-sweep-YYYYMMDD.json` using the `gmail-labels.py apply` schema:

```json
[
  {"thread_id": "...", "add_labels": ["@Reference", "Life Admin/Finance"], "remove_labels": ["INBOX"], "description": "..."}
]
```

Run `gmail-labels apply /tmp/email-sweep-YYYYMMDD.json` via Bash (the CLI is installed on PATH during setup — see the repo README). Report the result line (`Applied: X, Errors: Y`).

## Step 6 — Log decisions

For every thread (obvious OR resolved-ambiguous), append one JSON line to `training/decisions.jsonl` inside the email-sweep repo (resolve via `$EMAIL_SWEEP_HOME/training/decisions.jsonl` if set, else the current repo checkout):

```json
{"timestamp": "2026-04-15T21:34:00-07:00", "thread_id": "...", "sender": "...", "subject": "...", "labels_applied": ["@Reference", "Life Admin/Finance"], "decision_source": "auto|human|rule"}
```

- `auto` — matched a clear sender/subject pattern but not an existing standing rule (candidate for future rule-mining)
- `human` — resolved via ambiguous-review in step 4
- `rule` — matched an existing entry in `standing-rules.json`

**Append only. Never rewrite.** One line per thread. Newline-delimited JSON.

## Step 7 — Wrap

Report:
```
/email-sweep complete — YYYY-MM-DD
  Threads swept: N
  Auto: A | Human: H | Rule: R
  Errors: E
  Decisions logged: N
  Next: run `/email-sweep` again tomorrow EOD
```

If `H > 3` (4+ human rulings today), add:
```
  Rule-mining candidates: [list top 3 senders from today's human decisions]
  → These are candidates for standing-rules.json entries in the week 2 upgrade.
```

## Never

- Never send email (Gmail MCP can only draft — enforced by design).
- Never trash a thread without explicit confirmation (even if a sender-rule says trash; confirm at least once per sweep).
- Never bulk-operate on > 20 threads in a single `gmail-labels apply` call without showing the plan first.
- Never skip the decisions.jsonl append — the training loop is the whole point of the week-1 build.
