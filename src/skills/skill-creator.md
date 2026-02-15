Use when the user asks to create or update skills, especially from accumulated memory.

## Workflow

1. Clarify scope:
- skill goal
- trigger conditions
- output contract

2. Apply skills-first:
- prefer `src/skills`
- only use plugins if skill-only solution is insufficient
- avoid direct edits to `src/core` and `src/channel` unless user explicitly asks

3. Keep skill content concise and operational:
- concrete steps
- explicit boundaries
- failure-safe behavior

## Memory Assisted Manual Skill Creation

When user asks to create a skill from memory (for example: "根据记忆创建 skill"), use this mandatory pipeline:

1. Read memory sources in this order:
- `src/memory/MEMORY.md` for distilled durable context
- `src/memory/braindump.md` for raw ideas and tags
- `src/memory/mission-log.md` for pending and promoted items
- latest `src/memory/daily/YYYY-MM-DD.md` files for recent decisions

2. Distill into a skill spec:
- skill name (kebab-case)
- "Use when ..." trigger sentence
- non-goals (what not to do)
- minimal step-by-step workflow
- 1-3 concrete examples

3. Write or update the skill file:
- target path: `src/skills/<skill-name>.md`
- keep text short, actionable, and reusable
- avoid project-specific noise unless explicitly requested

4. Record follow-up:
- append a short line in `src/memory/mission-log.md` noting the skill created/updated and why

## Authoring Rules

- One skill solves one clear problem.
- Avoid vague policy language; prefer executable instructions.
- Prefer stable patterns over temporary hacks.
- Keep compatibility with existing runtime constraints.
