# Repository Guidelines

## Project Structure & Module Organization
`src/` is the runtime root.
- `src/core/`: stable runtime core (bot loop, tools runtime, registry, memory, settings, NOUS).
- `src/channel/`: adapters/channels (`cli`, Telegram runner, handlers, schedulers).
- `src/plugins/`: external tool plugins (hot-reloaded, failure-safe by design).
- `src/skills/`: markdown operational skills and playbooks.
- `src/memory/`: durable memory artifacts (`MEMORY.md`, `braindump.md`, `mission-log.md`).
- `ops/systemd/`: deployment unit files (e.g., Telegram service).
- Tests: `test_bot.py`, `test_telegram_bot.py`.

Keep `src/core` and `src/channel` changes intentional and minimal; extend behavior through `src/plugins` or `src/skills` first.

## Build, Test, and Development Commands
- `pdm install`: install dependencies into the project environment.
- `pdm run abraxas-cli`: start local CLI agent.
- `pdm run abraxas-telegram`: start Telegram bot runner.
- `pdm run python -m unittest -v`: run all tests.
- `pdm run python -m unittest -v test_bot.BotTests.test_xxx`: run a focused test.

For production on Debian, use the systemd template at `ops/systemd/abraxas-telegram.service`.

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indentation, explicit type hints where practical.
- Filenames: `snake_case.py` for Python modules; skill docs typically `kebab-case.md`.
- Keep functions small and composable; return readable error strings for plugin failures.
- Prefer ASCII unless an existing file already requires Unicode.

## Testing Guidelines
- Framework: built-in `unittest`.
- Add/update tests for behavior changes, especially around settings, plugin loading, and command routing.
- Name tests descriptively: `test_<feature>_<expected_behavior>`.
- Ensure new plugin/skill behavior has at least one positive-path and one failure-path assertion.

## Commit & Pull Request Guidelines
- Follow Conventional Commit style seen in history: `feat: ...`, `refactor: ...`.
- One logical change per commit; keep diff scope tight.
- PRs should include:
1. What changed and why.
2. Key files touched (example: `src/plugins/...`).
3. Test evidence (example: `pdm run python -m unittest -v`).
4. Screenshots or log snippets for Telegram/CLI UX changes when relevant.

## Security & Configuration Tips
- Never commit secrets. Use `.env` (copy from `.env.example`).
- Required core key: `API_KEY`.
- Optional plugin key: `GEMINI_API_KEY` (for image-generation plugin).
- Optional OpenAI-compatible overrides: `OPENAI_BASE_URL`, `OPENAI_MODEL`.
