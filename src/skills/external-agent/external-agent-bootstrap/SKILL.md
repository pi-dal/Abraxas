---
name: external-agent-bootstrap
description: Bootstrap and validate external-agent access to PaperAnim/PaperPeeler internal APIs. Use when setting INTERNAL_API_SECRET, checking /api/internal/capabilities, verifying /api/internal/chat-config, or diagnosing auth/connectivity failures before real agent calls.
metadata: {"abraxas":{"emoji":"🧭","requires":{"env":["INTERNAL_API_SECRET"],"bins":["python"]},"preferences":{"strict":true,"realtimeCollab":true}}}
---

# External Agent Bootstrap

## Prepare environment

Set these environment variables:

```bash
export INTERNAL_API_BASE_URL="http://localhost:3000"
export INTERNAL_API_SECRET="<your-internal-secret>"
```

## Run connectivity check

Run:

```bash
python scripts/bootstrap_check.py
```

Strict mode (non-zero exit on failures):

```bash
python scripts/bootstrap_check.py --strict
```

Expected behavior:

- HTTP 200 for `/api/internal/capabilities`
- HTTP 200 for `/api/internal/chat-config`
- Parsed JSON output for both responses

## Common fixes

- Unauthorized: verify `INTERNAL_API_SECRET` matches server `.env`
- Connection refused: verify Next.js server is running on the configured base URL
- Empty AI capabilities: verify `AI_SERVER_URL` and ai-server are running
