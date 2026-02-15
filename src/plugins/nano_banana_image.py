import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.tools import ToolPlugin

DEFAULT_MODEL = "gemini-3-pro-image-preview"
FAST_MODEL = "gemini-2.5-flash-image"
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SUPPORTED_MODES = {
    "text_to_image",
    "image_edit",
    "multi_turn_edit",
    "interleaved_text_image",
    "interleaved_image_text",
    "batch_generate",
    "search_grounded_generate",
}


def _resolve_mode(value: Any) -> str:
    text = str(value or "text_to_image").strip().lower().replace("-", "_")
    aliases = {
        "generate": "text_to_image",
        "generate_image": "text_to_image",
        "edit": "image_edit",
        "multi_turn": "multi_turn_edit",
        "interleaved": "interleaved_text_image",
        "batch": "batch_generate",
        "search_grounded": "search_grounded_generate",
    }
    return aliases.get(text, text)


def _resolve_api_key(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("api_key", "")).strip()
    if explicit:
        return explicit
    return os.getenv("GEMINI_API_KEY", "").strip()


def _resolve_model(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("model", "")).strip()
    if explicit:
        return explicit
    fast = bool(payload.get("fast", False))
    return FAST_MODEL if fast else DEFAULT_MODEL


def _read_int(payload: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    raw = payload.get(name, default)
    try:
        value = int(raw)
    except Exception:
        value = default
    if value < low:
        return low
    if value > high:
        return high
    return value


def _as_list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _image_part(image_b64: str, mime_type: str) -> dict[str, Any]:
    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": image_b64,
        }
    }


def _build_generation_config(payload: dict[str, Any], *, include_text: bool = True) -> dict[str, Any]:
    modal = payload.get("response_modalities")
    if isinstance(modal, list) and modal:
        response_modalities = [str(item).strip().upper() for item in modal if str(item).strip()]
    elif bool(payload.get("image_only", False)):
        response_modalities = ["IMAGE"]
    else:
        response_modalities = ["TEXT", "IMAGE"] if include_text else ["IMAGE"]

    image_cfg: dict[str, Any] = {}
    aspect_ratio = str(payload.get("aspect_ratio", "")).strip()
    image_size = str(payload.get("image_size", "")).strip()
    if aspect_ratio:
        image_cfg["aspectRatio"] = aspect_ratio
    if image_size:
        image_cfg["imageSize"] = image_size

    config: dict[str, Any] = {"responseModalities": response_modalities}
    if image_cfg:
        config["imageConfig"] = image_cfg
    return config


def _build_history_contents(payload: dict[str, Any], mime_type: str) -> list[dict[str, Any]]:
    history = payload.get("history")
    if not isinstance(history, list):
        return []

    contents: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user")).strip().lower()
        if role not in {"user", "model"}:
            role = "user"

        parts: list[dict[str, Any]] = []
        text = str(item.get("text", "")).strip()
        if text:
            parts.append({"text": text})

        images = _as_list_of_strings(item.get("input_images", []))
        image_one = str(item.get("input_image", "")).strip()
        if image_one:
            images.append(image_one)
        for image_b64 in images:
            parts.append(_image_part(image_b64, mime_type))

        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def _build_single_request(payload: dict[str, Any], mode: str, prompt_text: str) -> tuple[dict[str, Any], str | None]:
    mime_type = str(payload.get("mime_type", "image/png")).strip() or "image/png"
    input_images = _as_list_of_strings(payload.get("input_images", []))
    input_image = str(payload.get("input_image", "")).strip()
    if input_image:
        input_images.insert(0, input_image)

    if mode in {"text_to_image", "interleaved_text_image", "search_grounded_generate"} and not prompt_text:
        return {}, "image generation error: prompt is required"

    if mode in {"image_edit", "interleaved_image_text"}:
        if not input_images:
            return {}, "image generation error: at least one input image is required"
        if not prompt_text:
            return {}, "image generation error: prompt is required"

    contents: list[dict[str, Any]] = []

    if mode == "multi_turn_edit":
        contents.extend(_build_history_contents(payload, mime_type))
        if not contents and not prompt_text:
            return {}, "image generation error: history or prompt is required for multi_turn_edit"

    current_parts: list[dict[str, Any]] = []
    if prompt_text:
        current_parts.append({"text": prompt_text})
    for image_b64 in input_images:
        current_parts.append(_image_part(image_b64, mime_type))
    if current_parts:
        contents.append({"role": "user", "parts": current_parts})

    if not contents:
        return {}, "image generation error: empty request contents"

    include_text = mode in {
        "interleaved_text_image",
        "interleaved_image_text",
        "multi_turn_edit",
        "search_grounded_generate",
    }
    request_body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": _build_generation_config(payload, include_text=include_text),
    }

    use_google_search = bool(payload.get("use_google_search", False)) or mode == "search_grounded_generate"
    if use_google_search:
        request_body["tools"] = [{"google_search": {}}]

    return request_body, None


def _post_json(model: str, api_key: str, body: dict[str, Any], timeout_sec: int) -> tuple[dict[str, Any] | None, str | None]:
    url = API_URL_TEMPLATE.format(model=model)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        return None, f"image generation error: http {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return None, f"image generation error: {exc.reason}"
    except Exception as exc:
        return None, f"image generation error: {exc}"

    try:
        return json.loads(raw), None
    except Exception as exc:
        return None, f"image generation error: invalid json response: {exc}"


def _extract_output(payload: dict[str, Any], response_data: dict[str, Any], index: int = 1) -> str:
    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "image generation error: no candidates returned"

    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list):
        parts = []

    output_lines: list[str] = []
    image_saved = False
    output_dir_raw = str(payload.get("output_dir", "")).strip()
    output_prefix = str(payload.get("output_prefix", "nano_banana")).strip() or "nano_banana"
    output_dir = Path(output_dir_raw) if output_dir_raw else None

    image_count = 0
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            output_lines.append(f"text: {text.strip()}")

        inline = part.get("inline_data") or part.get("inlineData")
        if isinstance(inline, dict):
            data = inline.get("data")
            if isinstance(data, str) and data.strip():
                image_count += 1
                if output_dir is not None:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    path = output_dir / f"{output_prefix}_{index}_{image_count}.png"
                    try:
                        path.write_bytes(base64.b64decode(data))
                        output_lines.append(f"image_saved: {path}")
                        image_saved = True
                    except Exception as exc:
                        output_lines.append(f"image_save_error: {exc}")
                else:
                    preview = data[:80]
                    output_lines.append(f"image_base64_preview: {preview}...")

    if image_count == 0:
        if output_lines:
            return "\n".join(output_lines)
        return "image generation error: no image part in response"

    if not output_lines:
        return f"image generated: {image_count}"
    if image_saved:
        output_lines.append("status: ok")
    return "\n".join(output_lines)


def _run_single(payload: dict[str, Any], mode: str, api_key: str, model: str) -> str:
    prompt = str(payload.get("prompt", "")).strip()
    body, error = _build_single_request(payload, mode, prompt)
    if error:
        return error

    timeout_sec = _read_int(payload, "timeout_sec", 90, 10, 300)
    response_data, request_error = _post_json(model, api_key, body, timeout_sec)
    if request_error:
        return request_error
    if response_data is None:
        return "image generation error: empty response"
    return _extract_output(payload, response_data, index=1)


def _run_batch(payload: dict[str, Any], api_key: str, model: str) -> str:
    prompts = _as_list_of_strings(payload.get("prompts", []))
    fallback_prompt = str(payload.get("prompt", "")).strip()
    count = _read_int(payload, "batch_count", 1, 1, 8)

    if not prompts:
        if not fallback_prompt:
            return "image generation error: batch_generate requires prompts[] or prompt"
        prompts = [fallback_prompt for _ in range(count)]

    outputs: list[str] = []
    for index, prompt in enumerate(prompts, start=1):
        child_payload = dict(payload)
        child_payload["prompt"] = prompt
        body, error = _build_single_request(child_payload, "text_to_image", prompt)
        if error:
            outputs.append(f"batch[{index}] {error}")
            continue

        timeout_sec = _read_int(payload, "timeout_sec", 90, 10, 300)
        response_data, request_error = _post_json(model, api_key, body, timeout_sec)
        if request_error:
            outputs.append(f"batch[{index}] {request_error}")
            continue
        if response_data is None:
            outputs.append(f"batch[{index}] image generation error: empty response")
            continue
        result = _extract_output(payload, response_data, index=index)
        outputs.append(f"batch[{index}] {result}")

    return "\n".join(outputs)


def _handle(payload: dict[str, Any]) -> str:
    try:
        mode = _resolve_mode(payload.get("mode"))
        if mode not in SUPPORTED_MODES:
            supported = ", ".join(sorted(SUPPORTED_MODES))
            return f"image generation error: unsupported mode '{mode}'. supported: {supported}"

        api_key = _resolve_api_key(payload)
        if not api_key:
            return "image generation error: missing GEMINI_API_KEY"

        model = _resolve_model(payload)

        if mode == "batch_generate":
            return _run_batch(payload, api_key, model)
        return _run_single(payload, mode, api_key, model)
    except Exception as exc:
        return f"image generation error: {exc}"


def register(registry) -> None:
    registry.register(
        ToolPlugin(
            name="nano_banana_image",
            description=(
                "Generate/edit images via Gemini (text-to-image, image edit, multi-turn, "
                "interleaved, batch, search-grounded)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_MODES),
                        "description": "Image mode to run.",
                    },
                    "prompt": {"type": "string"},
                    "prompts": {"type": "array", "items": {"type": "string"}},
                    "input_image": {
                        "type": "string",
                        "description": "Single base64 image for edit/interleaved modes.",
                    },
                    "input_images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple base64 images for composition/editing.",
                    },
                    "history": {
                        "type": "array",
                        "description": "For multi_turn_edit; items: {role,text,input_images[]}",
                        "items": {"type": "object"},
                    },
                    "model": {"type": "string"},
                    "fast": {"type": "boolean"},
                    "api_key": {"type": "string"},
                    "mime_type": {"type": "string"},
                    "response_modalities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "aspect_ratio": {"type": "string"},
                    "image_size": {"type": "string"},
                    "use_google_search": {"type": "boolean"},
                    "timeout_sec": {"type": "integer"},
                    "batch_count": {"type": "integer"},
                    "output_dir": {"type": "string"},
                    "output_prefix": {"type": "string"},
                    "image_only": {"type": "boolean"},
                },
                "required": ["mode"],
            },
            handler=_handle,
        )
    )
