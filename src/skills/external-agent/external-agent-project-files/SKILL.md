---
name: external-agent-project-files
description: Operate projects and files through internal APIs for external agents. Use when listing projects, listing files, reading file content, or writing updated file content via /api/internal/projects/* and /api/internal/files/* endpoints.
metadata: {"abraxas":{"emoji":"📁","requires":{"env":["INTERNAL_API_SECRET"],"bins":["python"]},"preferences":{"readSource":"effective","syncYjs":true,"strictYjs":true}}}
---

# External Agent Project and File Operations

Use this skill to perform deterministic project/file operations before or after
agent reasoning steps.

## Required env

```bash
export INTERNAL_API_BASE_URL="http://localhost:3000"
export INTERNAL_API_SECRET="<your-internal-secret>"
```

## Common commands

List projects:

```bash
python scripts/internal_api_cli.py list-projects --owner-id "user-1"
```

List files:

```bash
python scripts/internal_api_cli.py list-files --project-id "proj-1"
```

Read file:

```bash
python scripts/internal_api_cli.py read-file --project-id "proj-1" --file-path "main.tex"
```

Read collaborative/effective content (Yjs + pending edits):

```bash
python scripts/internal_api_cli.py read-file --project-id "proj-1" --file-path "main.tex" --source effective
```

Replace file content:

```bash
python scripts/internal_api_cli.py edit-file --project-id "proj-1" --file-path "main.tex" --content "new content"
```

Replace file content and push to Yjs (only if you really need live editor sync):

```bash
python scripts/internal_api_cli.py edit-file --project-id "proj-1" --file-path "main.tex" --content "new content" --sync-yjs
```

Replace file content from local text file:

```bash
python scripts/internal_api_cli.py edit-file --project-id "proj-1" --file-path "main.tex" --content-file "/tmp/main.tex"
```

## Guidance

- Prefer `read-file` before `edit-file`.
- `read-file` defaults to `--source effective` for real-time collaborative context.
- `edit-file` defaults to `syncYjs=true`; pass `--no-sync-yjs` only for offline/debug workflows.
- Yjs sync is strict by default (request fails if ws sync fails); add `--best-effort-yjs` only when needed.
- Keep edits minimal and scoped.
- Use `--start-line`/`--end-line` when partial reads are enough.
- Use `--no-recursive` if directory listing is too large.
