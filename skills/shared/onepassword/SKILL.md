---
name: onepassword
description: "Retrieve credentials from 1Password using the Python SDK with a Service Account Token. Use when the agent needs login credentials, API keys, tokens, or OTPs stored in 1Password."
requires:
  env:
    - name: OP_SERVICE_ACCOUNT_TOKEN
      prompt: "Provide your 1Password Service Account Token (starts with ops_). Create one at https://my.1password.com → Developer → Service Accounts."
      example: "ops_eyJ..."
  config:
    - name: OP_DEFAULT_VAULT
      prompt: "Default 1Password vault name to search in"
      example: "Private"
---

# 1Password Credential Manager

Securely retrieve credentials from 1Password using the Python SDK with a Service Account Token. No desktop app or biometric approval is required — the token provides headless access to secrets.

## When to Use This Skill

Activate this skill when:
- Another skill or tool needs login credentials (e.g., browser automation needs a password)
- The user asks to retrieve a password, API key, or token from 1Password
- You need a one-time password (OTP/2FA code) for a login flow
- Any operation requires credentials stored in the user's 1Password vault

## Prerequisites

1. A 1Password Service Account Token (`OP_SERVICE_ACCOUNT_TOKEN`) must be configured.
   - The user can create one at: https://my.1password.com → Developer → Service Accounts
   - The token starts with `ops_`
   - Store it via `set_skill_env_vars(skill_name="onepassword", env_json='{"OP_SERVICE_ACCOUNT_TOKEN": "<token>"}')`
2. The Service Account must have access to the vault(s) containing the needed credentials.
3. Optionally set `OP_DEFAULT_VAULT` to avoid specifying the vault on every call.

## How It Works

1. Call `get_credential(service_name="Item Name")` with the 1Password item name.
2. The SDK resolves `op://vault/item/field` using the Service Account Token.
3. The credential value is returned as JSON.

## Common Operations

### Retrieve Username and Password

```python
get_credential(service_name="Outlook Work", fields="username,password")
# Returns: {"username": "user@company.com", "password": "..."}
```

### Retrieve from a Specific Vault

```python
get_credential(service_name="GitHub Token", vault="Development", fields="password")
# Returns: {"password": "ghp_..."}
```

### Retrieve OTP (One-Time Password)

```python
get_credential(service_name="Outlook Work", fields="otp")
# Returns: {"otp": "123456"}
```

### Retrieve Multiple Fields

```python
get_credential(service_name="AWS Production", fields="username,password,otp")
# Returns: {"username": "...", "password": "...", "otp": "..."}
```

## Integration with Browser Tool

When using the `browser` tool for a site that requires login:

1. First retrieve credentials: `get_credential(service_name="Service Name", fields="username,password")`
2. Init a browser session and navigate to the target URL.
3. Use browser actions (`getText`, `type`, `click`) to fill in the credentials step by step.

Example flow:
```
# Step 1: Get credentials
creds = get_credential(service_name="Outlook Work", fields="username,password")

# Step 2: Init browser session
browser(action={"type": "initSession", "session_name": "outlook"})

# Step 3: Navigate
browser(action={"type": "navigate", "url": "https://outlook.office.com", "session_name": "outlook"})

# Step 4: Observe and fill credentials step by step
browser(action={"type": "getText", "session_name": "outlook"})
browser(action={"type": "type", "selector": "input[type='email']", "text": "<username>", "session_name": "outlook"})
browser(action={"type": "click", "selector": "#idSIButton9", "session_name": "outlook"})
# ... continue with password, OTP, etc.
```

## Security Notes

- Credentials are NEVER logged or stored outside of 1Password.
- The Service Account Token should be treated as a sensitive secret.
- The agent should never echo credential values back to the user in plain text.
- Use credentials only for the specific operation requested, then discard them.

## Error Handling

### Common Issues and Solutions

**1. "OP_SERVICE_ACCOUNT_TOKEN is not set"**
- The token has not been configured yet.
- Ask the user for their Service Account Token and store it with `set_skill_env_vars`.

**2. "onepassword-sdk package is not installed"**
- Install with: `pip install onepassword-sdk`

**3. "could not find item" or field resolution errors**
- Check that the item name matches exactly (case-sensitive).
- Verify the vault name is correct.
- Ensure the Service Account has access to the vault.

**4. "authentication failed"**
- The Service Account Token may be expired or revoked.
- Ask the user to generate a new token at https://my.1password.com → Developer → Service Accounts.
