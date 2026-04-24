---
description: Daily end-of-day Gmail inbox sweep — auto-label the obvious, flag the rest for review, log decisions for future automation. Use `--init` for first-run setup (OAuth, CLI, label taxonomy) and `--remove` to clean up the shims `--init` created.
---

You are running the user's `/email-sweep`. Behavior depends on the flag:

- **No flag**: daily end-of-day sweep. Classify today's unreads, confirm obvious batch, walk ambiguous by sender, apply labels, log decisions. Target: keep the inbox trending toward zero as a habit.
- **`--all`**: full inbox sweep (read + unread, any age) — weekly catch-up.
- **`--init`**: first-run setup. Detect missing pieces (CLI on PATH, OAuth credentials, token, label taxonomy, permissions) and fix them or walk the user through fixing them. Idempotent — safe to re-run.
- **`--remove`**: cleanup. Remove the user-level shims that `--init` created (CLI symlink, command symlink). Never auto-touches sensitive state — OAuth credentials, decisions log, settings.json permissions, and synced Gmail labels stay put with printed manual-cleanup guidance.

## Step 0 — Parse flag and dispatch

Inspect `$ARGUMENTS` (or the user's invocation text):

| Flag | Action |
|------|--------|
| `--init` | **Skip the daily-sweep pre-flight and Steps 1-7.** Jump to the **Init Mode** section at the bottom of this file. Do not sweep the inbox. |
| `--remove` | **Skip the daily-sweep pre-flight and Steps 1-7.** Jump to the **Remove Mode** section at the bottom of this file. Do not sweep the inbox. |
| `--all` | Proceed to Pre-flight, then Steps 1-7 with the `--all` query variant. |
| (none) | Proceed to Pre-flight, then Steps 1-7 with the default query. |

If `--init` or `--remove` was passed, everything below (Pre-flight through Step 7) does **not** apply this run — only the corresponding mode section does. If both flags appear, prefer `--remove` (cleanup wins).

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

---

# Init Mode (`--init`)

First-run setup. Runs when the user invokes `/email-sweep --init`. **Does not sweep the inbox.** Idempotent — re-running only fixes what's missing.

At the end of a successful run, the user has:
- `gmail-labels` CLI on PATH
- Google OAuth credentials installed and a valid token for `gmail.modify`
- Canonical label taxonomy synced in Gmail
- Claude Code permissions merged so the daily sweep runs without per-call prompts
- A personalized `labels.json` / `standing-rules.json` if they chose to customize
- The MCP-side and CLI-side auth matched to the same Gmail account

## Output format

Every `--init` run produces three kinds of output: an opening banner once, a status line per gate as each one finishes, and a final report. Keep it tight — this is a short run, not a novel. Never dump raw Python tracebacks; translate errors into one-sentence plain English.

### Opening banner

Print once, at the very start:

```
/email-sweep --init — first-run setup
Checking 12 gates. One step (Google OAuth consent) may need your browser.
Legend: ✓ passed · → fixing · ⚠ needs you · ✗ blocking
```

### Per-gate status line

One line per gate, printed as each one finishes. Standard form:

```
[N/11] <Gate name> — <symbol> <one-line outcome>
```

Examples (showing the variety of outcomes):

```
[1/12] Python + deps — ✓ Python 3.12.3, all imports present
[2/12] CLI on PATH — ✓ ~/.local/bin/gmail-labels → $PLUGIN_ROOT/scripts/gmail-labels.py
[4/12] Claude Code permissions — ✓ all required perms present
[6/12] OAuth token — → fixing (opening browser for consent)
[6/12] OAuth token — ✓ authenticated as jason.garcia24@gmail.com
[7/12] Account match — ✓ CLI = MCP = jason.garcia24@gmail.com
[9/12] Sync labels to Gmail — ✓ 16 labels (0 new, 16 existing)
```

If a gate does real work (Gate 3 jq merge, Gate 5 browser auth, Gate 8 sync), print a `→ fixing` line when starting and a final `✓` / `⚠` / `✗` line when done — so the user sees motion instead of a long pause.

### Remediation block (when a gate is ⚠ or ✗)

Follow the status line with an indented block. Three required fields: **What's wrong** (one sentence), **Next step** (numbered concrete actions), **Blocking** (`yes` or `no`).

```
[5/12] OAuth credentials — ⚠ needs you
  What's wrong:  No OAuth client at ~/.config/email-sweep/credentials.json.
  Next step (one-time, ~5 min):
    1. console.cloud.google.com → create project → enable Gmail API.
    2. OAuth consent screen → External → add yourself as a Test user.
       Add scope: https://www.googleapis.com/auth/gmail.modify
    3. Credentials → Create → OAuth client ID → Desktop app → download JSON.
    4. Drop it in ~/Downloads and reply "done" — I'll move it into place.
  Blocking: yes — gates 6-12 paused until resolved.
```

Rules:
- **What's wrong:** plain English, one sentence. Never a stack trace.
- **Next step:** ordered list of concrete actions (commands or click-paths). Each step should be independently runnable. If the fix is a single command, one step is fine.
- **Blocking:** `yes` if the rest of init can't proceed (Gate 4 creds, Gate 5 token, Gate 6 account mismatch). `no` if it's optional (Gate 3 perms, Gate 10 rules).
- For `✗` (hard fail), append one closing line: `Aborting init. Re-run /email-sweep:sweep --init after fixing.`

### Final report

One of three shapes depending on outcome.

**Success — all 11 ✓:**

```
═══════════════════════════════════════════════════════════
 /email-sweep --init — complete (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

All 12 gates passed.

Account:   <email> (CLI = MCP)
Labels:    N synced (M new, K existing)
Rules:     R loaded from standing-rules.json
Log dir:   <path> (empty — seeded at first sweep)

Next (after Gate 3 installed the short-form alias, either form works):
  /email-sweep               daily — today's unreads
  /email-sweep --all         weekly — full inbox catch-up
```

**Partial — at least one ⚠, no ✗:**

```
═══════════════════════════════════════════════════════════
 /email-sweep --init — incomplete (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

Passed:     [1] [2] [3] [6] [7] [8] [9] [10] [11] [12]
Needs you:  [4] Claude Code permissions — jq merge not run
            [5] OAuth credentials — drop file in ~/Downloads and reply "done"

Re-run /email-sweep:sweep --init after resolving. It's idempotent — already-passed gates get confirmed quickly.
```

**Aborted — any ✗:**

```
═══════════════════════════════════════════════════════════
 /email-sweep --init — aborted at gate [N] (YYYY-MM-DD)
═══════════════════════════════════════════════════════════

Blocking:       [N] <gate name> — <one-line reason>
Passed so far:  [1] [2] ... [N-1]

Fix the blocking gate (see remediation above) and re-run /email-sweep:sweep --init.
```

## Finding plugin-root

Several gates need the plugin root path (where `scripts/gmail-labels.py` and `skills/email-sweep/labels.json` live). Resolve it once at the start:

```bash
PLUGIN_ROOT="$(find ~/.claude/plugins -type d -path '*/marketplaces/*/jason-email-sweep' 2>/dev/null | head -1)"
# Fallback to dev-clone locations if marketplace install isn't the source of truth
[ -z "$PLUGIN_ROOT" ] && PLUGIN_ROOT="$(ls -d ~/code/email-sweep ~/Documents/email-sweep 2>/dev/null | head -1)"
```

Abort with a clear error if `$PLUGIN_ROOT/scripts/gmail-labels.py` doesn't exist — the plugin install is broken in a way `--init` can't fix.

## Gate 1 — Python + Google API deps

- Run `python3 --version`. Require 3.10+.
- Probe: `python3 -c "import googleapiclient, google.auth, google_auth_oauthlib"`.
- If `ImportError`: print
  ```
  pip install --user google-api-python-client google-auth-httplib2 google-auth-oauthlib
  ```
  and wait for the user to confirm before re-probing. Do **not** run pip yourself — the user's Python environment is theirs to manage.

## Gate 2 — CLI symlink on PATH

- Check `~/.local/bin/gmail-labels` exists, is a symlink, and points at `$PLUGIN_ROOT/scripts/gmail-labels.py`.
- If missing or pointing elsewhere:
  ```bash
  chmod +x "$PLUGIN_ROOT/scripts/gmail-labels.py"
  ln -sf "$PLUGIN_ROOT/scripts/gmail-labels.py" ~/.local/bin/gmail-labels
  ```
- Verify: `gmail-labels --help` returns exit 0.
- Verify `~/.local/bin` is on PATH (`echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin"`). If not, tell the user to add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` or `~/.zshrc` and open a fresh shell.

## Gate 3 — Short-form slash command alias

Enables bare `/email-sweep` invocation alongside the plugin-namespaced `/email-sweep:sweep`. Both resolve to the same file, so no drift.

- Target: `~/.claude/commands/email-sweep.md`. Source: `$PLUGIN_ROOT/commands/sweep.md`.
- If target exists AND is a symlink pointing at source: ✓ already set.
- If target is missing:
  ```bash
  mkdir -p ~/.claude/commands
  ln -sf "$PLUGIN_ROOT/commands/sweep.md" ~/.claude/commands/email-sweep.md
  ```
- If target exists but is a regular file OR a symlink pointing elsewhere: **stop and confirm before overwriting** — the user may have a custom command there. Print the conflict (what's at the path, what it points to) and ask `Overwrite? (y/N)`. Default no.
- Verify after creation: `test -L ~/.claude/commands/email-sweep.md && readlink ~/.claude/commands/email-sweep.md` returns the source path.
- This gate is non-blocking: if the user declines, `/email-sweep:sweep` still works — they just lose the short form. Record as `⚠ needs you` and continue.
- Caveat to document: if the plugin is ever uninstalled, this symlink dangles. `/email-sweep` will error until removed. No auto-cleanup today.

## Gate 4 — Claude Code permissions

- Parse `~/.claude/settings.json` with `jq '.permissions.allow // []'`.
- Required entries (read `$PLUGIN_ROOT/settings.fragment.json` for the authoritative list):
  - `Bash(gmail-labels:*)`
  - `mcp__claude_ai_Gmail__search_threads`
  - `mcp__claude_ai_Gmail__get_thread`
  - `mcp__claude_ai_Gmail__list_labels`
  - `mcp__claude_ai_Gmail__label_thread` / `unlabel_thread`
  - `mcp__claude_ai_Gmail__label_message` / `unlabel_message`
  - `mcp__claude_ai_Gmail__create_draft`
- If any missing, print the exact jq-merge command from the README and ask the user to run it themselves:
  ```bash
  cp ~/.claude/settings.json ~/.claude/settings.json.bak
  jq -s '
    (.[0].permissions.allow // []) as $a
    | (.[1].permissions.allow // []) as $b
    | .[0] * .[1]
    | .permissions.allow = ($a + $b | unique)
  ' ~/.claude/settings.json "$PLUGIN_ROOT/settings.fragment.json" \
    > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
  ```
- **Never rewrite `settings.json` without an explicit "yes" from the user.** Modifying it silently inside a slash command is a trust violation.
- Missing perms don't block Init Mode (the user can approve MCP calls interactively). Note what's missing and continue.

## Gate 5 — OAuth credentials file

Target path: `$EMAIL_SWEEP_CREDENTIALS` if set, else `~/.config/email-sweep/credentials.json`.

1. If the target exists: `jq . "$target"` to verify it parses as JSON. ✓.
2. If missing:
   - `mkdir -p ~/.config/email-sweep`.
   - Look for a downloaded file: `ls ~/Downloads/credentials.json ~/Downloads/client_secret_*.json 2>/dev/null`. If any match, show the path(s) and ask: *"Found credentials.json in ~/Downloads. Move it to `~/.config/email-sweep/credentials.json`?"* Move on "yes."
   - If no candidate, print the Google Cloud Console walkthrough (one screen, numbered):
     1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a new project (e.g. `email-sweep`) or reuse an existing one.
     2. APIs & Services → Library → enable **Gmail API**.
     3. APIs & Services → OAuth consent screen → External → add your own email under *Test users* → add scope `https://www.googleapis.com/auth/gmail.modify`.
     4. APIs & Services → Credentials → Create Credentials → OAuth client ID → **Desktop app** → download the JSON.
     5. Save as `~/.config/email-sweep/credentials.json` (or drop it in `~/Downloads` and tell me "done" — I'll move it).
   - Wait for the user to say they've placed the file. Re-check the target path.
3. Validate: the file must be a JSON object with a top-level key `installed` (desktop OAuth client shape). If it has `web` instead, the user created the wrong client type — walk them back to step 4 of the walkthrough.

## Gate 6 — OAuth token

- Probe: `gmail-labels list` (exits 0 only if token is valid).
- If it fails with an auth/credentials error (matches `not found` or `invalid_grant`), run `gmail-labels auth`.
  - This spawns a local HTTP server and opens the default browser for OAuth consent. In a slash-command context the Bash call may block until the user completes the flow — that's fine up to ~2 min.
  - **Fallback if the tool call hangs or the browser doesn't open:** tell the user to run `gmail-labels auth` in their own terminal and report back; once they say "done," re-probe.
- Re-probe `gmail-labels list` after auth. Loop only once — if it still fails, surface the error and stop; don't retry blindly.

## Gate 7 — MCP ↔ CLI account match

Same dual-whoami check the daily pre-flight does:

- **CLI:** `gmail-labels whoami` → capture email.
- **MCP:** `mcp__claude_ai_Gmail__search_threads` with `query: "from:me"`, `pageSize: 1`. Take the returned thread id, call `mcp__claude_ai_Gmail__get_thread` with `format: METADATA`, read the `from` header of the first message.
- Print side-by-side: `CLI: <email> | MCP: <email>`.
- If mismatch: **abort Init Mode**. Possible causes and fixes:
  - CLI authed against the wrong account → delete the token (`rm "$PLUGIN_ROOT/scripts/token.json"`) and re-run `gmail-labels auth` signed into the intended Google account.
  - MCP authed against the wrong account → reconnect the Gmail connector at [claude.ai/settings/connectors](https://claude.ai/settings/connectors).
  - Fix the mismatch, then re-run `/email-sweep --init`.

## Gate 8 — Label taxonomy review (optional)

- Read `$PLUGIN_ROOT/skills/email-sweep/labels.json` and list its contents.
- Prompt: *"This is the canonical label taxonomy. Want to customize before syncing to Gmail? (y/N, default: keep as-is)"*
- If yes: tell the user to edit `$PLUGIN_ROOT/skills/email-sweep/labels.json`, then say "done." Re-read and show the diff before moving on.
- If no: continue.

## Gate 9 — Sync labels to Gmail

- Run `gmail-labels sync`. Capture output (lists created labels).
- Verify: `mcp__claude_ai_Gmail__list_labels` → every name in `labels.json` should be present. If any are still missing, re-run sync once; if still missing, surface the Gmail API error.

## Gate 10 — Decisions log directory

- If `$EMAIL_SWEEP_HOME` is set: `mkdir -p "$EMAIL_SWEEP_HOME"`.
- Else: `mkdir -p ~/.local/share/email-sweep`.
- Do **not** create `decisions.jsonl` — leave that for the first real sweep so we don't seed the training log with empty state.

## Gate 11 — Standing rules review (optional)

- Read `$PLUGIN_ROOT/skills/email-sweep/standing-rules.json` and show the entries.
- Note: these reflect the plugin author's inbox and likely don't all apply to the user.
- Prompt: *"These ship with the plugin. Prune now, or leave for later — the daily sweep will propose new rules from your decisions either way?"*
- If prune: let the user edit and confirm. Don't block on this — default to "leave" if ambiguous.

## Gate 12 — Final verification + report

Dry-run the first two steps of the daily sweep to prove end-to-end wiring works:

- `mcp__claude_ai_Gmail__search_threads` with `query: "in:inbox"`, `pageSize: 1` — proves MCP reads work.
- `mcp__claude_ai_Gmail__list_labels` — proves taxonomy lookup works.

On success, print `[12/12] Final verification — ✓ MCP read + label lookup ok`, then the **Final report** per `## Output format` above. Pick the success / partial / aborted shape based on how gates 1-11 turned out:

- Any `✗` → aborted shape (init stopped early at that gate).
- At least one `⚠`, no `✗` → partial shape (list every `⚠` in the "Needs you" block with its one-line remediation).
- All `✓` → success shape.

## Init Mode safety rules

- **Never** auto-mutate `~/.claude/settings.json` — it's the user's config.
- **Never** run `pip install` yourself — print the command and wait.
- **Never** download or commit OAuth credentials to the repo.
- **Never** proceed past Gate 7 (account match) on a mismatch — silent cross-account activity is the worst failure mode this plugin has.
- Each gate is independent: if Gate N fails non-fatally, note it and continue; report at the end. If a gate is fatal (deps missing, creds absent, account mismatch), stop and tell the user exactly what to do next.

---

# Remove Mode (`--remove`)

Cleanup pass for `--init`. Removes the user-level shims that `--init` Gates 2 and 3 create (the `gmail-labels` CLI symlink and the short-form command alias). **Does not sweep the inbox. Never auto-touches sensitive state** — OAuth credentials, decisions log, settings.json permissions, and synced Gmail labels stay put with printed manual-cleanup guidance.

At the end of a successful run, the user has:

- `~/.local/bin/gmail-labels` symlink removed (only if it points at email-sweep)
- `~/.claude/commands/email-sweep.md` symlink removed (only if it points at email-sweep) — short-form `/email-sweep` invocation goes away; `/email-sweep:sweep` (namespaced) still works until `/plugin uninstall`
- A printed checklist for what to do about credentials, decisions log, settings permissions, and Gmail labels — left alone by default

This command does NOT actually invoke `/plugin uninstall` itself — that's a Claude Code slash command, not a Bash command. Cleanup mode only handles the file-system shim removal; full plugin teardown requires the two slash commands at the end.

## Implementation

Run the following Bash sequence:

```bash
removed=0
foreign=0
not_symlink=0
absent=0

remove_symlink() {
  local dst="$1"
  local label="$2"
  if [ -L "$dst" ]; then
    local target=$(readlink "$dst")
    if echo "$target" | grep -qE '(email-sweep|jason-email-sweep)'; then
      rm "$dst"
      echo "  ✓ removed: $label"
      removed=$((removed+1))
    else
      echo "  ⚠ kept: $label — symlink points elsewhere ($target); not touching"
      foreign=$((foreign+1))
    fi
  elif [ -e "$dst" ]; then
    echo "  ⚠ kept: $label — exists but is not a symlink (real file); not touching"
    not_symlink=$((not_symlink+1))
  else
    absent=$((absent+1))
  fi
}

echo "Symlinks:"
remove_symlink "$HOME/.local/bin/gmail-labels" "~/.local/bin/gmail-labels"
remove_symlink "$HOME/.claude/commands/email-sweep.md" "~/.claude/commands/email-sweep.md"

echo ""
echo "email-sweep --remove complete — $removed removed, $absent already absent, $foreign kept (foreign), $not_symlink kept (not a symlink)."
echo ""
echo "Items NOT touched (manage them yourself if you want full cleanup):"
echo ""
echo "  ~/.config/email-sweep/credentials.json   ← OAuth client (sensitive — keeping it means no new Cloud Console setup if you reinstall)"
echo "  ~/.local/share/email-sweep/decisions.jsonl   ← Training log (deleting loses your standing-rule history)"
echo "  ~/.claude/settings.json                  ← Has email-sweep permission entries from --init Gate 4"
echo "  Your Gmail labels                         ← The taxonomy synced via gmail-labels sync stays in your inbox"
echo ""
echo "If you want full cleanup, manually:"
echo "  rm -rf ~/.config/email-sweep ~/.local/share/email-sweep"
echo "  Edit ~/.claude/settings.json to remove the email-sweep permissions block (back up first: cp ~/.claude/settings.json ~/.claude/settings.json.bak)"
echo "  Delete unwanted labels via Gmail Settings → Labels"
echo ""
echo "To uninstall the plugin itself:"
echo ""
echo "  /plugin uninstall email-sweep@jason-email-sweep"
echo "  /plugin marketplace remove jason-email-sweep"
echo ""
echo "(Skipping those keeps the plugin installed — symlinks can be recreated with /email-sweep:sweep --init.)"
```

Report the command output verbatim.

## Remove Mode safety rules

- **Never** auto-edit `~/.claude/settings.json` — it's the user's config; print remediation instead.
- **Never** auto-delete OAuth credentials or tokens (`~/.config/email-sweep/credentials.json`, `<plugin-root>/scripts/token.json`) — print warnings + manual `rm` commands.
- **Never** auto-delete `decisions.jsonl` — it's training data; the user decides whether to keep their rule-mining history.
- **Never** auto-delete labels from Gmail — those are user data created in their inbox.
- Only remove symlinks that point at email-sweep paths — leave foreign symlinks alone.
- **Never** auto-invoke `/plugin uninstall` — print the command and let the user decide.
