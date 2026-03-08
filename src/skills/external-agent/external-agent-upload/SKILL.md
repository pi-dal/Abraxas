---
name: external-agent-upload
description: Upload files and import archives from an external agent through internal APIs. Use when writing binary/text files into an existing project via /api/internal/files/upload or creating projects from uploaded archives via /api/internal/projects/import/upload.
metadata: {"abraxas":{"emoji":"📤","requires":{"env":["INTERNAL_API_SECRET"],"bins":["python"]},"preferences":{"syncYjs":true,"strictYjs":true,"overwrite":true}}}
---

# External Agent Upload

Use this skill for all upload/import operations from external agents.

## Set environment

```bash
export INTERNAL_API_BASE_URL="http://localhost:3000"
export INTERNAL_API_SECRET="<your-internal-secret>"
```

## Upload into existing project path

```bash
python scripts/upload_cli.py upload-file \
  --project-id "proj-1" \
  --file-path "assets/reference.pdf" \
  --source "/absolute/path/reference.pdf"
```

If you need ws live-sync for text-like uploads:

```bash
python scripts/upload_cli.py upload-file \
  --project-id "proj-1" \
  --file-path "main.tex" \
  --source "/absolute/path/main.tex" \
  --sync-yjs
```

## Import archive/tex as a new project

```bash
python scripts/upload_cli.py import-project \
  --owner-id "user-1" \
  --source "/absolute/path/paper.zip" \
  --name "Paper Import"
```

## Notes

- `upload-file` calls: `POST /api/internal/files/upload`
- `import-project` calls: `POST /api/internal/projects/import/upload`
- Supported import types follow server rules (`.zip`, `.tar.gz`, `.tgz`, `.tar`, `.tex`, `.bib`, `.cls`, `.sty`).
- CLI defaults to `syncYjs=true` for real-time sync; pass `--no-sync-yjs` for offline/debug workflows.
- Yjs sync is strict by default; add `--best-effort-yjs` if you prefer non-failing fallback behavior.
