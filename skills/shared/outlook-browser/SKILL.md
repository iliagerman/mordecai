---
name: outlook-browser
description: "Manage Outlook email via AgentCore Browser automation. Use for reading, sending, and organizing emails in Outlook Web when CLI-based email is not available. Requires the browser and get_credential tools."
allowed-tools: Bash,Read
requires:
  config:
    - name: OUTLOOK_ITEM_NAME
      prompt: "Name of the 1Password item containing Outlook credentials"
      example: "Outlook Work"
    - name: OUTLOOK_VAULT
      prompt: "1Password vault containing the Outlook credentials"
      example: "Private"
---

# Outlook Browser Automation

Manage Outlook email through step-by-step browser automation using the AgentCore Browser tool. This skill navigates Outlook Web App (OWA) to read, send, and organize emails.

## Microsoft Login Flow

The Microsoft login flow has **4 distinct pages** that appear in order. You must handle each one.

### Page 1: Email Entry (`login.microsoftonline.com`)

The starting URL is `https://login.microsoftonline.com`. The page shows "Sign in" with an email input field.

- **Selector**: `input[type='email']` or `input[name='loginfmt']`
- **Submit button**: `#idSIButton9` ("Next")

### Page 2: Password Entry

After email submission, the page shows "Enter password" with a password input field.

- **Selector**: `input[type='password']` or `input[name='passwd']`
- **Submit button**: `#idSIButton9` ("Sign in")

### Page 3: MFA Verification

After password, a "Verify your identity" page appears with multiple verification options (e.g., "Text +XX XXXXXXXX", Microsoft Authenticator, etc.).

**You MUST select the "Text" (SMS) option.** This sends a code to the user's phone.

- **Click the SMS/Text option** link on the page (look for text containing "Text +" in `getText` output)
- After selecting text, Microsoft sends an SMS code and shows an input field for it
- **Code input selector**: `input[name='otc']`
- **Verify button**: `#idSubmit_SAOTCC_Continue`

**CRITICAL: The SMS code must come from the user.** After selecting the text message option:
1. **Stop and respond** to the user: "I've requested an SMS verification code to your phone. Please send me the code when you receive it."
2. **Wait for the user's next message** containing the code.
3. When the user sends the code, **resume the browser session** (it stays alive between messages), fill the code field, and click verify.

### Page 4: "Stay signed in?"

After MFA, a "Stay signed in?" prompt appears.

- **Check the checkbox**: `input[name='DontShowAgain']` (the "Don't show this again" checkbox)
- **Click Yes**: `#idSIButton9` ("Yes")

Always check the box and click Yes to reduce future login prompts.

### After Login

Once all 4 pages are handled, the browser is redirected to Outlook. Navigate to `https://outlook.office.com/mail/inbox` if not already there.

---

## Credential Flow

### Step 1: ALWAYS fetch username and password from 1Password FIRST

```python
creds = get_credential(service_name="<OUTLOOK_ITEM_NAME>", vault="<OUTLOOK_VAULT>", fields="username,password")
```

Where `<OUTLOOK_ITEM_NAME>` and `<OUTLOOK_VAULT>` come from the skill config.

**Do NOT fetch `otp` — the OTP for Outlook comes via SMS from the user, not from 1Password.**

### Step 2: Use the browser tool step-by-step

#### Full Login Example

```python
# 1. Fetch credentials from 1Password (username + password only)
creds = get_credential(service_name="<OUTLOOK_ITEM_NAME>", vault="<OUTLOOK_VAULT>", fields="username,password")

# 2. Init browser session
browser(action={"type": "initSession", "session_name": "outlook"})

# 3. Navigate to Microsoft login
browser(action={"type": "navigate", "url": "https://login.microsoftonline.com", "session_name": "outlook"})

# 4. Observe page state
browser(action={"type": "getText", "session_name": "outlook"})

# --- Page 1: Email ---
browser(action={"type": "type", "selector": "input[name='loginfmt']", "text": "<username>", "session_name": "outlook"})
browser(action={"type": "click", "selector": "#idSIButton9", "session_name": "outlook"})

# --- Page 2: Password ---
browser(action={"type": "getText", "session_name": "outlook"})
browser(action={"type": "type", "selector": "input[name='passwd']", "text": "<password>", "session_name": "outlook"})
browser(action={"type": "click", "selector": "#idSIButton9", "session_name": "outlook"})

# --- Page 3: MFA ---
browser(action={"type": "getText", "session_name": "outlook"})
# Select the "Text +" SMS option (click the link/button that mentions texting)
# ... click the SMS option ...
# STOP HERE — ask the user for the SMS code they receive
# (respond with: "I've requested an SMS code. Please send me the code.")

# --- NEXT USER MESSAGE provides the code ---
browser(action={"type": "type", "selector": "input[name='otc']", "text": "<code from user>", "session_name": "outlook"})
browser(action={"type": "click", "selector": "#idSubmit_SAOTCC_Continue", "session_name": "outlook"})

# --- Page 4: Stay signed in ---
browser(action={"type": "getText", "session_name": "outlook"})
browser(action={"type": "click", "selector": "input[name='DontShowAgain']", "session_name": "outlook"})
browser(action={"type": "click", "selector": "#idSIButton9", "session_name": "outlook"})

# --- Navigate to Outlook inbox ---
browser(action={"type": "navigate", "url": "https://outlook.office.com/mail/inbox", "session_name": "outlook"})
browser(action={"type": "getText", "session_name": "outlook"})
```

### NEVER DO THIS:
- Do NOT interact with the browser without first calling `get_credential`
- Do NOT pass hardcoded/dummy credentials
- Do NOT skip the `get_credential` step even if cookies might still be valid
- Do NOT invent or guess credential values — they MUST come from 1Password
- Do NOT try to fetch `otp` from 1Password for Outlook — the MFA code comes via SMS from the user
- Do NOT try to auto-fill the SMS code — you MUST ask the user and wait for their response
- Do NOT click "No" on "Stay signed in?" — always check the box and click "Yes"

### ALWAYS DO THIS:
1. Call `get_credential(fields="username,password")` FIRST
2. Use `getText` to observe the page state before each action
3. Use `screenshot` if `getText` isn't clear enough
4. Fill form fields one at a time
5. On the MFA page, select the SMS/text method, then ask the user for the code
6. On "Stay signed in?", check "Don't show this again" and click "Yes"

## When to Use This Skill

Activate this skill when the user:
- Asks to check, read, or send emails via Outlook
- Wants to interact with their Outlook inbox
- Needs to manage email in a browser-based flow (not CLI)
- The himalaya CLI skill is not configured or not available for Outlook

## Prerequisites

1. **Browser tool enabled**: `browser_enabled: true` in config.
2. **1Password configured**: The `onepassword` skill must be set up with access to Outlook credentials.
3. **Outlook credentials in 1Password**: An item with username/password for Outlook.

### First-Time Setup

If not yet configured:

1. Ensure `browser` and `get_credential` tools are available.
2. Set the 1Password item name for Outlook:
   ```
   set_skill_config(
     skill_name="outlook-browser",
     config_json='{"OUTLOOK_ITEM_NAME": "Outlook Work", "OUTLOOK_VAULT": "Private"}'
   )
   ```

## Common Operations

**REMINDER: Every operation starts with `get_credential` and login (if needed).**

### Check Inbox (List Recent Emails)

1. Fetch credentials and log in (see login flow above).
2. Navigate to `https://outlook.office.com/mail/inbox`.
3. Use `getText` to read the inbox and list the 5 most recent emails (sender, subject, date, preview).

### Read a Specific Email

1. Log in and navigate to inbox.
2. Use `click` on the email by subject or sender.
3. Use `getText` to read the full email body.

### Send an Email

1. Log in and navigate to inbox.
2. Click "New Message" button.
3. Fill To, Subject, and Body fields using `type`.
4. Click Send.

### Reply to an Email

1. Log in and navigate to inbox.
2. Open the target email.
3. Click Reply.
4. Fill the reply body using `type`.
5. Click Send.

### Search for Emails

1. Log in and navigate to inbox.
2. Click the search bar and type the search query using `type`.
3. Press Enter or click search.
4. Use `getText` to read the results.

## Session Persistence

After successful login (with "Stay signed in" checked), cookies persist so subsequent browser sessions may skip the login flow entirely.

However, sessions expire periodically. If the browser navigates to a login page after cookie restoration, run the full login flow again.

**Even with cookies, always call `get_credential` before every browser interaction** so credentials are ready if re-login is needed.

## Tips

- **Observe before acting**: Always use `getText` or `screenshot` before filling forms or clicking.
- **Login URL**: Always start at `https://login.microsoftonline.com`, NOT `https://outlook.office.com`.
- **Known selectors**: `input[name='loginfmt']` (email), `input[name='passwd']` (password), `#idSIButton9` (Next/Sign In/Yes), `input[name='otc']` (verification code), `input[name='DontShowAgain']` (stay signed in checkbox), `#idSubmit_SAOTCC_Continue` (verify code button).
- **Adaptive approach**: If a known selector doesn't work, use `getText` to read the page and identify the correct element.
- **Multi-turn MFA**: The browser session stays alive between messages. Start login, ask for SMS code, then continue when the user replies.
- **Session timeout**: Browser sessions have a default timeout. For very long operations, break them into smaller steps.

## Error Handling

### Common Issues and Solutions

**1. Login fails or loops**
- Use `screenshot` to see the actual page state.
- Check that the 1Password item name is correct and contains valid credentials.
- The selectors may have changed — use `getText` to adapt.

**2. "Credential access denied"**
- The user declined the approval prompt. Respect their decision.

**3. "get_credential returned an error"**
- Check that `OP_SERVICE_ACCOUNT_TOKEN` is set and valid.
- Check that `OUTLOOK_ITEM_NAME` and `OUTLOOK_VAULT` match the actual 1Password item.
- Do NOT proceed to browser interaction if credential retrieval failed.

**4. MFA code rejected**
- Ask the user to send the code again — they may have mistyped it.
- The code may have expired if the user took too long. Request a new SMS code by navigating back and re-selecting the text method.

**5. Page navigation errors**
- Outlook's UI may change. Use `getText`/`screenshot` to adapt to the current page layout.
- Start from `https://login.microsoftonline.com` and navigate from there.

**6. Session expired**
- Run the full login flow again. Cookies and "Stay signed in" reduce how often this happens.
