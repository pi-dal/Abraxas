from pathlib import Path

DEFAULT_SKILLS_DIR = "src/skills"
SUPPORTED_SKILL_EXTENSIONS = {".md", ".txt"}


def _resolve_skills_dir(skills_dir: str) -> Path:
    path = Path(skills_dir)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def load_skills_prompt(skills_dir: str = DEFAULT_SKILLS_DIR) -> str:
    base_dir = _resolve_skills_dir(skills_dir)
    if not base_dir.exists() or not base_dir.is_dir():
        return ""

    skill_files = sorted(
        path
        for path in base_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SKILL_EXTENSIONS
    )
    if not skill_files:
        return ""

    sections: list[str] = []
    for path in skill_files:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        sections.append(f"[skill:{path.name}]\n{content}")

    if not sections:
        return ""

    skills_text = "\n\n".join(sections)
    return (
        "Additional skills loaded from src/skills. Follow these instructions when relevant.\n"
        f"{skills_text}"
    )
