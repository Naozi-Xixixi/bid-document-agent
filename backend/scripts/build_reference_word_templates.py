from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_service import _extract_docx_text
from app.services.word_template_service import _write_template


def slugify(name: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", Path(name).stem).strip("-").lower()
    return value[:80] or "reference-template"


def package_stats(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        media = [archive.getinfo(name) for name in names if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml")
        return {
            "zip_entries": len(names),
            "document_xml_bytes": len(document_xml),
            "paragraph_count": len(re.findall(rb"<w:p(?: |>)", document_xml)),
            "table_count": len(re.findall(rb"<w:tbl(?: |>)", document_xml)),
            "section_count": len(re.findall(rb"<w:sectPr(?: |>)", document_xml)),
            "source_media_count": len(media),
            "source_media_bytes": sum(info.file_size for info in media),
        }


def build(source: Path, output_dir: Path) -> dict[str, object]:
    if source.suffix.lower() != ".docx":
        raise ValueError(f"仅支持 DOCX：{source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(source.name)
    target = output_dir / f"{slug}.docx"
    manifest_path = output_dir / f"{slug}.json"
    text_path = output_dir / f"{slug}.txt"
    source_stats = package_stats(source)
    rules = _write_template(source, target)
    text = _extract_docx_text(source)
    text_path.write_text(text, encoding="utf-8")
    source_hash = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            source_hash.update(chunk)
    target_size = target.stat().st_size
    manifest = {
        "template_id": f"reference:{slug}",
        "name": f"{source.stem} 格式模板",
        "source_file": str(source),
        "source_sha256": source_hash.hexdigest(),
        "template_file": str(target),
        "text_file": str(text_path),
        "source_stats": source_stats,
        "template_size": target_size,
        "format_rules": rules,
        "processing": {
            "text_extraction": "OOXML streaming; document.xml only",
            "body_images": "removed from template; extracted lazily by asset pipeline",
            "preserved_headers_footers": True,
        },
    }
    manifest["name"] = f"{source.stem} 格式模板"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract lightweight reusable DOCX bid format templates")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    for source in args.sources:
        print(json.dumps(build(source.resolve(), args.output_dir.resolve()), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
