# Abraxas Skills

Drop additional `.md` or `.txt` instruction files in this folder to extend Abraxas behavior.

- Each file is loaded into the system prompt at startup.
- File name is exposed as `[skill:<filename>]`.
- Keep instructions short, concrete, and safe.
- Avoid asking skills to modify `src/core` or `src/channel` directly unless explicitly requested by the user.
- Built-in examples in this repo:
  - `skills-first.md`
  - `skill-creator.md`
  - `skill-installer.md`
  - `plugin-creator.md`
  - `nano-banana-pro-photo.md`

Use `ABRAXAS_SKILLS_DIR` to point to a different skills folder at runtime.
