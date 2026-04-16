# email-sweep

A Claude Code skill for a daily, compounding Gmail inbox sweep.

`/email-sweep` runs an end-of-day pass over your Gmail inbox: it auto-labels the obvious threads, batches the ambiguous ones by sender for quick human rulings, and logs every decision to a training log that seeds tomorrow's auto-rules. The system is designed to compound — today's "ambiguous" set gets smaller every week as yesterday's rulings become standing rules.

## Why

The problem isn't inbox workflow — it's classification volume. A 10k+ thread backlog doesn't rebuild because of missing `@Action` SLAs or absent weekly dashboards. It rebuilds because nothing labels the incoming stream consistently, so "unread" becomes a junk drawer and the backlog silently regrows.

Existing tools treat the inbox as a work queue (priority, deferral, reminders). That's premature when the real pain is "I don't know what most of this is, and I'm not going to hand-sort 40 threads every morning."

`email-sweep` goes the other direction: it treats the inbox as a classification problem first. A slash command runs once a day, Claude classifies what it can, you rule on the rest in batched sender-groups, and every decision becomes training data. After ~2 weeks, the classifier's "obvious" set covers most of your daily volume and human rulings trend toward zero.

Design constraints worth knowing up front:

- **Human stays in the loop.** No cron, no background daemon. The slash command is the training signal — removing the human forecloses the compounding loop.
- **One account.** Single Gmail only. No multi-account plumbing.
- **Labels + archive, not send.** The Gmail MCP can draft but not send — this is deliberate and enforced by the protocol, not by this tool.

## How it works

The daily loop, each step mapped to a file in this repo:

1. You run `/email-sweep` in Claude Code (source: `commands/email-sweep.md`).
2. It fetches today's unread threads via the Gmail MCP (`is:unread newer_than:1d`).
3. Each thread is classified in-session against the heuristics in `SKILL.md` and any standing rules in `standing-rules.json`. Threads split into two buckets:
   - **obvious** — matches a standing rule or a clear sender/subject pattern (e.g., `noreply@*.lever.co` → `@Reference` + `Job Search/Application`).
   - **ambiguous** — novel sender, mixed signals, or confidence below ~95%.
4. Obvious threads are summarized in a table for a quick "go" confirmation.
5. Ambiguous threads are grouped by sender and walked one sender-group at a time. You answer once per sender; every thread from that sender in the batch gets the same treatment.
6. The combined plan is written to `/tmp/email-sweep-YYYYMMDD.json` and applied by `scripts/gmail-labels.py apply` — this is the CLI that covers the gap where the Gmail MCP can apply labels but can't create or delete them.
7. Every decision (obvious, human, or rule-matched) appends one line to `training/decisions.jsonl` with `decision_source ∈ {auto, human, rule}`.

That last step is the whole point. The training log is what lets the week-2 rule-miner propose new standing rules from observed patterns, which shrinks tomorrow's ambiguous set.

## Requirements

- **Claude Code CLI** — this is a Claude Code skill + slash command; it only runs inside a Claude Code session.
- **Gmail MCP enabled** in Claude Code. This is what provides the `mcp__claude_ai_Gmail__*` tools the skill calls (search, read, label, unlabel, draft). Enable it via Claude Code's MCP settings.
- **Python 3.10+** with the Google API client libraries, for the label-management CLI:
  ```
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
  ```
- **Google OAuth client credentials** (`credentials.json`) — see OAuth setup below. Required because Gmail's MCP can't create or delete labels, and initial label setup needs the REST API.
- **`jq`** — optional, only used by some workflow niceties. `apt install jq` on Debian/Ubuntu.

## Install

### Manual (symlinks)

Clone the repo somewhere stable (moving it later breaks the symlinks), then wire the files into your Claude Code config:

```bash
git clone https://github.com/<you>/email-sweep.git ~/email-sweep
cd ~/email-sweep

# Skill (SKILL.md + adjacent data files in one directory)
mkdir -p ~/.claude/skills/email-sweep
ln -sf "$PWD/SKILL.md"             ~/.claude/skills/email-sweep/SKILL.md
ln -sf "$PWD/labels.json"          ~/.claude/skills/email-sweep/labels.json
ln -sf "$PWD/standing-rules.json"  ~/.claude/skills/email-sweep/standing-rules.json

# Slash command
mkdir -p ~/.claude/commands
ln -sf "$PWD/commands/email-sweep.md" ~/.claude/commands/email-sweep.md

# Label-management CLI on PATH
mkdir -p ~/.local/bin
ln -sf "$PWD/scripts/gmail-labels.py" ~/.local/bin/gmail-labels
chmod +x "$PWD/scripts/gmail-labels.py"

# Training log directory (contents gitignored — just needs to exist)
mkdir -p training
```

Then merge `settings.fragment.json` into `~/.claude/settings.json` so Claude Code is allowed to call the Gmail MCP tools and the `gmail-labels` CLI. The fragment looks like:

```json
{
  "permissions": {
    "allow": [
      "Bash(gmail-labels:*)",
      "mcp__claude_ai_Gmail__search_threads",
      "mcp__claude_ai_Gmail__get_thread",
      "mcp__claude_ai_Gmail__list_labels",
      "mcp__claude_ai_Gmail__label_thread",
      "mcp__claude_ai_Gmail__unlabel_thread",
      "mcp__claude_ai_Gmail__label_message",
      "mcp__claude_ai_Gmail__unlabel_message",
      "mcp__claude_ai_Gmail__create_draft"
    ]
  }
}
```

If you use `jq`, a reasonable merge is:

```bash
jq -s '.[0] * .[1]' ~/.claude/settings.json settings.fragment.json \
  > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
```

`manifest.json` is kept in the repo for reference — it's the deployment descriptor from the author's manifest-driven tool-bundle system. You don't need it if you're doing manual symlinks.

### OAuth setup

The label-management CLI (`gmail-labels.py`) needs an OAuth token with the `gmail.modify` scope. One-time setup:

1. Create a Google Cloud project and enable the Gmail API.
2. Create an **OAuth 2.0 Client ID** of type "Desktop app" and download the `credentials.json`.
3. Put `credentials.json` somewhere the script can find it. By default it looks at `~/.config/email-sweep/credentials.json`:
   ```bash
   mkdir -p ~/.config/email-sweep
   mv ~/Downloads/credentials.json ~/.config/email-sweep/credentials.json
   ```
   Or point at your own path via env var:
   ```bash
   export EMAIL_SWEEP_CREDENTIALS=/path/to/credentials.json
   ```
4. Run the OAuth flow:
   ```bash
   gmail-labels auth
   ```
   This opens a browser, you consent, and the resulting token is saved to `scripts/token.json` (gitignored).
5. Create the canonical labels in Gmail:
   ```bash
   gmail-labels sync
   ```

## Usage

Two modes:

```
/email-sweep          # default — today's unreads (is:unread newer_than:1d)
/email-sweep --all    # full sweep — everything still in INBOX
```

Default is the daily habit. `--all` is for when you've skipped a few days and need to drain the backlog — the sweep will warn you if volume exceeds the habit threshold.

An abridged session looks like this:

```
## /email-sweep — 2026-04-16

### Summary
- Mode: default
- Threads fetched: 14
- Obvious auto: 9
- Ambiguous (flag for review): 5
- Threshold alarm: no

### Obvious (will auto-apply)
| # | From                             | Subject                  | Labels                            |
|---|----------------------------------|--------------------------|-----------------------------------|
| 1 | jobs-noreply@linkedin.com        | Jobs for you             | Notifications (archive, read)     |
| 2 | noreply@jobs.lever.co            | Application received     | @Reference, Job Search/Application|
| ...                                                                                                 |

### Ambiguous (review per sender)
Group 1/3 — recruiter@acmecorp.com (2 threads)
  - "Quick chat about a Sr TPM role?"
  - "Re: Quick chat about a Sr TPM role?"
  → How should I label this sender's threads today?
```

After each sender ruling, the plan executes and the sweep reports:

```
/email-sweep complete — 2026-04-16
  Threads swept: 14
  Auto: 7 | Human: 5 | Rule: 2
  Errors: 0
  Decisions logged: 14
  Next: run `/email-sweep` again tomorrow EOD
```

Full spec — threshold alarms, decision-log schema, pagination behavior, failure modes — lives in `commands/email-sweep.md`.

## Label taxonomy

Two axes. Every thread gets **one status** label and **one category** label.

**Status** (GTD-inspired, `@`-prefixed so they sort to the top of Gmail's sidebar):

| Label | Meaning |
|---|---|
| `@Action` | Needs a response, decision, or task from you |
| `@Waiting` | Ball is in someone else's court — follow up if stale |
| `@Reference` | Keep for later lookup, no action needed |

**Category** (canonical list in `labels.json`):

| Label | Meaning |
|---|---|
| `Job Search/Recruiter` | Inbound recruiter outreach, sourcing messages |
| `Job Search/Application` | Application confirmations, portal notifications, rejections |
| `Job Search/Interview` | Scheduling, prep materials, interviewer intros |
| `Job Search/Offer` | Offer letters, negotiation, comp |
| `Life Admin/Finance` | Bills, statements, tax docs, receipts |
| `Life Admin/Benefits` | Health insurance, HR, benefits |
| `Life Admin/Wedding` | Vendor comms, RSVPs, venue logistics |
| `Newsletters` | Subscription / bulk content |
| `Notifications` | Automated alerts (GitHub, Linear, calendar, shipping) |

Example: an interview scheduling email from a recruiter is `Job Search/Interview` + `@Action`.

## Customizing for your own inbox

Two files are meant to be edited:

- **`labels.json`** — the canonical taxonomy. The shipped list reflects the author's inbox (job search + life admin dominated). Edit freely. After editing, run `gmail-labels sync` to push the changes to Gmail.
- **`standing-rules.json`** — auto-apply rules. The shipped file contains the author's personal rules (LinkedIn job alerts → `Notifications`, Enwild gear emails → `Newsletters`, a specific phishing domain → spam). **You probably want to start this as `{"rules": []}`** and let it grow as you approve rule proposals during sweeps. The shipped rules are illustrative examples of the format, not defaults to adopt.

Rule format, for reference:

```json
{
  "rules": [
    {
      "match": { "sender": ["noreply@calendly.com"] },
      "category": "Notifications",
      "action": "archive",
      "mark_read": true,
      "created": "2026-04-16",
      "reason": "Calendar notifications — always archive + read"
    }
  ]
}
```

Match supports `sender`, `sender_domain`, and `subject_contains`. Actions supported by `gmail-labels apply`: label additions/removals, `archive` (remove `INBOX`), `mark_read` (remove `UNREAD`), `trash` (add `TRASH`), `spam` (add `SPAM`).

## How the compounding works

Every sweep appends one line per thread to `training/decisions.jsonl`:

```json
{"timestamp":"2026-04-16T21:34:00-07:00","thread_id":"19d7e...","sender":"noreply@calendly.com","subject":"Event scheduled","labels_applied":["@Reference","Notifications"],"decision_source":"human"}
```

`decision_source` is the key field:

- `rule` — matched an existing entry in `standing-rules.json` (no human touched it).
- `auto` — matched a sender/subject pattern inline but isn't covered by a standing rule yet. These are prime candidates for new rules.
- `human` — you ruled on it during the ambiguous-review phase.

After ~2 weeks of sweeps, the log surfaces patterns: "you've labeled 4 threads from `noreply@calendly.com` as Notifications over the past 10 days." That's a rule-mining candidate. The week-2 layer proposes it as a new standing rule at the end of a sweep; you approve or decline; approved rules flow into `standing-rules.json` and auto-fire the next day.

Net effect: your `decision_source` mix trends from mostly `human` in week 1 to mostly `rule`/`auto` by week 3+.

## Roadmap / what's not built yet

Deliberately out of scope, at least for now:

- **Cron / scheduler.** Slash command only. Removing the human from the loop removes the training signal — cron can be added later as a choice; starting with cron forecloses the compounding.
- **`@Waiting` / `@Action` SLA automation.** Requires a scheduler + state machine. Current pain is classification volume, not workflow. Revisit once the backlog is stable.
- **Unsubscribe automation.** Adjacent problem, different tool. The sweep labels and archives — it doesn't manage sender relationships.
- **Weekly dashboards / trend charts.** No MVP value. Maybe after the daily loop has been stable for a month.
- **Multi-account support.** Single Gmail only. Generalize later if ever needed.
- **Automated rule-miner.** The design calls for a week-2 rule-mining pass that diffs `decisions.jsonl` against `standing-rules.json` and proposes additions. Today, proposals happen informally in-session at the end of a sweep when the human-ruling count is high (the command calls out rule-mining candidates). A dedicated, automated pass is the next build.

## Acknowledgments / origin

Extracted from the author's personal `my-claude-tools` monorepo — a manifest-driven collection of Claude Code tool bundles (agents, skills, slash commands, CLIs) deployed via a lifecycle agent. The `manifest.json` in this repo is a leftover from that system — it describes how the files deploy into `~/.claude/` and `~/.local/bin/` via the parent repo's lifecycle agent. Harmless if you ignore it; useful if you want to see the intended deployment shape.

License: MIT (see `LICENSE`).
