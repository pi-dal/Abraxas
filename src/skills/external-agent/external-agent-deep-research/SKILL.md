---
name: external-agent-deep-research
description: Run Deep Research from an external agent using the ai-server SSE endpoint. Use when generating research reports, collecting iterative web+arXiv findings, or streaming structured deep research output from /api/deep-research/stream.
metadata: {"abraxas":{"emoji":"🔎","requires":{"bins":["python"]}}}
---

# External Agent Deep Research

Use this skill when the external agent needs long-form research output.

## Set environment

```bash
export AI_SERVER_BASE_URL="http://localhost:8000"
```

## Run deep research stream

```bash
python scripts/run_deep_research_stream.py \
  --query "How does retrieval augmentation affect theorem proving quality?" \
  --arxiv-papers 8 \
  --web-pages 8 \
  --max-iterations 3 \
  --structured
```

## Recommended usage pattern

1. Use this skill for exploration/synthesis tasks.
2. Save full stream JSONL for traceability (`--output-jsonl`).
3. Use deterministic project/file skills to apply concrete edits afterward.

## Notes

- Endpoint: `POST /api/deep-research/stream`
- Response: SSE stream with events (`progress`, `section_*`, `done`)
- `projectId` is optional but recommended when linking to a workspace context.
