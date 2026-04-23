#!/usr/bin/env python3
"""Gmail label management CLI.

Creates, deletes, and syncs Gmail labels from a config file, filling the gap
left by the Gmail MCP (which can apply labels but not create/delete them).

Usage:
    gmail-labels.py auth                        # One-time OAuth flow for Gmail scope
    gmail-labels.py whoami                      # Print the authenticated Gmail address
    gmail-labels.py list                        # List all user-defined labels
    gmail-labels.py sync                        # Create missing / optionally remove extra labels
    gmail-labels.py add "Label Name"            # Create a single label
    gmail-labels.py remove "Label Name"         # Delete a single label
    gmail-labels.py nuke --confirm              # Delete ALL user-defined labels

Credentials:
    - credentials.json  — Google OAuth client credentials (desktop-app OAuth client).
                          Default path: $EMAIL_SWEEP_CREDENTIALS, else
                          ~/.config/email-sweep/credentials.json.
    - token.json        — Saved OAuth token (auto-created by 'auth' subcommand).
                          Stored alongside this script at scripts/token.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_DIR = SCRIPT_DIR.parent
LABELS_CONFIG = TOOL_DIR / "skills" / "email-sweep" / "labels.json"

# Credentials: override with $EMAIL_SWEEP_CREDENTIALS, else ~/.config/email-sweep/credentials.json.
# Token lives alongside this script.
CREDENTIALS_FILE = Path(
    os.environ.get(
        "EMAIL_SWEEP_CREDENTIALS",
        str(Path.home() / ".config" / "email-sweep" / "credentials.json"),
    )
)
TOKEN_FILE = SCRIPT_DIR / "token.json"


def get_credentials() -> Credentials:
    """Load or refresh OAuth2 credentials. Raises if not authenticated."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            return creds
        except google.auth.exceptions.RefreshError:
            # Refresh token revoked — delete stale token and re-auth below
            TOKEN_FILE.unlink(missing_ok=True)
            creds = None

    if creds and creds.valid:
        return creds

    # No valid credentials — run interactive OAuth flow
    if not CREDENTIALS_FILE.exists():
        raise RuntimeError(
            f"OAuth client credentials not found at: {CREDENTIALS_FILE}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE), SCOPES
    )
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    return creds


def get_gmail_service():
    """Build and return a Gmail API service instance."""
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def get_user_labels(service) -> list[dict]:
    """Fetch all user-defined labels (excludes system labels)."""
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])
    return [l for l in labels if l.get("type") == "user"]


def load_label_config() -> list[str]:
    """Load the label taxonomy from labels.json."""
    if not LABELS_CONFIG.exists():
        print(f"Error: {LABELS_CONFIG} not found", file=sys.stderr)
        sys.exit(1)
    data = json.loads(LABELS_CONFIG.read_text())
    return data.get("labels", [])


# --- Subcommands ---

def do_auth(_args):
    """Run the OAuth2 authorization flow for Gmail scope."""
    if not CREDENTIALS_FILE.exists():
        print(f"Error: credentials.json not found at {CREDENTIALS_FILE}", file=sys.stderr)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"Authentication successful. Token saved to {TOKEN_FILE}")


def do_whoami(_args):
    """Print the Gmail address the CLI is authenticated against."""
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    print(profile["emailAddress"])


def do_list(_args):
    """List all user-defined Gmail labels."""
    service = get_gmail_service()
    labels = get_user_labels(service)

    if not labels:
        print("No user-defined labels found.")
        return

    labels.sort(key=lambda l: l["name"])
    print(f"User-defined labels ({len(labels)}):\n")
    for l in labels:
        print(f"  {l['name']}  (id: {l['id']})")


def do_sync(_args):
    """Sync Gmail labels to match labels.json config."""
    service = get_gmail_service()
    desired = load_label_config()
    existing = get_user_labels(service)
    existing_names = {l["name"]: l["id"] for l in existing}

    # Sort so parents ("Job Search") come before children ("Job Search/Application")
    desired_sorted = sorted(desired, key=lambda n: n.count("/"))

    # Create missing labels
    created = []
    for name in desired_sorted:
        if name not in existing_names:
            body = {
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }
            result = service.users().labels().create(userId="me", body=body).execute()
            existing_names[name] = result["id"]
            created.append(name)
            print(f"  + Created: {name}")

    # Report extra labels (don't delete by default)
    desired_set = set(desired)
    extras = [l["name"] for l in existing if l["name"] not in desired_set]

    if created:
        print(f"\nCreated {len(created)} label(s).")
    else:
        print("All labels already exist.")

    if extras:
        print(f"\n{len(extras)} label(s) in Gmail not in config:")
        for name in sorted(extras):
            print(f"  ? {name}")
        print("Run 'remove \"<name>\"' to delete, or add to labels.json to keep.")


def do_add(args):
    """Create a single Gmail label."""
    service = get_gmail_service()
    name = args.name
    existing = get_user_labels(service)
    existing_names = {l["name"] for l in existing}

    if name in existing_names:
        print(f"Label already exists: {name}")
        return

    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    result = service.users().labels().create(userId="me", body=body).execute()
    print(f"Created: {result['name']}  (id: {result['id']})")

    # Offer to add to config
    config_labels = load_label_config()
    if name not in config_labels:
        config_labels.append(name)
        config_labels.sort()
        LABELS_CONFIG.write_text(json.dumps({"labels": config_labels}, indent=2) + "\n")
        print(f"Added to {LABELS_CONFIG.name}")


def do_remove(args):
    """Delete a single Gmail label."""
    service = get_gmail_service()
    name = args.name
    existing = get_user_labels(service)
    match = [l for l in existing if l["name"] == name]

    if not match:
        print(f"Label not found: {name}")
        return

    label_id = match[0]["id"]
    service.users().labels().delete(userId="me", id=label_id).execute()
    print(f"Deleted: {name}")


def do_nuke(args):
    """Delete ALL user-defined Gmail labels."""
    if not args.confirm:
        print("This will delete ALL user-defined labels. Pass --confirm to proceed.",
              file=sys.stderr)
        sys.exit(1)

    service = get_gmail_service()
    labels = get_user_labels(service)

    if not labels:
        print("No user-defined labels to delete.")
        return

    print(f"Deleting {len(labels)} label(s)...\n")
    deleted = 0
    errors = 0
    for l in labels:
        try:
            service.users().labels().delete(userId="me", id=l["id"]).execute()
            print(f"  - Deleted: {l['name']}")
            deleted += 1
        except Exception as e:
            print(f"  ! Failed to delete {l['name']}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nDone. Deleted: {deleted}, Errors: {errors}")


def get_label_name_to_id(service) -> dict[str, str]:
    """Build a mapping of label name → label ID (user + system labels)."""
    user_labels = get_user_labels(service)
    mapping = {l["name"]: l["id"] for l in user_labels}
    # Add system labels
    for sys_label in ["INBOX", "TRASH", "SPAM", "STARRED", "UNREAD",
                      "IMPORTANT", "CHAT", "DRAFT", "SENT"]:
        mapping[sys_label] = sys_label
    return mapping


def do_apply(args):
    """Apply a triage action plan from a JSON file.

    The JSON file should contain a list of actions:
    [
        {
            "thread_id": "19d7e694258b0664",
            "add_labels": ["Life Admin/Finance", "@Action"],
            "remove_labels": ["UNREAD", "INBOX"],
            "description": "Credit Karma — collection account"
        },
        ...
    ]

    remove_labels: ["UNREAD"] = mark read, ["INBOX"] = archive,
                   ["UNREAD", "INBOX"] = mark read + archive
    To trash: add_labels: ["TRASH"]
    """
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    actions = json.loads(plan_path.read_text())
    service = get_gmail_service()
    label_map = get_label_name_to_id(service)

    success = 0
    errors = 0
    for i, action in enumerate(actions, 1):
        thread_id = action["thread_id"]
        desc = action.get("description", thread_id)
        add = action.get("add_labels", [])
        remove = action.get("remove_labels", [])

        # Resolve label names to IDs
        add_ids = []
        for name in add:
            if name in label_map:
                add_ids.append(label_map[name])
            else:
                print(f"  ! Unknown label '{name}', skipping for {desc}", file=sys.stderr)

        remove_ids = []
        for name in remove:
            if name in label_map:
                remove_ids.append(label_map[name])
            else:
                print(f"  ! Unknown label '{name}', skipping for {desc}", file=sys.stderr)

        try:
            body = {}
            if add_ids:
                body["addLabelIds"] = add_ids
            if remove_ids:
                body["removeLabelIds"] = remove_ids

            if body:
                service.users().threads().modify(
                    userId="me", id=thread_id, body=body
                ).execute()

            action_parts = []
            if add:
                action_parts.append(f"+{','.join(add)}")
            if remove:
                action_parts.append(f"-{','.join(remove)}")
            action_str = " ".join(action_parts)

            print(f"  [{i}/{len(actions)}] {desc} → {action_str}")
            success += 1
        except Exception as e:
            print(f"  ! [{i}/{len(actions)}] Failed: {desc} — {e}", file=sys.stderr)
            errors += 1

    print(f"\nDone. Applied: {success}, Errors: {errors}")


def main():
    parser = argparse.ArgumentParser(
        description="Gmail label management CLI for the email-sweep skill."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # auth
    sub.add_parser("auth", help="Run OAuth2 authorization flow for Gmail")

    # whoami
    sub.add_parser("whoami", help="Print the authenticated Gmail address")

    # list
    sub.add_parser("list", help="List all user-defined labels")

    # sync
    sub.add_parser("sync", help="Sync labels to match labels.json config")

    # add
    p = sub.add_parser("add", help="Create a single label")
    p.add_argument("name", help="Label name (e.g., 'Job Search/Networking')")

    # remove
    p = sub.add_parser("remove", help="Delete a single label")
    p.add_argument("name", help="Label name to delete")

    # nuke
    p = sub.add_parser("nuke", help="Delete ALL user-defined labels")
    p.add_argument("--confirm", action="store_true", help="Required to actually delete")

    # apply
    p = sub.add_parser("apply", help="Apply a triage action plan from JSON")
    p.add_argument("plan", help="Path to JSON action plan file")

    args = parser.parse_args()

    dispatch = {
        "auth": do_auth,
        "whoami": do_whoami,
        "list": do_list,
        "sync": do_sync,
        "add": do_add,
        "remove": do_remove,
        "nuke": do_nuke,
        "apply": do_apply,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
