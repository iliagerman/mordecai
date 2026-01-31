---
name: himalaya
description: "CLI to manage emails via IMAP/SMTP. Use for listing, reading, writing, replying, forwarding, searching, and organizing emails. Activate when user asks about email, inbox, messages, or wants to send/read mail."
allowed-tools: Bash,Read,Write
requires:
  bins:
    - himalaya
  config:
    - name: GMAIL
      prompt: Gmail email address for the account to access
      example: ilia@example.com
    - name: PASSWORD
      prompt: Gmail App Password (recommended) for IMAP/SMTP
      example: "abcd efgh ijkl mnop"
install:
  # Best-effort hints for installers/tools; actual install method depends on environment.
  - kind: brew
    package: himalaya
  - kind: cargo
    package: himalaya
  - kind: apt
    package: himalaya
---

# Himalaya Email CLI

Himalaya is a CLI email client that lets you manage emails from the terminal using IMAP, SMTP, Notmuch, or Sendmail backends.

## When to Use This Skill

Activate this skill when the user:
- Asks to check, read, or list emails
- Wants to send, reply to, or forward an email
- Asks about their inbox or email folders
- Wants to search for specific emails
- Needs to manage email flags (read/unread, starred, etc.)
- Asks to download or save email attachments

## References

- `references/configuration.md` (config file setup + IMAP/SMTP authentication)
- `references/message-composition.md` (MML syntax for composing emails)

## Prerequisites

1. Himalaya CLI installed (`himalaya --version` to verify)
2. Mordecai will manage a per-user Himalaya config file from the template `himalaya.toml_example`.
3. You MUST have values for the template placeholders:
	- `[GMAIL]` (your Gmail email address)
	- `[PASSWORD]` (a Gmail App Password is recommended)

When the placeholders are provided and persisted, Mordecai will:
- Render a per-user `himalaya.toml` next to the template (in the user's skills folder)
- Export `HIMALAYA_CONFIG` pointing to that file on every skill invocation
- Write a per-user `.env` convenience file under `skills/<user>/.env` (git-ignored)

## Installation Check

**IMPORTANT**: Always verify himalaya is installed and configured first:

```bash
command -v himalaya && himalaya --version
```

**IMPORTANT**: To verify configuration exists, use `himalaya account list` instead of checking the file directly. This avoids path expansion issues with `~` in containerized environments:

```bash
himalaya account list
```

If this command succeeds and shows accounts, the configuration is valid. Do NOT use file read tools to check `~/.config/himalaya/config.toml` as the `~` may not expand correctly.

### If Not Installed

**macOS (Homebrew)**:
```bash
brew install himalaya
```

**Alternative (Cargo)**:
```bash
cargo install himalaya
```

If installation fails, inform the user and provide the GitHub link: https://github.com/pimalaya/himalaya

## Configuration Setup

Mordecai uses a template-based approach (recommended for multi-user isolation).

### Mordecai-managed configuration (recommended)

1. Ensure Himalaya is installed.
2. Ask the user for the required placeholders `[GMAIL]` and `[PASSWORD]`.
3. Persist them using `set_skill_config(skill_name="himalaya", ...)`.

After that, Mordecai will render the per-user config file and set `HIMALAYA_CONFIG` automatically.

### Manual (outside Mordecai)

If the user wants to manage their own config outside Mordecai, they can use:

```bash
himalaya account configure
```

## Common Operations

### List Folders

```bash
himalaya folder list
```

### List Emails

List emails in INBOX (default):
```bash
himalaya envelope list
```

List emails in a specific folder:
```bash
himalaya envelope list --folder "Sent"
```

List with pagination:
```bash
himalaya envelope list --page 1 --page-size 20
```

### Search Emails

Himalaya uses a query language with operators and conditions:

**Filter Conditions:**
- `date <yyyy-mm-dd>` - emails on exact date
- `before <yyyy-mm-dd>` - emails before date (exclusive)
- `after <yyyy-mm-dd>` - emails after date (exclusive)
- `from <pattern>` - sender matches pattern
- `to <pattern>` - recipient matches pattern
- `subject <pattern>` - subject contains pattern
- `body <pattern>` - body contains pattern
- `flag <flag>` - has flag (seen, flagged, etc.)

**Operators:**
- `not <condition>` - negate condition
- `<condition> and <condition>` - both must match
- `<condition> or <condition>` - either matches

**Sort Query (append after filters):**
- `order by date desc` - newest first
- `order by date asc` - oldest first
- `order by from asc` - by sender alphabetically
- `order by subject desc` - by subject reverse alphabetically

**Examples:**

```bash
# Emails from today
himalaya envelope list date 2026-01-29

# Emails from a specific sender
himalaya envelope list from john@example.com

# Emails with subject containing "meeting"
himalaya envelope list subject meeting

# Combined: from john with "meeting" in subject
himalaya envelope list from john@example.com and subject meeting

# Emails from last week, sorted newest first
himalaya envelope list after 2026-01-22 and before 2026-01-30 order by date desc

# Unread emails only
himalaya envelope list not flag seen

# Flagged/starred emails
himalaya envelope list flag flagged
```

### Read an Email

Read email by ID (shows plain text):
```bash
himalaya message read 42
```

Export raw MIME:
```bash
himalaya message export 42 --full
```

### Reply to an Email

Interactive reply (opens $EDITOR):
```bash
himalaya message reply 42
```

Reply-all:
```bash
himalaya message reply 42 --all
```

### Forward an Email

```bash
himalaya message forward 42
```

### Write a New Email

Interactive compose (opens $EDITOR):
```bash
himalaya message write
```

Send directly using template:
```bash
cat << 'EOF' | himalaya template send
From: you@example.com
To: recipient@example.com
Subject: Test Message

Hello from Himalaya!
EOF
```

Or with headers flag:
```bash
himalaya message write -H "To:recipient@example.com" -H "Subject:Test" "Message body here"
```

### Move/Copy Emails

Move to folder:
```bash
himalaya message move 42 "Archive"
```

Copy to folder:
```bash
himalaya message copy 42 "Important"
```

### Delete an Email

```bash
himalaya message delete 42
```

### Manage Flags

Add flag:
```bash
himalaya flag add 42 --flag seen
```

Remove flag:
```bash
himalaya flag remove 42 --flag seen
```

## Multiple Accounts

List accounts:
```bash
himalaya account list
```

Use a specific account:
```bash
himalaya --account work envelope list
```

## Attachments

Save attachments from a message:
```bash
himalaya attachment download 42
```

Save to specific directory:
```bash
himalaya attachment download 42 --dir ~/Downloads
```

## Output Formats

Most commands support `--output` for structured output:
```bash
himalaya envelope list --output json
himalaya envelope list --output plain
```

## Debugging

Enable debug logging:
```bash
RUST_LOG=debug himalaya envelope list
```

Full trace with backtrace:
```bash
RUST_LOG=trace RUST_BACKTRACE=1 himalaya envelope list
```

## Common Use Cases

**IMPORTANT**: CLI flags like `--output json` must come BEFORE the query!

### "Get today's emails"
```bash
# Get current date and filter
TODAY=$(date +%Y-%m-%d)
himalaya envelope list --output json date $TODAY
```

### "Get emails from the last 7 days"
```bash
# GNU date (Linux/Docker containers)
WEEK_AGO=$(date -d '7 days ago' +%Y-%m-%d)
himalaya envelope list --output json after $WEEK_AGO order by date desc

# macOS date alternative
WEEK_AGO=$(date -v-7d +%Y-%m-%d)
himalaya envelope list --output json after $WEEK_AGO order by date desc
```

### "Find unread emails"
```bash
himalaya envelope list --output json not flag seen
```

### "Search for emails about a topic"
```bash
himalaya envelope list --output json subject "project update" or body "project update"
```

### "Get recent emails sorted by date"
```bash
himalaya envelope list --output json --page-size 20 order by date desc
```

### "Get emails from a date range"
```bash
# Emails between Jan 20-29, 2026
himalaya envelope list --output json after 2026-01-19 and before 2026-01-30 order by date desc
```

## Tips

- Use `himalaya --help` or `himalaya <command> --help` for detailed usage.
- Message IDs are relative to the current folder; re-list after folder changes.
- For composing rich emails with attachments, use MML syntax (see `references/message-composition.md`).
- Store passwords securely using `pass`, system keyring, or a command that outputs the password.

## Error Handling

### Common Issues and Solutions:

**1. Himalaya not installed**
- Attempt installation via Homebrew: `brew install himalaya`
- If that fails, suggest Cargo: `cargo install himalaya`
- Verify with `himalaya --version`

**2. No configuration found**
- Run `himalaya account list` to verify configuration (do NOT use file read tools to check `~/.config/himalaya/config.toml` as `~` may not expand correctly in containers)
- If no accounts found, guide user through `himalaya account configure` wizard
- Offer to help create config manually if wizard fails

**3. Authentication errors**
- Verify IMAP/SMTP credentials are correct
- Check if password command works: run the `backend.auth.cmd` manually
- Some providers require app-specific passwords (Gmail, etc.)

**4. Connection failures**
- Verify host/port settings match provider's documentation
- Check encryption type (TLS vs STARTTLS)
- Test network connectivity to mail server

**5. Permission denied on password command**
- Ensure password manager (pass, keyring) is unlocked
- Verify the command path is correct

### Best Practices:

- ✅ Always verify installation before running commands
- ✅ Check configuration exists before attempting email operations
- ✅ Use `--output json` when parsing results programmatically
- ✅ Handle empty results gracefully (no emails in folder)
- ✅ Confirm before destructive operations (delete, move)
