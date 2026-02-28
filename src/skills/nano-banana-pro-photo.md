Use when the user asks for photo generation or image editing with Gemini/Nano Banana, including multi-image composition, inpainting, interleaved text+image outputs, batch rendering, and search-grounded visuals.

## Goal
Cover all image-generation modes documented in the Gemini guide while staying bash-first and implementation-minimal.

## Source of truth
- https://ai.google.dev/gemini-api/docs/image-generation

## Model policy
1. Default: `gemini-3.1-flash-image-preview` for the standard image flow in this repo.
2. Fallback: `gemini-2.5-flash-image` (Nano Banana) for speed/cost or high-volume rendering.
3. If user asks for a specific Gemini image model, pass it explicitly instead of silently switching defaults.

## Mode coverage (must support all)
1. `Image generation (text-to-image)`
2. `Image editing (text-and-image-to-image)`
3. `Multi-turn image editing` (chat loop)
4. `Other image generation modes`:
   - `Text -> image(s) + text` (interleaved)
   - `Image(s) + text -> image(s) + text` (interleaved)
5. `Generate images in batch` (Batch API)
6. Prompting strategies for generating images:
   - `Photorealistic scenes`
   - `Stylized illustrations & stickers`
   - `Accurate text in images`
   - `Product mockups & commercial photography`
   - `Minimalist & negative space design`
   - `Sequential art (Comic panel / Storyboard)`
   - `Grounding with Google Search`
7. Prompts for editing images:
   - `Adding and removing elements`
   - `Inpainting (Semantic masking)`
   - `Style transfer`
   - `Advanced composition: Combining multiple images`
   - `High-fidelity detail preservation`

## Execution contract (bash-first)
- Prefer shell + curl.
- API key: `GEMINI_API_KEY`.
- If key missing, return `image generation error: missing GEMINI_API_KEY`.
- Always parse output parts; save first image (`inline_data`) and print text parts.
- When `nano_banana_image` plugin exists, prefer calling that plugin over hand-writing one-off HTTP payloads.

### 1) Text-to-image
```bash
API_KEY="${GEMINI_API_KEY}"
MODEL="${MODEL:-gemini-3.1-flash-image-preview}"
PROMPT="<descriptive scene prompt>"

curl -sS -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent" \
  -H "x-goog-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"contents\": [{\"parts\": [{\"text\": \"${PROMPT}\"}]}],
    \"generationConfig\": {
      \"responseModalities\": [\"TEXT\", \"IMAGE\"],
      \"imageConfig\": {\"aspectRatio\": \"16:9\"}
    }
  }"
```

### 2) Text + image -> image edit
- Send input image as `inline_data` plus surgical edit instruction in the same `contents.parts` array.
- Keep unchanged regions explicit (identity, logo, face, background, lighting).

### 3) Multi-turn editing
- Preserve chat history between turns.
- For Nano Banana Pro flows, preserve thought-signature-bearing parts by passing full prior response history forward.

### 4) Interleaved modes
- Use `responseModalities: ["TEXT", "IMAGE"]`.
- Allow mixed outputs (explanatory text + generated frames/assets) for recipes, guides, storyboards, design rationale.

### 5) Batch mode
- For high-volume generation, use Batch API path from official docs.
- Treat as asynchronous job; do not block interactive conversation with long polling unless user asks.

### 6) Search-grounded generation
- Add `tools: [{"google_search": {}}]` when prompt needs real-time facts (scores, weather, events, market data).
- For grounded image requests, state that image-based search results are excluded per docs.

## Prompt protocol (unified)
Use this structure by default:
1. Subject and objective
2. Scene/environment
3. Camera/composition
4. Lighting and color
5. Style/material details
6. Text requirements (if any)
7. Immutable constraints (what must not change)
8. Output intent and aspect ratio

## Optional config checklist
- `responseModalities`: `["TEXT","IMAGE"]` or image-only
- `imageConfig.aspectRatio`: set intentionally
- `imageConfig.imageSize`: use higher sizes on Nano Banana Pro when needed
- Multi-image inputs: use only what is needed; keep ordering explicit

## Guardrails
- Do not edit `src/core` or `src/channel` for image tasks unless user explicitly asks.
- Prefer this skill before proposing new plugins.
- Be explicit about rights/safety when user supplies images.
- Remind that generated images include SynthID watermark.

## Plugin-first execution examples
If runtime plugin `nano_banana_image` is available, use it directly.

1. Text to image:
```json
{"mode":"text_to_image","prompt":"A cinematic close-up of a silver wristwatch on black basalt, studio lighting","aspect_ratio":"16:9","output_dir":"outputs/images"}
```

2. Image edit:
```json
{"mode":"image_edit","prompt":"Keep subject unchanged; replace background with clean white studio","input_images":["<base64_png>"],"output_dir":"outputs/images"}
```

3. Batch generate:
```json
{"mode":"batch_generate","prompts":["Prompt A","Prompt B"],"output_dir":"outputs/images"}
```

4. Search-grounded:
```json
{"mode":"search_grounded_generate","prompt":"Create a visual card for today's Shanghai weather summary","use_google_search":true,"output_dir":"outputs/images"}
```
