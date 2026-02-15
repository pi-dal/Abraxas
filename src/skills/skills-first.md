Use a skills-first workflow:

1. Try solving tasks with existing skills in `src/skills` first.
2. If no suitable skill exists or skills are insufficient, add or extend a plugin in `src/plugins`.
3. Do not edit `src/core` or `src/channel` unless the user explicitly asks.

When plugin fallback is needed:
1. Register through `register(registry)`.
2. Return readable error text instead of uncaught exceptions.
3. Keep behavior focused, safe, and reversible.
