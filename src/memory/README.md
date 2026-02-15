# Abraxas Memory

Memory is a first-class runtime layer.

- `MEMORY.md`: distilled durable memory, loaded at bot startup.
- `braindump.md`: append-only raw idea capture.
- `daily/YYYY-MM-DD.md`: structured daily sync output.
- `mission-log.md`: periodic promotion from braindump to backlog.

Write policy:
- Manual: `/remember <note>`
- Manual ops: `/memory status|sync|promote|compound|query`
- Automatic: pre-compaction flush
- Automatic idea capture: keyword-triggered braindump capture in chat
- Scheduled: daily sync (default `02:00`, `Asia/Shanghai`)

QMD integration:
- Recall: `qmd query "<question>"`
- Index refresh after writes: `qmd update && qmd embed`

Mission-Memory bridge:
- `promote_braindump_to_mission`: promote raw dumps into mission log
- `sync_mission_to_memory`: sync mission backlog into `MEMORY.md`
- `compound_weekly_memory`: maintain one upserted weekly compound section
