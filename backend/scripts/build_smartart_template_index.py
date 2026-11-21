from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


TEMPLATE_DEFINITIONS: dict[str, dict[str, str]] = {
    "arrow_process": {"name": "彩色箭头流程", "scene": "阶段递进、流程步骤", "office_layout_id": "ContinuousArrowProcess"},
    "vertical_card_list": {"name": "垂直卡片列表", "scene": "模块说明、能力清单", "office_layout_id": "VerticalBlockList"},
    "timeline_milestone": {"name": "时间轴里程碑", "scene": "项目周期、实施阶段", "office_layout_id": "MilestoneTimeline"},
    "radial_relation": {"name": "中心辐射关系图", "scene": "核心能力、体系框架", "office_layout_id": "RadialCycle"},
    "hexagon_matrix": {"name": "六边形矩阵", "scene": "分类说明、并列模块", "office_layout_id": "HexagonCluster"},
    "quadrant_matrix": {"name": "四象限卡片矩阵", "scene": "维度分析、能力矩阵", "office_layout_id": "GridMatrix"},
    "step_hierarchy": {"name": "阶梯式层级图", "scene": "组织架构、层级体系", "office_layout_id": "TableHierarchy"},
    "circle_category": {"name": "圆形分类图", "scene": "分类说明、要点列举", "office_layout_id": "CircleArrowProcess"},
    "basic_hierarchy": {"name": "基础层级图", "scene": "通用层级关系、体系框架", "office_layout_id": "HorizontalMultilevelList"},
}


PROMPT_HINTS: dict[str, str] = {
    "arrow_process": "参考素材库中的连续箭头流程：节点横向首尾拼接，适合阶段递进，不单独绘制复杂连接线。",
    "vertical_card_list": "参考素材库中的纵向卡片列表：卡片上下排列，左侧可带序号标记，适合模块清单。",
    "timeline_milestone": "参考素材库中的里程碑时间轴：横向主线、圆形节点、说明卡片上下交替。",
    "radial_relation": "参考素材库中的中心辐射关系：中心主节点突出，周围子节点环绕并连接中心。",
    "hexagon_matrix": "参考素材库中的六边形群组：六边形节点并列或错落排列，适合分类模块。",
    "quadrant_matrix": "参考素材库中的四象限矩阵：2x2 网格卡片，行列严格对齐。",
    "step_hierarchy": "参考素材库中的阶梯层级：节点逐级缩进，形成清晰层级台阶。",
    "circle_category": "参考素材库中的圆形分类：左侧圆形序号与右侧说明卡片成组排列。",
    "basic_hierarchy": "参考素材库中的基础层级：上下分层、同层对齐、连接关系克制清晰。",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SmartArt PPTX template manifest for bid diagram generation.")
    parser.add_argument("--source", required=True, help="Source .pptx file path")
    parser.add_argument("--output", required=True, help="Output manifest json path")
    parser.add_argument("--top-per-category", type=int, default=36, help="Recommended candidates kept per category")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    manifest = build_manifest(source, top_per_category=args.top_per_category)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    print(f"slides={manifest['slide_count']} indexed={len(manifest['templates'])}")
    for category, items in manifest["recommended_by_category"].items():
        print(f"{category}: {len(items)}")


def build_manifest(source: Path, *, top_per_category: int = 36) -> dict[str, Any]:
    with ZipFile(source) as archive:
        slide_paths = _slide_paths_in_order(archive)
        templates = []
        for index, slide_path in enumerate(slide_paths, 1):
            try:
                info = _inspect_slide(archive, slide_path, index)
            except Exception as exc:  # noqa: BLE001 - bad third-party template slide should not kill indexing.
                info = {
                    "template_id": f"ppt_smartart_{index:04d}",
                    "slide_number": index,
                    "source_slide_path": slide_path,
                    "category": "basic_hierarchy",
                    "confidence": 0.1,
                    "error": str(exc),
                    "shape_counts": {},
                    "text_examples": [],
                    "tags": ["parse_error"],
                }
            templates.append(info)

    recommended: dict[str, list[dict[str, Any]]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in templates:
        grouped[item["category"]].append(item)
    for category in TEMPLATE_DEFINITIONS:
        ranked = sorted(grouped.get(category, []), key=lambda item: (item.get("confidence", 0), item.get("shape_count", 0)), reverse=True)
        recommended[category] = [
            {
                "template_id": item["template_id"],
                "slide_number": item["slide_number"],
                "confidence": item["confidence"],
                "shape_counts": item.get("shape_counts", {}),
                "tags": item.get("tags", []),
                "prompt_hint": item.get("prompt_hint", PROMPT_HINTS.get(category, "")),
            }
            for item in ranked[:top_per_category]
        ]

    return {
        "version": 1,
        "source_file": source.name,
        "source_path": str(source),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "slide_count": len(templates),
        "template_definitions": TEMPLATE_DEFINITIONS,
        "recommended_by_category": recommended,
        "templates": templates,
    }


def _slide_paths_in_order(archive: ZipFile) -> list[str]:
    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        rel_by_id = {rel.attrib.get("Id"): rel.attrib.get("Target") for rel in rels.findall("pr:Relationship", NS)}
        paths = []
        for slide_id in presentation.findall(".//p:sldIdLst/p:sldId", NS):
            rel_id = slide_id.attrib.get(f"{{{NS['r']}}}id")
            target = rel_by_id.get(rel_id or "")
            if target:
                paths.append("ppt/" + target.lstrip("/"))
        if paths:
            return paths
    except Exception:  # noqa: BLE001
        pass
    return sorted(
        [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")],
        key=lambda item: int(re.search(r"slide(\d+)\.xml$", item).group(1)),
    )


def _inspect_slide(archive: ZipFile, slide_path: str, slide_number: int) -> dict[str, Any]:
    root = ET.fromstring(archive.read(slide_path))
    shapes = _extract_shapes(root)
    shape_counts = Counter(shape["geom"] for shape in shapes if shape.get("geom"))
    texts = _extract_text_examples(root)
    colors = _extract_colors(root)
    scores = _score_categories(shapes, shape_counts, texts)
    category, confidence = max(scores.items(), key=lambda item: item[1])
    tags = _tags_from_shapes(shape_counts, shapes)
    bbox = _overall_bbox(shapes)
    return {
        "template_id": f"ppt_smartart_{slide_number:04d}",
        "slide_number": slide_number,
        "source_slide_path": slide_path,
        "category": category,
        "category_name": TEMPLATE_DEFINITIONS[category]["name"],
        "confidence": round(float(confidence), 3),
        "office_layout_id": TEMPLATE_DEFINITIONS[category]["office_layout_id"],
        "shape_count": len(shapes),
        "shape_counts": dict(shape_counts.most_common(12)),
        "text_count": len(texts),
        "text_examples": texts[:8],
        "color_samples": colors[:10],
        "bbox": bbox,
        "layout_metrics": _layout_metrics(shapes),
        "tags": tags,
        "prompt_hint": PROMPT_HINTS[category],
    }


def _extract_shapes(root: ET.Element) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for element in root.findall(".//p:sp", NS) + root.findall(".//p:cxnSp", NS):
        geom = ""
        geom_el = element.find(".//a:prstGeom", NS)
        if geom_el is not None:
            geom = geom_el.attrib.get("prst", "")
        off = element.find(".//a:xfrm/a:off", NS)
        ext = element.find(".//a:xfrm/a:ext", NS)
        x = _int_attr(off, "x")
        y = _int_attr(off, "y")
        cx = _int_attr(ext, "cx")
        cy = _int_attr(ext, "cy")
        text = "".join(t.text or "" for t in element.findall(".//a:t", NS)).strip()
        if geom or text or cx or cy:
            shapes.append({"geom": geom, "x": x, "y": y, "cx": cx, "cy": cy, "text": text})
    return shapes


def _extract_text_examples(root: ET.Element) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for text_el in root.findall(".//a:t", NS):
        text = (text_el.text or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:40])
    return result


def _extract_colors(root: ET.Element) -> list[str]:
    colors = []
    for color in root.findall(".//a:srgbClr", NS):
        value = color.attrib.get("val")
        if value and value not in colors:
            colors.append(f"#{value.upper()}")
    return colors


def _score_categories(shapes: list[dict[str, Any]], counts: Counter[str], texts: list[str]) -> dict[str, float]:
    total = max(1, len(shapes))
    metrics = _layout_metrics(shapes)
    arrow_count = sum(counts.get(name, 0) for name in ("rightArrow", "leftArrow", "upArrow", "downArrow", "chevron", "pentagon", "homePlate", "rightArrowCallout", "downArrowCallout"))
    connector_count = sum(count for geom, count in counts.items() if "Connector" in geom or geom == "line")
    ellipse_count = counts.get("ellipse", 0)
    rect_count = counts.get("rect", 0) + counts.get("roundRect", 0)
    hex_count = counts.get("hexagon", 0)
    diamond_count = counts.get("diamond", 0)
    text_blob = "".join(texts)

    scores = {category: 0.05 for category in TEMPLATE_DEFINITIONS}
    scores["arrow_process"] += 0.18 * arrow_count + (0.24 if metrics["orientation"] == "horizontal" and arrow_count >= 2 else 0)
    scores["vertical_card_list"] += 0.10 * rect_count + (0.28 if metrics["orientation"] == "vertical" and rect_count >= 4 else 0) + (0.1 if len(texts) >= 3 else 0)
    scores["timeline_milestone"] += 0.12 * connector_count + 0.14 * ellipse_count + (0.3 if metrics["orientation"] == "horizontal" and ellipse_count >= 3 and connector_count >= 1 else 0)
    scores["radial_relation"] += 0.16 * ellipse_count + 0.10 * connector_count + (0.22 if ellipse_count >= 4 and connector_count >= 4 else 0)
    scores["hexagon_matrix"] += 0.42 * hex_count + (0.16 if hex_count >= 3 else 0)
    scores["quadrant_matrix"] += 0.16 * rect_count + 0.10 * diamond_count + (0.45 if metrics["row_count"] == 2 and metrics["col_count"] == 2 and 3 <= rect_count <= 8 else 0) + (0.18 if "矩阵" in text_blob or "象限" in text_blob else 0)
    scores["step_hierarchy"] += 0.16 * counts.get("homePlate", 0) + 0.12 * counts.get("parallelogram", 0) + (0.26 if metrics["stair_like"] else 0)
    scores["circle_category"] += 0.16 * ellipse_count + 0.12 * rect_count + (0.28 if metrics["orientation"] == "vertical" and ellipse_count >= 2 and rect_count >= 2 else 0)
    scores["basic_hierarchy"] += 0.08 * connector_count + 0.08 * rect_count + (0.2 if metrics["row_count"] >= 2 and metrics["col_count"] >= 2 else 0)

    # Normalize roughly into 0-1 while keeping useful relative ordering.
    return {key: min(0.98, value / (1.0 + total * 0.035)) for key, value in scores.items()}


def _layout_metrics(shapes: list[dict[str, Any]]) -> dict[str, Any]:
    boxes = [shape for shape in shapes if shape.get("cx", 0) > 0 and shape.get("cy", 0) > 0]
    if not boxes:
        return {"orientation": "unknown", "row_count": 0, "col_count": 0, "stair_like": False}
    centers = [(shape["x"] + shape["cx"] / 2, shape["y"] + shape["cy"] / 2) for shape in boxes]
    xs = [item[0] for item in centers]
    ys = [item[1] for item in centers]
    x_span = max(xs) - min(xs) if len(xs) > 1 else 0
    y_span = max(ys) - min(ys) if len(ys) > 1 else 0
    orientation = "horizontal" if x_span > y_span * 1.25 else "vertical" if y_span > x_span * 1.25 else "balanced"
    row_count = _bucket_count(ys)
    col_count = _bucket_count(xs)
    sorted_by_y = sorted(centers, key=lambda item: item[1])
    stair_like = len(sorted_by_y) >= 4 and sum(1 for a, b in zip(sorted_by_y, sorted_by_y[1:]) if b[0] > a[0]) >= len(sorted_by_y) * 0.55
    return {"orientation": orientation, "row_count": row_count, "col_count": col_count, "stair_like": stair_like, "x_span": x_span, "y_span": y_span}


def _bucket_count(values: list[float]) -> int:
    if not values:
        return 0
    values = sorted(values)
    span = max(values) - min(values)
    tolerance = max(85000, span * 0.08)
    buckets = []
    for value in values:
        if not buckets or abs(value - buckets[-1]) > tolerance:
            buckets.append(value)
    return len(buckets)


def _overall_bbox(shapes: list[dict[str, Any]]) -> dict[str, int]:
    boxes = [shape for shape in shapes if shape.get("cx", 0) > 0 and shape.get("cy", 0) > 0]
    if not boxes:
        return {"x": 0, "y": 0, "cx": 0, "cy": 0}
    x0 = min(shape["x"] for shape in boxes)
    y0 = min(shape["y"] for shape in boxes)
    x1 = max(shape["x"] + shape["cx"] for shape in boxes)
    y1 = max(shape["y"] + shape["cy"] for shape in boxes)
    return {"x": x0, "y": y0, "cx": x1 - x0, "cy": y1 - y0}


def _tags_from_shapes(counts: Counter[str], shapes: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for geom in ("rightArrow", "chevron", "hexagon", "ellipse", "roundRect", "homePlate", "diamond", "line"):
        if counts.get(geom):
            tags.append(geom)
    metrics = _layout_metrics(shapes)
    tags.extend([f"orientation:{metrics['orientation']}", f"rows:{metrics['row_count']}", f"cols:{metrics['col_count']}"])
    if metrics.get("stair_like"):
        tags.append("stair_like")
    return tags


def _int_attr(element: ET.Element | None, key: str) -> int:
    if element is None:
        return 0
    try:
        return int(element.attrib.get(key) or 0)
    except ValueError:
        return 0


if __name__ == "__main__":
    main()
