# Request and Response Shapes

Request body to `/api/internal/agent/run`:

```json
{
  "projectId": "proj-1",
  "userId": "user-1",
  "message": "Update intro",
  "mode": "agent",
  "runtime": "openclaw",
  "referencedFiles": ["main.tex"],
  "sessionId": "optional-session"
}
```

Success response fields (additive):

- `success`
- `response`
- `sessionId`
- `runtimeUsed`
- `requestedRuntime`
- `fallbackUsed`
- `fallbackReason`
- `toolCalls`
- `uiPatches` (validated patch list; may be empty)

Notes:

- `uiPatches` is additive and safe to ignore if your client has no UI layer.
- To route emitted patches to an active browser session, provide `userId` in the request.
