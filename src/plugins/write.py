"""File write tool plugin for Abraxas runtime."""
import os
from pathlib import Path
from core.tools import ToolPlugin


def _handle(payload: dict) -> str:
    """
    Write content to a file.
    
    Expected payload:
        path: str - target file path (relative or absolute)
        content: str - content to write
        mode: str - write mode: 'write' (overwrite), 'append', 'prepend' (default: 'write')
        mkdir: bool - create parent directories if needed (default: true)
    
    Returns:
        str - status message or error
    """
    try:
        # Extract parameters with defaults
        path = payload.get("path", "").strip()
        content = payload.get("content", "")
        mode = str(payload.get("mode", "write")).strip().lower()
        mkdir = payload.get("mkdir", True)
        
        if not path:
            return "plugin error: path is required"
        
        # Convert to Path object
        target_path = Path(path).expanduser().resolve()
        
        # Validate mode
        valid_modes = {"write", "append", "prepend"}
        if mode not in valid_modes:
            return f"plugin error: invalid mode '{mode}'. Use: write, append, or prepend"
        
        # Create parent directories if requested
        if mkdir:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        elif not target_path.parent.exists():
            return f"plugin error: parent directory does not exist and mkdir=false: {target_path.parent}"
        
        # Handle different modes
        if mode == "write":
            target_path.write_text(content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))
            return f"ok: wrote {bytes_written} bytes to {target_path}"
        
        elif mode == "append":
            if target_path.exists():
                existing = target_path.read_text(encoding="utf-8")
                new_content = existing + content
            else:
                new_content = content
            target_path.write_text(new_content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))
            return f"ok: appended {bytes_written} bytes to {target_path}"
        
        elif mode == "prepend":
            if target_path.exists():
                existing = target_path.read_text(encoding="utf-8")
                new_content = content + existing
            else:
                new_content = content
            target_path.write_text(new_content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))
            return f"ok: prepended {bytes_written} bytes to {target_path}"
        
    except PermissionError:
        return f"plugin error: permission denied writing to {path}"
    except IsADirectoryError:
        return f"plugin error: path is a directory, not a file: {path}"
    except Exception as exc:
        return f"plugin error: {exc}"


def register(registry) -> None:
    """Register the write tool plugin."""
    registry.register(
        ToolPlugin(
            name="write",
            description="Write content to files with options for overwrite/append/prepend.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path (relative or absolute)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["write", "append", "prepend"],
                        "description": "Write mode: write (overwrite), append, or prepend"
                    },
                    "mkdir": {
                        "type": "boolean",
                        "description": "Create parent directories if needed"
                    }
                },
                "required": ["path", "content"]
            },
            handler=_handle,
        )
    )
