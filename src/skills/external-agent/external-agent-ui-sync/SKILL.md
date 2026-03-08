---
name: external-agent-ui-sync
description: Coordinate UI control between external agents and active browser sessions. Use when an external agent should trigger workspace ui_patch behavior via /api/internal/agent/run and the browser should apply those patches in real time.
metadata: {"abraxas":{"emoji":"🧩","requires":{"env":["INTERNAL_API_SECRET"]},"preferences":{"realtimeUi":true,"requireUserId":true}}}
---

# External Agent UI Sync

Use this skill when external automation must drive workspace UI state.

## Preconditions

1. External agent calls `POST /api/internal/agent/run`.
2. Request includes `userId` (required for UI routing).
3. Browser user is signed in as the same user and connected to `GET /api/workspace-ui/events`.

## Minimal request pattern

```json
{
  "projectId": "proj-1",
  "userId": "user-1",
  "message": "Open this project and switch to preview",
  "mode": "agent",
  "runtime": "openclaw"
}
```

## What to consume from response

- `success`
- `sessionId`
- `runtimeUsed`
- `fallbackUsed`
- `uiPatches` (array, additive, may be empty)

## Orchestrator rules

- Always keep `userId` stable per human session.
- Reuse `sessionId` to preserve context and reduce prompt drift.
- Treat `uiPatches` as advisory output, not a hard guarantee that every run emits one.
- If `uiPatches` is empty, do not retry solely for UI effects unless product logic requires it.

## Failure handling

- If response is `success=false`, log `error` and skip any UI action.
- If `fallbackUsed=true`, continue normal handling; runtime fallback is expected behavior.
- If browser is offline, keep `uiPatches` in orchestrator logs for replay/manual audit.
