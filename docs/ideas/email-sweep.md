# email-sweep

## Problem Statement

How might Jason keep his inbox trending toward zero as a habit — so the 13,368-thread backlog never rebuilds — using email-admin as the labeling engine?

## Recommended Direction

**Compounding classifier**, starting from a habit-first skeleton. Ship a daily `/email-sweep` slash command that auto-labels obvious threads, batches the rest by sender for quick human rulings, and funnels those rulings into a training log that mines new auto-rules over time. Tomorrow's "ambiguous" set is smaller than today's.

The habit-first skeleton (week 1) proves the daily rhythm and delivers immediate relief. The rule-miner layer (week 2) is what makes the system compound — the flagged-items-become-training-data mechanic is the whole point of the request, not a nice-to-have. A threshold alarm (force review if inbox > 25 unread) guards against silent backlog rebuild on skipped days.

Explicitly rejecting the "inbox as work queue" direction (@Action SLAs, @Waiting re-surfacing, weekly dashboards). Those are seductive but premature — the current pain is classification volume, not workflow. Shipping them first would delay the habit loop by 2-3x.

## Key Assumptions to Validate

- [ ] **Claude's in-session classification judgment is a good-enough "confidence score."** Test: run `/email-sweep` for 1 week; measure how often auto-labeled threads need correction. If > 5%, add an explicit confidence field to the classification plan.
- [ ] **Sender-grouping collapses review volume meaningfully.** Test: on a 30-thread ambiguous day, measure decisions-per-sweep with vs. without grouping. Target: ≥ 4x collapse.
- [ ] **The training log is actually read by the next run.** Test: a rule added Monday via approved proposal should auto-handle the same sender-pattern Tuesday without re-asking. If this loop breaks, the whole "compounding" premise dies.
- [ ] **Daily volume stays in the 20–50 threads/day range.** Test: after 2 weeks, if steady-state is > 75 threads/day, the sweep becomes too long for a daily habit and cadence needs to shift (or auto-threshold needs to be lower).
- [ ] **The user will actually approve/reject rule proposals.** Test: after 2 weeks, count proposals generated vs. resolved. If < 50% resolved, the rule-miner degrades to noise and the system reverts to baseline V1 behavior.

## MVP Scope

**Week 1 — Habit skeleton**
- `/email-sweep` slash command in `~/.claude/commands/` → fetches today's inbox threads, classifies them via Claude in-session, writes a plan to `/tmp/email-sweep-YYYYMMDD.json`, applies via `gmail-labels.py apply`.
- Ambiguous items are batched by sender (V6) and walked inline in the Claude session.
- Threshold alarm: if inbox has > 25 unread threads at sweep time, force full review instead of silent auto-mode.
- All decisions append to `tools/email-admin/training/decisions.jsonl` (schema: `{timestamp, thread_id, sender, subject, labels_applied, decision_source}` where decision_source ∈ `auto | human | rule`).

**Week 2 — Compounding layer**
- Rule-miner: after each sweep, diff new decisions against `standing-rules.json`, propose rule additions ("you labeled 4 `noreply@calendly.com` threads as Notifications today — add standing rule?").
- Approved rules flow into `standing-rules.json`; next run's classifier context includes them so the same sender pattern auto-labels without asking.

**In scope:** the slash command, sender-grouped review UX, threshold alarm, decisions.jsonl, rule-proposal flow.
**Out of scope:** everything in "Not Doing" below.

## Not Doing (and Why)

- **Numeric confidence scores on the classifier** — Claude's in-session judgment is the proxy. Add a real score only if the week-1 miscategorization rate proves it's needed.
- **@Waiting / @Action SLA automation** — requires a scheduler + state machine. Current pain is classification volume, not workflow. Defer until backlog is stable.
- **Weekly dashboard / trend charts** — premium feature, no MVP value. Revisit once the daily loop is stable for a month.
- **Cron-based auto-triggering** — slash command only. Cron removes the human from the loop, which *is* the loop's training data. Adding cron later is a choice; starting with cron forecloses the training signal.
- **Unsubscribe automation** — adjacent problem, different tool. The sweep's job is to *label and archive*, not manage sender relationships. Out of scope entirely.
- **Multi-account support** — single Gmail account only. Generalize later if it's ever needed.

## Open Questions

- Where should `decisions.jsonl` and `standing-rules.json` live? Inside `tools/email-admin/training/`, or a separate `tools/email-admin-training/` bundle? (Lean: inside email-admin — same tool, same lifecycle, no coordination cost.)
- Should the sweep operate on "threads since last sweep" (stateful, robust to skipped days) or "threads received today" (stateless, simpler)? Stateful is clearly better for the skipped-day case, but adds a state file to maintain.
- Do flagged items need a "punt to tomorrow" option, or are all decisions final-at-sweep? Punting is humane but creates a hidden backlog of its own.
- How does the rule-miner handle *negative* rules ("this sender is usually Newsletters, but not when the subject contains `Invoice`")? First version should probably only propose positive rules and let the human hand-edit negatives in `standing-rules.json` directly.
