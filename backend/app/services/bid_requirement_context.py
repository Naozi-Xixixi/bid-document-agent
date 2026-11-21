"""Choose current-tender requirements relevant to one chapter before prompting."""

from __future__ import annotations

import re


def _bigrams(text: str) -> set[str]:
    chars = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
    return {chars[index:index + 2] for index in range(max(0, len(chars) - 1))}


def select_requirements(text: str, chapter: str, *, max_chars: int = 3600) -> str:
    source = (text or "").strip()
    if len(source) <= max_chars:
        return source
    pieces = [part.strip() for part in re.split(r"\n+|(?<=[。；;])", source) if part.strip()]
    segments: list[str] = []
    for piece in pieces:
        segments.extend(piece[i:i + 550] for i in range(0, len(piece), 550))
    query = _bigrams(chapter)
    # Common words in most chapters should not outrank a specific scoring item.
    query.difference_update({"项目", "方案", "服务", "管理", "要求", "实施", "投标"})
    ranked = sorted(
        enumerate(segments),
        key=lambda row: (len(query & _bigrams(row[1])) / max(1, len(query)), -row[0]),
        reverse=True,
    )
    chosen: list[tuple[int, str]] = []
    used = 0
    for index, part in ranked:
        if used + len(part) > max_chars:
            continue
        chosen.append((index, part))
        used += len(part)
        if used >= max_chars - 300:
            break
    if not chosen:
        return source[:max_chars]
    return "\n".join(part for _, part in sorted(chosen))[:max_chars]
