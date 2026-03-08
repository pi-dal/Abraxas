# Runtime Sync From Abraxasdaily Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Sync the non-skill, non-memory runtime changes from `~/developer/abraxasdaily/src` into this repository without importing `src/skills` or `src/memory` content.

**Architecture:** Port the new runtime layers in slices: first tests, then core execution/tape primitives, then command and channel integration, then plugin additions. Keep `core/memory.py`, `core/skills.py`, and `src/memory/*` untouched so the sync stays inside the requested scope while still wiring the new runtime hooks around them.

**Tech Stack:** Python 3, `unittest`, existing Abraxas runtime modules under `src/core`, `src/channel`, and `src/plugins`.

### Task 1: Add regression tests for synced runtime behavior

**Files:**
- Modify: `test_bot.py`
- Modify: `test_telegram_bot.py`
- Create: `test_execution_protocol.py`
- Create: `test_telegram_formatter.py`

**Step 1: Write the failing tests**

Add tests for:
- execution protocol text appearing in `build_system_prompt`
- new `/checkpoint`, `/handoff`, `/tape`, `/rci`, `/yolo`, `/safe`, `/allow`, `/deny`, `/stop` command routing
- telegram formatter helpers
- telegram file delivery metadata behavior if needed through channel tests

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m unittest -v test_bot test_telegram_bot test_execution_protocol`

Expected: failures for missing modules, missing command handlers, or missing prompt content.

**Step 3: Keep the tests focused on the sync surface**

Do not add assertions for `src/skills/*` or `src/memory/*` content. The tests should only verify runtime behavior exposed through core/channel/plugins.

### Task 2: Sync core runtime modules and entry points

**Files:**
- Create: `src/core/bot_hitl.py`
- Create: `src/core/commands_hitl.py`
- Create: `src/core/rci_state.py`
- Create: `src/core/session_context.py`
- Create: `src/core/tape.py`
- Modify: `src/core/bot.py`
- Modify: `src/core/commands.py`
- Modify: `src/core/settings.py`
- Modify: `src/core/tools.py`

**Step 1: Port new helper modules**

Bring over the standalone runtime helpers exactly where possible:
- HITL controller
- RCI session state
- tape engine
- tape/session reconstruction helpers

**Step 2: Update `bot.py` minimally around current memory runtime**

Integrate:
- execution protocol prompt section
- optional OpenAI import guard
- session-aware constructor
- tape-backed working set rebuilds
- checkpoint proposal flow
- RCI state hooks

Keep compatibility with the current `core.memory` APIs by preserving or using fallback logic already present in the source branch.

**Step 3: Update commands and settings**

Add:
- new command handlers and help text
- checkpoint/context settings
- telegram streaming/temp settings
- builtin `read_skill` tool if still compatible with the local `core.skills` module

### Task 3: Sync CLI and Telegram channel integration

**Files:**
- Modify: `src/channel/cli.py`
- Create: `src/channel/telegram_formatter.py`
- Modify: `src/channel/telegram_client.py`
- Modify: `src/channel/telegram_handlers.py`
- Modify: `src/channel/telegram_runner.py`
- Modify: `src/channel/telegram.py`

**Step 1: Port CLI execution-control UX**

Add streaming reply support and intercepted-tool approval flows in CLI command handling.

**Step 2: Port Telegram rendering and control-plane support**

Add:
- formatter-based rendering
- callback query handling
- typing/draft/edit support
- non-blocking per-chat worker model
- tape/checkpoint/rci command routing

**Step 3: Verify current channel exports remain valid**

Make sure `channel.telegram` still exports the symbols imported by existing tests and runtime entrypoints.

### Task 4: Sync plugin additions and compatible plugin updates

**Files:**
- Create: `src/plugins/brave_search.py`
- Create: `src/plugins/minimax_mcp.py`
- Create: `src/plugins/telegram_file.py`
- Create: `src/plugins/write.py`
- Modify: `src/plugins/nano_banana_image.py`

**Step 1: Port new plugins**

Copy the plugin implementations while preserving the local plugin contract.

**Step 2: Update `nano_banana_image.py`**

Add output prefix sanitization and prompt-derived prefixes without disturbing existing generation behavior.

**Step 3: Run focused plugin-adjacent tests**

Run tests covering plugin registry exposure and Telegram delivery behavior.

### Task 5: Verify end to end

**Files:**
- Modify: `test_bot.py`
- Modify: `test_telegram_bot.py`
- Create: `test_execution_protocol.py`
- Create: `test_telegram_formatter.py`

**Step 1: Run focused tests**

Run: `PYTHONPATH=src python -m unittest -v test_bot test_telegram_bot test_execution_protocol test_telegram_formatter`

Expected: all pass.

**Step 2: Run the full suite**

Run: `PYTHONPATH=src python -m unittest -v`

Expected: all tests pass.

**Step 3: Review for excluded scope**

Confirm no edits were made under `src/skills/` or `src/memory/`.
