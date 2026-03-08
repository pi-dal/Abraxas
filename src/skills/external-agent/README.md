# External Agent Skills Pack

This folder contains ready-to-use skills for external agents that need to call
PaperAnim/PaperPeeler internal APIs.

Included skills:

- `external-agent-bootstrap`
  - Validate auth and connectivity for `/api/internal/*` endpoints.
- `external-agent-project-files`
  - List projects/files and read/edit project files (`read-file` defaults to effective/Yjs view, `edit-file` syncs ws by default and fails on sync errors unless best-effort).
- `external-agent-runner`
  - Invoke `/api/internal/agent/run` with runtime selection (`builtin`/`openclaw`), session reuse, and additive `uiPatches`.
- `external-agent-ui-sync`
  - Coordinate external agent UI control flow: keep stable `userId`, consume `uiPatches`, and pair with browser `/api/workspace-ui/events` subscription.
- `external-agent-deep-research`
  - Stream Deep Research output from `POST /api/deep-research/stream`.
- `external-agent-upload`
  - Upload files into projects and import archives through internal upload endpoints (`syncYjs` enabled by default with strict failure semantics).

All skills assume:

- Base URL default: `http://localhost:3000`
- Header: `X-Internal-Secret: <INTERNAL_API_SECRET>`

Compatibility note:

- Frontmatter includes `metadata.abraxas` hints (`emoji`, `requires`) for
  Abraxas-style skill loaders to surface requirement checks.
- Frontmatter now also includes `metadata.abraxas.preferences` so external
  orchestrators can apply ws-first defaults (effective reads, strict Yjs sync,
  openclaw runtime, agent mode).
