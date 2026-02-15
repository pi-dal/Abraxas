When asked to install or enable a new skill, follow this process:

1. Check whether a matching skill already exists under `src/skills`.
2. If missing, create a new `.md` skill file with clear trigger and steps.
3. Validate that the skill does not require unsafe core edits by default.
4. Only when skills are insufficient, implement extra runtime capability as a plugin in `src/plugins`.
5. Report what was added and how to activate it.

Activation notes:
- Skills in `src/skills` are loaded into system prompt at bot startup.
- If skill files change, restart CLI/Telegram process to pick up updates.
