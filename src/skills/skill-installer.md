When asked to install or enable a new skill, follow this process:

1. Reuse prior art first via Skills CLI:
   - `npx skills find <query>` to discover matching public skills.
   - If discovery capability itself is missing, install it first:
     - `npx skills add https://github.com/vercel-labs/skills --skill find-skills`
   - Prefer install command style from find-skills ecosystem: `npx skills add <owner/repo@skill>`.
2. If a good match exists, install it first; use global non-interactive flags when needed:
   - `npx skills add <owner/repo@skill> -g -y`
3. After installation, check freshness:
   - `npx skills check`
   - `npx skills update` (when updates are available and user approves)
4. If no good match exists, check whether a matching local skill already exists under `src/skills`.
5. If still missing, create a new `.md` skill file with clear trigger and steps.
6. Validate that the skill does not require unsafe core edits by default.
7. Only when skills are insufficient for executable capability, implement extra runtime capability as a plugin in `src/plugins`.
8. After adding/enabling a skill in this repo, update `src/skills/README.md` built-in list so inventory stays discoverable.
9. Report what was added, what was reused, and how to activate it.

Activation notes:
- Skills in `src/skills` are prompt-level instructions; load/reload them by starting a new runtime session.
- Current entrypoints:
  - `pdm run abraxas-cli`
  - `pdm run abraxas-telegram`
- If skill files change and active sessions are already running, restart the process to ensure a clean reload.
- Plugins are hot-reloaded by the runtime registry and usually do not require process restart.
