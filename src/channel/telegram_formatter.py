"""
Telegram MarkdownV2 Formatter

Converts CommonMarkdown to Telegram MarkdownV2 format.
Handles proper escaping while preserving format markers.
"""

from dataclasses import dataclass
from html import escape as escape_html
import logging
import re
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramRenderedText:
    text: str
    parse_mode: str | None = None
    fallback_text: str = ""


INLINE_HTML_CODE_RE = re.compile(r"(`)([^`]+?)(`)")
INLINE_HTML_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
INLINE_HTML_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
INLINE_HTML_ITALIC_RE = re.compile(r"(?<![\w\*_])(\*|_)(.+?)\1(?![\w\*_])", re.DOTALL)
INLINE_HTML_STRIKE_RE = re.compile(r"(~~)(.+?)\1", re.DOTALL)
INLINE_HTML_SPOILER_RE = re.compile(r"(\|\|)(.+?)\1", re.DOTALL)


def render_telegram_message(
    text: Optional[str],
    use_formatting: bool = True,
    max_chars: int = 4096,
) -> TelegramRenderedText:
    raw_text = str(text or "")
    if not raw_text:
        return TelegramRenderedText("", None, "")
    if not use_formatting:
        return TelegramRenderedText(raw_text, None, raw_text)

    html_text = markdown_to_telegram_html(raw_text)
    if html_text and len(html_text) <= max_chars:
        return TelegramRenderedText(html_text, "HTML", raw_text)
    return TelegramRenderedText(raw_text, None, raw_text)


def markdown_to_telegram_html(text: str) -> str:
    if not text:
        return ""

    lines = str(text).splitlines()
    rendered_lines: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                rendered_lines.append(
                    f"<pre><code>{escape_html('\n'.join(code_lines), quote=False)}</code></pre>"
                )
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if not line:
            rendered_lines.append("")
            continue

        if stripped.startswith(">"):
            quote_text = stripped.lstrip(">").strip()
            rendered_lines.append(f"<blockquote>{_render_inline_html(quote_text)}</blockquote>")
            continue

        rendered_lines.append(_render_inline_html(line))

    if in_code_block:
        rendered_lines.append(
            f"<pre><code>{escape_html('\n'.join(code_lines), quote=False)}</code></pre>"
        )

    return "\n".join(rendered_lines)


def _render_inline_html(text: str) -> str:
    if not text:
        return ""

    result: list[str] = []
    pos = 0
    patterns = [
        ("inline_code", INLINE_HTML_CODE_RE),
        ("link", INLINE_HTML_LINK_RE),
        ("bold", INLINE_HTML_BOLD_RE),
        ("italic", INLINE_HTML_ITALIC_RE),
        ("strike", INLINE_HTML_STRIKE_RE),
        ("spoiler", INLINE_HTML_SPOILER_RE),
    ]

    while pos < len(text):
        matched = False
        for token_type, pattern in patterns:
            match = pattern.match(text, pos)
            if not match:
                continue
            matched = True
            if token_type == "inline_code":
                result.append(f"<code>{escape_html(match.group(2), quote=False)}</code>")
            elif token_type == "link":
                label = _render_inline_html(match.group(1))
                href = escape_html(match.group(2).strip(), quote=True)
                result.append(f'<a href="{href}">{label}</a>')
            elif token_type == "bold":
                result.append(f"<b>{_render_inline_html(match.group(2))}</b>")
            elif token_type == "italic":
                result.append(f"<i>{_render_inline_html(match.group(2))}</i>")
            elif token_type == "strike":
                result.append(f"<s>{_render_inline_html(match.group(2))}</s>")
            else:
                result.append(f"<tg-spoiler>{_render_inline_html(match.group(2))}</tg-spoiler>")
            pos = match.end()
            break
        if matched:
            continue
        result.append(escape_html(text[pos], quote=False))
        pos += 1

    return "".join(result)

# MarkdownV2 special chars that must be escaped in plain text
MD_V2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"

# Regex patterns for format markers
CODE_BLOCK_RE = re.compile(r'(```)([\s\S]+?)(```)')
INLINE_CODE_RE = re.compile(r'(`)([^`]+?)(`)')
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
# Bold: **text** or __text__ (preserve original delimiter)
BOLD_RE = re.compile(r'(\*\*|__)(.+?)\1')
# Italic: *text* or _text_ (but not part of ** or __)
ITALIC_RE = re.compile(r'(?<![\*_])(\*|_)(.+?)\1(?![\*_])')


def escape_markdown_v2(text: str) -> str:
    """
    Escape text for MarkdownV2 (no format markers).
    
    Args:
        text: Plain text to escape
    
    Returns:
        Escaped text safe for MarkdownV2
    """
    if not text:
        return ""
    
    result = []
    for char in text:
        if char in MD_V2_SPECIAL_CHARS:
            result.append('\\')
        result.append(char)
    
    return ''.join(result)


def format_response(text: Optional[str], use_formatting: bool = True) -> str:
    """
    Format bot response for Telegram.
    
    Args:
        text: Response text (may contain CommonMarkdown)
        use_formatting: Whether to apply MarkdownV2 formatting
    
    Returns:
        Formatted text ready for Telegram
    """
    if not text or not use_formatting:
        return str(text) if text else ""

    rendered = render_telegram_message(text, use_formatting=use_formatting)
    return rendered.text


def convert_markdown_to_v2(md_text: str) -> str:
    """
    Convert CommonMarkdown to Telegram MarkdownV2 format.
    
    Supported formats:
    - Bold: **text** or __text__
    - Italic: *text* or _text_
    - Inline code: `text`
    - Code block: ```text```
    - Links: [text](url)
    
    Args:
        md_text: CommonMarkdown text
    
    Returns:
        MarkdownV2 formatted text
    """
    if not md_text:
        return ""
    
    try:
        # Parse tokens: code_block, inline_code, link, bold, italic, text
        tokens = _tokenize(md_text)
        
        # Convert tokens to MarkdownV2
        return _convert_tokens(tokens)
        
    except Exception as e:
        logger.error(f"MarkdownV2 conversion error: {e}")
        return md_text


def _tokenize(text: str) -> List[Tuple[str, str]]:
    """
    Tokenize text into format markers and content.
    
    Returns:
        List of (type, content) tuples
        Types: code_block, inline_code, link, bold, italic, text
    """
    tokens = []
    pos = 0
    
    while pos < len(text):
        # Try each pattern in order
        matched = False
        
        # Code block (highest priority)
        match = CODE_BLOCK_RE.match(text, pos)
        if match:
            tokens.append(('code_block', match.group(0)))
            pos = match.end()
            matched = True
            continue
        
        # Inline code
        match = INLINE_CODE_RE.match(text, pos)
        if match:
            tokens.append(('inline_code', match.group(0)))
            pos = match.end()
            matched = True
            continue
        
        # Link
        match = LINK_RE.match(text, pos)
        if match:
            tokens.append(('link', match.group(0)))
            pos = match.end()
            matched = True
            continue
        
        # Bold
        match = BOLD_RE.match(text, pos)
        if match:
            delimiter = match.group(1)  # ** or __
            content = match.group(2)
            tokens.append(('bold', content, delimiter))
            pos = match.end()
            matched = True
            continue
        
        # Italic
        match = ITALIC_RE.match(text, pos)
        if match:
            delimiter = match.group(1)  # * or _
            content = match.group(2)
            tokens.append(('italic', content, delimiter))
            pos = match.end()
            matched = True
            continue
        
        # Plain text (accumulate until next pattern)
        end_pos = pos
        while end_pos < len(text):
            # Check if any pattern starts here
            if (CODE_BLOCK_RE.match(text, end_pos) or
                INLINE_CODE_RE.match(text, end_pos) or
                LINK_RE.match(text, end_pos) or
                BOLD_RE.match(text, end_pos) or
                ITALIC_RE.match(text, end_pos)):
                break
            end_pos += 1
        
        if end_pos > pos:
            tokens.append(('text', text[pos:end_pos]))
            pos = end_pos
        else:
            # Should not happen, but safety
            tokens.append(('text', text[pos]))
            pos += 1
    
    return tokens


def _convert_tokens(tokens: List[tuple]) -> str:
    """Convert tokens to MarkdownV2 format."""
    result = []
    
    for token in tokens:
        token_type = token[0]
        
        if token_type == 'code_block':
            # Code block: escape backslashes only
            content = token[1]
            # Extract content between ```
            inner = content[3:-3]
            # Escape backslashes
            escaped_inner = inner.replace('\\', '\\\\')
            result.append(f'```{escaped_inner}```')
        
        elif token_type == 'inline_code':
            # Inline code: escape backslashes
            content = token[1]
            inner = content[1:-1]  # Remove surrounding `
            escaped_inner = inner.replace('\\', '\\\\')
            result.append(f'`{escaped_inner}`')
        
        elif token_type == 'link':
            # Link: [text](url)
            content = token[1]
            match = LINK_RE.match(content)
            if match:
                link_text = match.group(1)
                url = match.group(2)
                # Escape link text
                escaped_text = escape_markdown_v2(link_text)
                # URL: escape backslashes only
                escaped_url = url.replace('\\', '\\\\')
                result.append(f'[{escaped_text}]({escaped_url})')
        
        elif token_type == 'bold':
            # Bold: preserve delimiter
            content = token[1]
            delimiter = token[2]
            # Recursively process content for nested formats
            inner_tokens = _tokenize(content)
            inner = _convert_tokens(inner_tokens)
            result.append(f'{delimiter}{inner}{delimiter}')
        
        elif token_type == 'italic':
            # Italic: preserve delimiter
            content = token[1]
            delimiter = token[2]
            # Recursively process content
            inner_tokens = _tokenize(content)
            inner = _convert_tokens(inner_tokens)
            result.append(f'{delimiter}{inner}{delimiter}')
        
        else:  # text
            # Plain text: escape all special chars
            result.append(escape_markdown_v2(token[1]))
    
    return ''.join(result)


def split_message_safe(text: str, limit: int = 4096) -> List[str]:
    """
    Split long message into chunks respecting character limit.
    
    Tries to split at sentence boundaries first.
    
    Args:
        text: Message text
        limit: Character limit per chunk (default 4096 for Telegram)
    
    Returns:
        List of message chunks
    """
    if len(text) <= limit:
        return [text]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    # Split by paragraphs first
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        para_len = len(para) + 2  # +2 for newlines
        
        if current_length + para_len <= limit:
            current_chunk.append(para)
            current_length += para_len
        else:
            # Save current chunk
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_length = 0
            
            # Check if single paragraph exceeds limit
            if len(para) > limit:
                # Split by sentences
                sentences = re.split(r'(?<=[.!?])\s+', para)
                
                for sent in sentences:
                    if current_length + len(sent) + 1 <= limit:
                        current_chunk.append(sent)
                        current_length += len(sent) + 1
                    else:
                        if current_chunk:
                            chunks.append(' '.join(current_chunk))
                        
                        # Still too long? Force split
                        if len(sent) > limit:
                            for i in range(0, len(sent), limit):
                                chunks.append(sent[i:i+limit])
                            current_chunk = []
                            current_length = 0
                        else:
                            current_chunk = [sent]
                            current_length = len(sent)
            else:
                current_chunk = [para]
                current_length = para_len
    
    # Last chunk
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
    
    return chunks
