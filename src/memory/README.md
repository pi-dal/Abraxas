# Abraxas Memory

Memory is a first-class runtime layer.

- `MEMORY.md`: distilled durable memory, loaded at bot startup.
- `braindump.md`: append-only raw idea capture.
- `daily/YYYY-MM-DD.md`: structured daily sync output.
- `mission-log.md`: periodic promotion from braindump to backlog.

Write policy:
- Manual: `/remember <note>`
- Automatic: pre-compaction flush
- Scheduled: daily sync (default `02:00`, `Asia/Shanghai`)

QMD integration:
- Recall: `qmd query "<question>"`
- Index refresh after writes: `qmd update && qmd embed`
