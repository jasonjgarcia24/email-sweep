---
description: Daily end-of-day Gmail inbox sweep — auto-label the obvious, flag the rest for review, log decisions for future automation. For first-run setup (OAuth, CLI, label taxonomy) or cleanup, see `/email-sweep:init` and `/email-sweep:init --remove`.
---

You are running the user's `/email-sweep`. Behavior depends on the flag:

- **No flag**: daily end-of-day sweep. Classify today's unreads, confirm obvious batch, walk ambiguous by sender, apply labels, log decisions. Target: keep the inbox trending toward zero as a habit.
- **`--all`**: full inbox sweep (read + unread, any age) — weekly catch-up.

> **First-run setup or cleanup:** invoke `/email-sweep:init` (12-gate setup) or `/email-sweep:init --remove` (cleanup). Those modes used to live here as `--init` / `--remove` flags but moved to a sibling command for consistency with other lifecycle plugins.

## Step 0 — Parse flag and dispatch

Inspect `$ARGUMENTS` (or the user's invocation text):

| Flag | Action |
|------|--------|
| `--init` or `--remove` | **Stop and redirect.** These flags moved to the sibling command. Tell the user: *"`--init` and `--remove` moved to `/email-sweep:init`. Run `/email-sweep:init` for setup, or `/email-sweep:init --remove` for cleanup."* Do not sweep the inbox. |
| `--all` | Proceed to Pre-flight, then Steps 1-7 with the `--all` query variant. |
| (none) | Proceed to Pre-flight, then Steps 1-7 with the default query. |

## Pre-flight

1. **Load the skill**. Invoke the `email-sweep:email-sweep` skill — this loads the classification heuristics, action safety rules, label taxonomy (`labels.json`), and active standing rules (`standing-rules.json`) from the plugin's `skills/email-sweep/` directory.
2. **Map labels → IDs**. Call `mcp__claude_ai_Gmail__list_labels` once; cache the name→ID map for the session.
3. **Verify both sides are authed to the same Gmail account.** The Gmail MCP and the `gmail-labels` CLI hold separate OAuth tokens — if one rotates or is authed against a different Google account, the sweep silently operates on the wrong mailbox. Run both checks before sweeping:
   - **CLI side:** shell out to `gmail-labels whoami` and capture the printed address.
   - **MCP side:** call `mcp__claude_ai_Gmail__search_threads` with `query: "from:me"`, `pageSize: 1`. Pass the returned `id` to `mcp__claude_ai_Gmail__get_thread` (`format: METADATA`) and read the `from` header of the first message — that's the MCP-authed account.
   - Print both addresses side-by-side (`CLI: X | MCP: Y`). If they don't match, **abort the sweep** — tell the user to re-auth the side that's wrong (`gmail-labels auth` for CLI, or reconnect the Gmail MCP in Claude.ai settings) before retrying.
4. **Cross-check taxonomy**. If any label in `labels.json` is missing from Gmail, warn and offer `gmail-labels add "<name>"`. Do NOT proceed with sweeping until the taxonomy is intact.

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

## Step 1b — Fetch stale @Action queue

Independently of the inbox sweep, surface `@Action` threads that may have rotted. Step 1 catches NEW items; this catches OLD `@Action` items that never got resolved. Runs every sweep (default and `--all`).

Call `mcp__claude_ai_Gmail__search_threads`:
- `query`: `label:"@Action" older_than:7d`
- `pageSize`: `10` (paginate as in Step 1)

Cap at 25 stale threads per sweep — if more, note in summary and surface the oldest 25; the user can run a targeted queue-clearing pass separately.

For each stale thread, pull `mcp__claude_ai_Gmail__get_thread` with `format: METADATA` to capture `from`, `subject`, and message age.

**Caveat on age**: Gmail's `older_than:` filters by message date, not label-application date. A freshly-tagged old thread will appear here even if the user only decided `@Action` yesterday. That's acceptable for v1 — they'll just say "keep." If this becomes noisy, upgrade to a `decisions.jsonl`-backed lookup (latest log entry per `thread_id` whose `labels_applied` contains `@Action`, compute label-age from that timestamp).

**Never auto-clear `@Action`.** This step only surfaces; the user always chooses the disposition (Step 4b).

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
- Stale @Action queue: Q (threads >7d still tagged @Action)
- Obvious auto: A
- Ambiguous (flag for review): B
- Threshold alarm: [yes/no — see Step 1 thresholds]

### Obvious (will auto-apply)
| # | From | Subject | Labels |
|---|------|---------|--------|
...

### Ambiguous (review per sender)
[Grouped by sender — ask per-sender if same treatment as last time or fresh ruling]

### Stale @Action queue (review per thread)
| # | Age | From | Subject |
|---|-----|------|---------|
[One row per stale thread, sorted oldest first]
```

Wait for the user's confirmation / edits on the obvious batch. Default: they'll say "go" and it runs.

## Step 4 — Review the ambiguous batch

For each sender group:
- Surface 1-2 representative threads (subject + snippet)
- Ask: "Same treatment as [last time's label]?" if prior rulings exist in `decisions.jsonl`, else "How should I label this sender's threads today?"
- Accept the answer, apply to all threads from that sender in the batch

## Step 4b — Resolve stale @Action queue

For each stale thread surfaced in Step 1b, present subject + age + snippet and ask for one of four dispositions:

- **keep** — still actionable; label stays, no plan entry
- **done** — action complete; add `{"add_labels": ["@Reference"], "remove_labels": ["@Action", "INBOX"]}` to the apply plan
- **waiting** — sent something, waiting on reply; add `{"add_labels": ["@Waiting"], "remove_labels": ["@Action"]}` to the apply plan
- **trash** — no longer relevant; confirm per the "Never trash without explicit confirmation" rule, then add `{"add_labels": ["TRASH"], "remove_labels": ["@Action", "INBOX"]}`

Batch-accept patterns (e.g., "keep all") are fine if the user calls for them — single keystroke per thread preferred over re-prompting.

Queue dispositions merge into the same apply plan built in Step 5.

## Step 5 — Apply

Write the combined plan (obvious + resolved-ambiguous) to `/tmp/email-sweep-YYYYMMDD.json` using the `gmail-labels.py apply` schema:

```json
[
  {"thread_id": "...", "add_labels": ["@Reference", "Life Admin/Finance"], "remove_labels": ["INBOX"], "description": "..."}
]
```

Run `gmail-labels apply /tmp/email-sweep-YYYYMMDD.json` via Bash (the CLI is installed on PATH during setup — see the repo README). Report the result line (`Applied: X, Errors: Y`).

## Step 6 — Log decisions

For every thread (obvious OR resolved-ambiguous), append one JSON line to the decisions log. Resolve the path in this order:

1. `$EMAIL_SWEEP_HOME/decisions.jsonl` if `EMAIL_SWEEP_HOME` is set
2. `~/.local/share/email-sweep/decisions.jsonl` (default)

Create parent directories if missing. Example line:

```json
{"timestamp": "2026-04-15T21:34:00-07:00", "thread_id": "...", "sender": "...", "subject": "...", "labels_applied": ["@Reference", "Life Admin/Finance"], "decision_source": "auto|human|rule"}
```

- `auto` — matched a clear sender/subject pattern but not an existing standing rule (candidate for future rule-mining)
- `human` — resolved via ambiguous-review in step 4
- `rule` — matched an existing entry in `standing-rules.json`
- `queue` — resolved via stale @Action queue review in step 4b

**Append only. Never rewrite.** One line per thread. Newline-delimited JSON.

## Step 7 — Wrap

Report:
```
/email-sweep complete — YYYY-MM-DD
  Threads swept: N
  Auto: A | Human: H | Rule: R | Queue: Q
  Stale @Action remaining (kept): K
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
