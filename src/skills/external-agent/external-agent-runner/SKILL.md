---
name: external-agent-runner
description: Invoke the internal AI runtime endpoint for external agents. Use when calling /api/internal/agent/run with message, mode, session context, referenced files, runtime selection (builtin or openclaw), and additive uiPatches for UI coordination.
metadata: {"abraxas":{"emoji":"🤖","requires":{"env":["INTERNAL_API_SECRET"],"bins":["python"]},"preferences":{"mode":"agent","runtime":"openclaw","timeoutSeconds":360}}}
---

# External Agent Runner

Use this skill to run the exposed internal runtime endpoint from an external
agent.

## Required env

```bash
export INTERNAL_API_BASE_URL="http://localhost:3000"
export INTERNAL_API_SECRET="<your-internal-secret>"
```

## Invoke runtime

```bash
python scripts/run_internal_agent.py \
  --project-id "proj-1" \
  --user-id "user-1" \
  --mode "agent" \
  --runtime "openclaw" \
  --timeout 360 \
  --output-json "/tmp/run.json" \
  --message "Read main.tex and summarize the key theorem in Chinese."
```

## Recommended call order for external orchestrators

1. Use `external-agent-bootstrap` once at startup.
2. Use `external-agent-project-files` for deterministic reads/writes.
3. Use this skill for reasoning-heavy actions.
4. Persist returned `sessionId` and reuse it on the next call.

## Runtime behavior

- `runtime=builtin`: in-process default runtime.
- `runtime=openclaw`: external runtime adapter (if enabled).
- Response may include `runtimeUsed`, `fallbackUsed`, and `uiPatches`.
- CLI default runtime is `openclaw` (override with `--runtime builtin` or `INTERNAL_AGENT_RUNTIME`).
- CLI timeout default is 360s (override with `--timeout` or `INTERNAL_AGENT_TIMEOUT_SECONDS`).
- `userId` is required if you want UI patches to be routed to the matching signed-in browser session.

## UI coordination notes

- Keep `userId` stable across calls and reuse `sessionId`.
- Treat `uiPatches` as additive hints; do not assume every call emits one.
- For browser-side application of patches, pair this skill with `external-agent-ui-sync`.
