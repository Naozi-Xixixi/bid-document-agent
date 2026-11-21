from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


DEFAULT_HEADING_STYLES: tuple[dict[str, Any], ...] = (
    {
        "selector": "paragraph[style=Title]",
        "props": {
            "color": "3A3A32",
            "bold": "true",
            "size": "20pt",
        },
    },
    {
        "selector": "paragraph[style=Heading1]",
        "props": {
            "color": "3A3A32",
            "bold": "true",
            "size": "16pt",
        },
    },
    {
        "selector": "paragraph[style=Heading2]",
        "props": {
            "color": "3A3A32",
            "bold": "true",
            "size": "14pt",
        },
    },
    {
        "selector": "paragraph[style=Heading3]",
        "props": {
            "color": "3A3A32",
            "bold": "true",
            "size": "13pt",
        },
    },
)


def enhance_docx_with_officecli(
    docx_bytes: bytes,
    *,
    heading_styles: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    body_font: str = "宋体",
    body_size: str = "12pt",
) -> bytes:
    """Run optional OfficeCLI post-processing on generated bid DOCX bytes.

    The export path must keep working even when OfficeCLI is not installed, so
    every failure falls back to the original python-docx output.
    """

    if not settings.enable_officecli_docx_formatting:
        return docx_bytes

    officecli = _officecli_executable()
    if not officecli:
        logger.info("OfficeCLI DOCX formatting skipped: binary not found")
        return docx_bytes

    try:
        with tempfile.TemporaryDirectory(prefix="wordagent-officecli-") as temp_dir:
            docx_path = Path(temp_dir) / "generated-bid.docx"
            docx_path.write_bytes(docx_bytes)

            _run_officecli([officecli, "open", str(docx_path)], required=False)
            _apply_document_defaults(officecli, docx_path, body_font=body_font, body_size=body_size)
            styled_count = _apply_heading_styles(officecli, docx_path, tuple(heading_styles or DEFAULT_HEADING_STYLES))
            _run_officecli([officecli, "validate", str(docx_path)], required=False)
            _run_officecli([officecli, "close", str(docx_path)], required=False)

            formatted = docx_path.read_bytes()
            logger.info("OfficeCLI DOCX formatting completed; styled_headings=%s", styled_count)
            return formatted or docx_bytes
    except Exception as exc:  # noqa: BLE001 - optional formatter must not break export.
        logger.warning("OfficeCLI DOCX formatting failed, falling back to raw DOCX: %s", exc)
        return docx_bytes


def format_docx_with_officecli(
    docx_bytes: bytes,
    heading_styles: list[dict[str, Any]],
    *,
    body_font: str = "宋体",
    body_size: str = "12pt",
) -> tuple[bytes, dict[str, Any]]:
    """Apply user-selected DOCX formatting through OfficeCLI."""

    if not settings.enable_officecli_docx_formatting:
        return docx_bytes, {"officecli_used": False, "styled_headings": 0, "error": "OfficeCLI formatting is disabled"}

    officecli = _officecli_executable()
    if not officecli:
        return docx_bytes, {"officecli_used": False, "styled_headings": 0, "error": "OfficeCLI binary not found"}

    with tempfile.TemporaryDirectory(prefix="wordagent-format-officecli-") as temp_dir:
        docx_path = Path(temp_dir) / "uploaded-bid.docx"
        docx_path.write_bytes(docx_bytes)

        _run_officecli([officecli, "open", str(docx_path)], required=False)
        _apply_document_defaults(officecli, docx_path, body_font=body_font, body_size=body_size)
        styled_count = _apply_heading_styles(officecli, docx_path, tuple(heading_styles))
        validation = _run_officecli([officecli, "validate", str(docx_path)], required=False)
        _run_officecli([officecli, "close", str(docx_path)], required=False)

        return docx_path.read_bytes(), {
            "officecli_used": True,
            "styled_headings": styled_count,
            "validation_ok": validation.returncode == 0,
            "validation_message": (validation.stdout or validation.stderr or "").strip(),
        }


def _officecli_executable() -> str | None:
    configured = settings.officecli_binary.strip() or "officecli"
    path = Path(configured)
    if path.is_absolute() and path.exists():
        return str(path)
    return shutil.which(configured)


def _apply_document_defaults(officecli: str, docx_path: Path, *, body_font: str = "宋体", body_size: str = "12pt") -> None:
    _run_officecli(
        [
            officecli,
            "set",
            str(docx_path),
            "/",
            "--prop",
            "docDefaults.font=宋体",
            "--prop",
            "docDefaults.fontSize=12pt",
            "--prop",
            f"docDefaults.font={body_font}",
            "--prop",
            f"docDefaults.fontSize={body_size}",
        ],
        required=False,
    )


def _apply_heading_styles(officecli: str, docx_path: Path, heading_styles: tuple[dict[str, Any], ...]) -> int:
    styled_count = 0
    for style in heading_styles:
        selector = str(style["selector"])
        paths = _query_paths(officecli, docx_path, selector)
        for target_path in paths:
            args = [officecli, "set", str(docx_path), target_path]
            for key, value in style["props"].items():
                args.extend(["--prop", f"{key}={value}"])
            if _run_officecli(args, required=False).returncode == 0:
                styled_count += 1
    return styled_count


def _query_paths(officecli: str, docx_path: Path, selector: str) -> list[str]:
    result = _run_officecli([officecli, "query", str(docx_path), selector, "--json"], required=False)
    if result.returncode != 0:
        return []
    paths = _paths_from_query_json(result.stdout)
    if paths:
        return paths
    return _paths_from_text(result.stdout)


def _paths_from_query_json(payload: str) -> list[str]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []

    candidates: list[Any]
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict) and isinstance(nested.get("Results"), list):
            candidates = nested["Results"]
        elif isinstance(nested, dict) and isinstance(nested.get("results"), list):
            candidates = nested["results"]
        elif isinstance(data.get("Results"), list):
            candidates = data["Results"]
        elif isinstance(data.get("results"), list):
            candidates = data["results"]
        else:
            candidates = []
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []

    paths: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            path = item.get("path") or item.get("Path")
            if isinstance(path, str) and path.startswith("/"):
                paths.append(path)
    return _unique_paths(paths)


def _paths_from_text(payload: str) -> list[str]:
    paths = re.findall(r"(?m)(/[A-Za-z0-9_@\[\]\-/]+)", payload)
    return _unique_paths(paths)


def _unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _run_officecli(args: list[str], *, required: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=settings.officecli_timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        logger.debug("OfficeCLI command failed: args=%s output=%s", args, message)
        if required:
            raise RuntimeError(message or f"officecli exited with code {result.returncode}")
    return result
