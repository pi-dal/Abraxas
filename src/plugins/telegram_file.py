"""
Telegram File Plugin - Send files from server to Telegram chat.

Provides tools to list files and send documents via Telegram Bot API.
Similar to /photos command but for generic files.
"""
import json
from pathlib import Path
from typing import Any

from core.tools import ToolPlugin


def _handle_list_files(payload: dict) -> str:
    """Handle list_files tool request - list files in a directory.
    
    Expected payload:
    - directory: str (optional) - Directory to list (default: current directory)
    - pattern: str (optional) - Glob pattern to filter files (default: *)
    - limit: int (optional) - Max files to return (default: 50)
    """
    try:
        directory = payload.get("directory", ".")
        pattern = payload.get("pattern", "*")
        limit = int(payload.get("limit", 50))
        
        base_path = Path(directory)
        if not base_path.exists():
            return f"list_files error: directory not found: {directory}"
        if not base_path.is_dir():
            return f"list_files error: not a directory: {directory}"
        
        # List files matching pattern
        files = sorted(base_path.glob(pattern), key=lambda p: (not p.is_file(), p.name))
        
        # Filter only files, limit results
        file_list = [f for f in files if f.is_file()][:limit]
        
        if not file_list:
            return f"no files found in {directory} matching '{pattern}'"
        
        lines = [f"files in {directory} matching '{pattern}':"]
        for f in file_list:
            size = f.stat().st_size
            size_str = f"{size} B" if size < 1024 else f"{size/1024:.1f} KB"
            lines.append(f"  {f.name} ({size_str})")
        
        if len(file_list) >= limit:
            lines.append(f"  ... (showing first {limit} files)")
        
        return "\n".join(lines)
    
    except Exception as exc:
        return f"list_files error: {exc}"


def _handle_send_telegram_file(payload: dict) -> str:
    """Prepare files for Telegram delivery by returning normalized metadata.

    Expected payload:
    - path: str (optional) - single local path or http(s) url
    - paths: list[str] (optional) - multiple local paths or http(s) urls
    - caption: str (optional) - text caption for all files
    """
    raw_path = str(payload.get("path", "")).strip()
    raw_paths = payload.get("paths")
    caption = str(payload.get("caption", "")).strip()

    requested: list[str] = []
    if raw_path:
        requested.append(raw_path)
    if isinstance(raw_paths, list):
        for item in raw_paths:
            value = str(item).strip()
            if value:
                requested.append(value)

    if not requested:
        return json.dumps(
            {
                "ok": False,
                "error": "send_telegram_file error: provide path or paths",
                "files": [],
            },
            ensure_ascii=False,
        )

    files: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in requested:
        if item in seen:
            continue
        seen.add(item)
        if item.startswith(("http://", "https://")):
            file_entry = {"public_url": item}
            if caption:
                file_entry["caption"] = caption
            files.append(file_entry)
            continue

        resolved = Path(item).expanduser()
        if not resolved.exists() or not resolved.is_file():
            errors.append(f"file not found: {resolved}")
            continue
        file_entry = {"local_path": str(resolved)}
        if caption:
            file_entry["caption"] = caption
        files.append(file_entry)

    if not files:
        return json.dumps(
            {
                "ok": False,
                "error": "send_telegram_file error: no valid files",
                "files": [],
                "errors": errors,
            },
            ensure_ascii=False,
        )

    output: dict[str, Any] = {
        "ok": True,
        "mode": "send_telegram_file",
        "files": files,
    }
    if errors:
        output["errors"] = errors
    return json.dumps(output, ensure_ascii=False)


def _handle_send_telegram_photo(payload: dict) -> str:
    """Prepare photos for Telegram delivery by returning normalized metadata.

    Expected payload:
    - path: str (optional) - single local path or http(s) url
    - paths: list[str] (optional) - multiple local paths or http(s) urls
    - caption: str (optional) - text caption for all images
    """
    raw_path = str(payload.get("path", "")).strip()
    raw_paths = payload.get("paths")
    caption = str(payload.get("caption", "")).strip()

    requested: list[str] = []
    if raw_path:
        requested.append(raw_path)
    if isinstance(raw_paths, list):
        for item in raw_paths:
            value = str(item).strip()
            if value:
                requested.append(value)

    if not requested:
        return json.dumps(
            {
                "ok": False,
                "error": "send_telegram_photo error: provide path or paths",
                "images": [],
            },
            ensure_ascii=False,
        )

    images: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in requested:
        if item in seen:
            continue
        seen.add(item)
        if item.startswith(("http://", "https://")):
            image_entry = {"public_url": item}
            if caption:
                image_entry["caption"] = caption
            images.append(image_entry)
            continue

        resolved = Path(item).expanduser()
        if not resolved.exists() or not resolved.is_file():
            errors.append(f"file not found: {resolved}")
            continue
        image_entry = {"local_path": str(resolved)}
        if caption:
            image_entry["caption"] = caption
        images.append(image_entry)

    if not images:
        return json.dumps(
            {
                "ok": False,
                "error": "send_telegram_photo error: no valid images",
                "images": [],
                "errors": errors,
            },
            ensure_ascii=False,
        )

    output: dict[str, Any] = {
        "ok": True,
        "mode": "send_telegram_photo",
        "images": images,
    }
    if errors:
        output["errors"] = errors
    return json.dumps(output, ensure_ascii=False)


def register(registry: Any) -> None:
    """Register telegram_file plugin tools."""
    registry.register(
        ToolPlugin(
            name="list_files",
            description="List files in a directory on the server.",
            parameters={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory path to list (default: current)"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter files (default: *)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum files to return (default: 50)"
                    }
                },
                "required": []
            },
            handler=_handle_list_files,
        )
    )
    registry.register(
        ToolPlugin(
            name="send_telegram_file",
            description="Prepare one or more files for Telegram delivery in the active chat.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Single local file path or public URL"
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple local file paths or public URLs"
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption to send with each file"
                    },
                },
                "required": []
            },
            handler=_handle_send_telegram_file,
        )
    )
    registry.register(
        ToolPlugin(
            name="send_telegram_photo",
            description="Prepare one or more photos for Telegram delivery in the active chat.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Single local image path or public URL"
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple local image paths or public URLs"
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption to send with each image"
                    },
                },
                "required": []
            },
            handler=_handle_send_telegram_photo,
        )
    )
