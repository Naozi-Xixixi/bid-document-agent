from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.core.config import settings
from app.services import diagram_image_service
from app.services.officecli_docx_formatter import _officecli_executable, _run_officecli

logger = logging.getLogger(__name__)


OFFICE_SMARTART_LAYOUTS: dict[str, dict[str, str]] = {
    "smartart": {"office_category": "hierarchy", "office_layout": "horizontal_multilevel_list", "mermaid": "flowchart TB"},
    "smartart_layer": {"office_category": "hierarchy", "office_layout": "horizontal_multilevel_list", "mermaid": "flowchart TB"},
    "organization": {"office_category": "hierarchy", "office_layout": "organization_chart", "mermaid": "flowchart TB"},
    "cycle": {"office_category": "cycle", "office_layout": "basic_cycle", "mermaid": "flowchart LR"},
    "timeline": {"office_category": "process", "office_layout": "horizontal_timeline", "mermaid": "flowchart LR"},
    "matrix": {"office_category": "matrix", "office_layout": "basic_matrix", "mermaid": "flowchart LR"},
    "flow": {"office_category": "process", "office_layout": "basic_process", "mermaid": "flowchart TB"},
    "framework": {"office_category": "hierarchy", "office_layout": "horizontal_multilevel_list", "mermaid": "flowchart LR"},
    "architecture": {"office_category": "hierarchy", "office_layout": "layered_architecture", "mermaid": "flowchart TB"},
}

OFFICE_SMARTART_TEMPLATE_LAYOUTS: dict[str, dict[str, Any]] = {
    "basic_hierarchy": {"office_layout_id": "HorizontalMultilevelList", "keywords": ("horizontal", "multi", "hierarchy", "list"), "mermaid": "flowchart TB"},
    "arrow_process": {"office_layout_id": "ContinuousArrowProcess", "keywords": ("continuous", "arrow", "process"), "mermaid": "flowchart LR"},
    "vertical_card_list": {"office_layout_id": "VerticalBlockList", "keywords": ("vertical", "block", "list"), "mermaid": "flowchart TB"},
    "timeline_milestone": {"office_layout_id": "MilestoneTimeline", "keywords": ("milestone", "timeline", "process"), "mermaid": "flowchart LR"},
    "radial_relation": {"office_layout_id": "RadialCycle", "keywords": ("radial", "cycle", "relationship"), "mermaid": "flowchart LR"},
    "hexagon_matrix": {"office_layout_id": "HexagonCluster", "keywords": ("hexagon", "cluster"), "mermaid": "flowchart LR"},
    "quadrant_matrix": {"office_layout_id": "GridMatrix", "keywords": ("grid", "matrix"), "mermaid": "flowchart LR"},
    "step_hierarchy": {"office_layout_id": "TableHierarchy", "keywords": ("table", "hierarchy"), "mermaid": "flowchart TB"},
    "circle_category": {"office_layout_id": "CircleArrowProcess", "keywords": ("circle", "arrow", "process"), "mermaid": "flowchart TB"},
}

FONT_MAP: dict[str, str] = {
    "default_heiti": "黑体",
    "source_han_sans": "思源黑体",
    "microsoft_yahei": "微软雅黑",
    "simsun": "宋体",
    "fangsong": "仿宋",
}

STYLE_MAP: dict[str, dict[str, Any]] = {
    "tech": {"accent": "3F6F82", "fills": ["EAF3F8", "E9F5EF", "EEF0FA", "F5EFE3"], "line": "3F6F82"},
    "mono": {"accent": "2B2B26", "fills": ["F7F7F5", "EFEDEA", "FFFFFF", "E5E2DC"], "line": "55534D"},
    "business": {"accent": "2F5F87", "fills": ["EAF2FB", "F1F6FB", "E8EEF7", "F7F4EA"], "line": "2F5F87"},
    "office": {"accent": "6B7280", "fills": ["F6F7F7", "EEF4F2", "F7F3EA", "F3F5F8"], "line": "6B7280"},
    "cartoon": {"accent": "6B4E71", "fills": ["FFE8EC", "E4F6F1", "FFF0C7", "E9ECFF", "EAF7D8"], "line": "6B4E71"},
}


def check_office_env() -> dict[str, Any]:
    """Check whether the existing OfficeCLI integration can run."""

    officecli = _officecli_executable()
    if not settings.enable_officecli_docx_formatting:
        return {"available": False, "officecli": None, "reason": "OfficeCLI formatting is disabled"}
    if not officecli:
        return {"available": False, "officecli": None, "reason": "OfficeCLI binary not found"}
    version = _run_officecli([officecli, "--version"], required=False)
    return {
        "available": version.returncode == 0,
        "officecli": officecli,
        "version": (version.stdout or version.stderr or "").strip(),
        "reason": "" if version.returncode == 0 else (version.stderr or version.stdout or "OfficeCLI version check failed").strip(),
    }


async def build_smartart_export(
    paragraph: str,
    *,
    diagram_type: str = "smartart",
    template_id: str = "basic_hierarchy",
    style_hint: str = "商务科技风",
    size: str = "wide",
    font_family: str = "default_heiti",
    style_preset: str = "tech",
    auto_match: bool = False,
    auto_mode: bool = True,
) -> tuple[bytes, str, dict[str, Any]]:
    """Reuse the diagram LLM/layout chain, then export a Word document."""

    diagram = await diagram_image_service.generate_diagram_image(
        paragraph,
        diagram_type=diagram_type,
        template_id=template_id,
        style_hint=style_hint,
        size=size,
        font_family=font_family,
        style_preset=style_preset,
        auto_match=auto_match,
        auto_mode=auto_mode,
    )
    if isinstance(diagram.get("smartart_structure"), dict):
        struct_data = convert_structure_to_office_smartart(
            diagram["smartart_structure"],
            diagram_type=str(diagram.get("diagram_type") or diagram_type),
            template_id=str(diagram.get("template_id") or template_id),
            title=str(diagram.get("title") or "标书逻辑图"),
        )
    else:
        struct_data = convert_elements_to_office_smartart(
            diagram.get("elements") or [],
            diagram_type=str(diagram.get("diagram_type") or diagram_type),
            template_id=str(diagram.get("template_id") or template_id),
            title=str(diagram.get("title") or "标书逻辑图"),
        )
    style_config = {
        "font_family": font_family,
        "style_preset": style_preset,
        "style_hint": style_hint,
        "size": size,
        "template_id": struct_data.get("template_id") or template_id,
    }
    return generate_smartart_word(struct_data, style_config)


def convert_elements_to_office_smartart(elements: list[dict[str, Any]], *, diagram_type: str, template_id: str = "basic_hierarchy", title: str) -> dict[str, Any]:
    """Convert service-level SVG elements into OfficeCLI diagram node data."""

    nodes = [element for element in elements if element.get("type") in {"rectangle", "ellipse", "diamond"}]
    nodes = sorted(nodes, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))[:12]
    rows: list[dict[str, Any]] = []
    for node in nodes:
        y = float(node.get("y") or 0)
        label = _node_label(node)
        if not label:
            continue
        target_row = next((row for row in rows if abs(row["y"] - y) < 12), None)
        if not target_row:
            target_row = {"y": y, "nodes": []}
            rows.append(target_row)
        if len(target_row["nodes"]) < 5:
            items = node.get("items") if isinstance(node.get("items"), list) else []
            target_row["nodes"].append({"id": str(node.get("id") or f"node{len(rows)}"), "text": _short_label(label), "items": [str(item) for item in items[:6]]})

    if not rows:
        rows = [{"y": 0, "nodes": [{"id": "node1", "text": "核心逻辑"}]}]

    normalized_type = diagram_image_service._normalize_diagram_type(diagram_type) or "smartart"
    normalized_template = diagram_image_service._normalize_smartart_template(template_id) or "basic_hierarchy"
    return {
        "title": title or "标书逻辑图",
        "diagram_type": normalized_type,
        "template_id": normalized_template,
        "office_layout_id": OFFICE_SMARTART_TEMPLATE_LAYOUTS.get(normalized_template, OFFICE_SMARTART_TEMPLATE_LAYOUTS["basic_hierarchy"])["office_layout_id"],
        "layout": OFFICE_SMARTART_LAYOUTS.get(normalized_type, OFFICE_SMARTART_LAYOUTS["smartart"]),
        "layers": [{"level": index + 1, "nodes": row["nodes"]} for index, row in enumerate(rows[:6])],
    }


def convert_structure_to_office_smartart(structure: dict[str, Any], *, diagram_type: str, template_id: str = "basic_hierarchy", title: str) -> dict[str, Any]:
    normalized_type = diagram_image_service._normalize_diagram_type(diagram_type) or "smartart"
    normalized_template = diagram_image_service._normalize_smartart_template(template_id) or "basic_hierarchy"
    modules = structure.get("modules") if isinstance(structure.get("modules"), list) else []
    clean_modules: list[dict[str, Any]] = []
    for index, module in enumerate(modules[:8], 1):
        if not isinstance(module, dict):
            continue
        name = str(module.get("name") or "").strip()[:15]
        if not name:
            continue
        raw_items = module.get("items") if isinstance(module.get("items"), list) else []
        items = [str(item).strip()[:15] for item in raw_items if str(item).strip()][:8]
        clean_modules.append({"id": f"module{index}", "text": name, "items": items})
    if not clean_modules:
        clean_modules = [{"id": "module1", "text": "核心逻辑", "items": []}]
    return {
        "title": str(structure.get("title") or title or "标书逻辑图")[:36],
        "diagram_type": normalized_type,
        "template_id": normalized_template,
        "office_layout_id": OFFICE_SMARTART_TEMPLATE_LAYOUTS.get(normalized_template, OFFICE_SMARTART_TEMPLATE_LAYOUTS["basic_hierarchy"])["office_layout_id"],
        "modules": clean_modules,
        "layout": OFFICE_SMARTART_LAYOUTS.get(normalized_type, OFFICE_SMARTART_LAYOUTS["smartart"]),
        "layers": [{"level": 1, "nodes": clean_modules[:5]}],
    }


def generate_smartart_word(struct_data: dict[str, Any], style_config: dict[str, Any]) -> tuple[bytes, str, dict[str, Any]]:
    """Generate a DOCX with multi-level fallback.

    Priority:
    1) Native Word SmartArt through OfficeCLI-created DOCX + local Word COM.
    2) Existing OfficeCLI Mermaid diagram path.
    3) python-docx editable table fallback.
    """

    env = check_office_env()
    if env["available"]:
        try:
            output = generate_native_smartart(struct_data, style_config, env["officecli"])
            return output, _safe_filename(struct_data.get("title"), suffix="_原生SmartArt.docx"), {
                "generation_method": "native_smartart",
                "officecli_used": True,
                "word_com_used": True,
                "fallback_used": False,
                "template_id": struct_data.get("template_id") or style_config.get("template_id"),
                "office_layout_id": struct_data.get("office_layout_id"),
                **env,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Native SmartArt export failed; trying OfficeCLI diagram fallback: %s", exc)
            native_error = str(exc)
        else:
            native_error = ""

        try:
            output = _generate_with_officecli(struct_data, style_config, env["officecli"])
            return output, _safe_filename(struct_data.get("title"), suffix="_OfficeDiagram.docx"), {
                "generation_method": "officecli_diagram",
                "officecli_used": True,
                "word_com_used": False,
                "fallback_used": True,
                "template_id": struct_data.get("template_id") or style_config.get("template_id"),
                "office_layout_id": struct_data.get("office_layout_id"),
                "native_error": native_error,
                **env,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("OfficeCLI diagram fallback failed; falling back to editable DOCX: %s", exc)
            env = {**env, "available": False, "reason": f"Native SmartArt failed: {native_error}; OfficeCLI diagram failed: {exc}"}

    output = _generate_editable_docx_fallback(struct_data, style_config)
    return output, _safe_filename(struct_data.get("title"), suffix="_SmartArt_可编辑.docx"), {
        "generation_method": "python_docx_table",
        "officecli_used": False,
        "word_com_used": False,
        "fallback_used": True,
        "template_id": struct_data.get("template_id") or style_config.get("template_id"),
        "office_layout_id": struct_data.get("office_layout_id"),
        **env,
    }


def clean_temp_files() -> None:
    """Temporary directories are context-managed; kept for API compatibility."""

    return None


def generate_native_smartart(struct_data: dict[str, Any], style_config: dict[str, Any], officecli: str | None = None) -> bytes:
    """Create a real Word SmartArt object when local Word COM is available.

    OfficeCLI 1.0.144 exposes docx `diagram` but not a schema-level `smartart`
    element.  To produce a true Office SmartArt object, this function uses
    OfficeCLI for document lifecycle/validation and Word COM for the native
    SmartArt insertion/fill operation.
    """

    officecli = officecli or _officecli_executable()
    if not officecli:
        raise RuntimeError("OfficeCLI binary not found")

    with tempfile.TemporaryDirectory(prefix="wordagent-native-smartart-") as temp_dir:
        docx_path = Path(temp_dir) / "native-smartart.docx"
        _run_officecli([officecli, "create", str(docx_path), "--force", "--locale", "zh-CN"], required=True)
        _run_officecli([officecli, "add", str(docx_path), "/body", "--type", "paragraph", "--prop", f"text={struct_data.get('title') or '标书逻辑图'}", "--prop", "style=Heading1"], required=False)
        _run_officecli([officecli, "close", str(docx_path)], required=False)

        _insert_native_smartart_with_word(docx_path, struct_data, style_config)

        _run_officecli([officecli, "open", str(docx_path)], required=False)
        _run_officecli([officecli, "validate", str(docx_path)], required=False)
        _run_officecli([officecli, "close", str(docx_path)], required=False)
        return docx_path.read_bytes()


def _insert_native_smartart_with_word(docx_path: Path, struct_data: dict[str, Any], style_config: dict[str, Any]) -> None:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Word COM is unavailable: {exc}") from exc

    pythoncom.CoInitialize()
    word = None
    doc = None
    should_quit_word = True
    try:
        word, should_quit_word = _open_word_application(win32com.client)
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(docx_path.resolve()))

        layout = _select_word_smartart_layout(
            word,
            str(struct_data.get("diagram_type") or "smartart"),
            str(struct_data.get("template_id") or style_config.get("template_id") or "basic_hierarchy"),
        )
        width_pt, height_pt = _smartart_box_points(str(style_config.get("size") or "wide"))
        # 72 pt = 1 inch. Position below the title, within normal page margins.
        shape = doc.Shapes.AddSmartArt(layout, 54, 118, width_pt, height_pt)
        _fill_native_smartart_nodes(shape.SmartArt, struct_data, style_config)
        _apply_native_smartart_style(word, shape, style_config)

        doc.Save()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Native Word SmartArt generation failed: {exc}") from exc
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:  # noqa: BLE001
                pass
        if word is not None:
            try:
                if should_quit_word:
                    word.Quit()
            except Exception:  # noqa: BLE001
                pass
        pythoncom.CoUninitialize()


def _open_word_application(win32com_client: Any) -> tuple[Any, bool]:
    """Open Word for COM automation.

    `DispatchEx` is preferred because it isolates this export from the user's
    open Word windows. Some Windows sessions reject creating a new Office COM
    server, so we then try to reuse an already-open Word instance. The boolean
    indicates whether this function owns the Word process and should quit it.
    """

    try:
        return win32com_client.DispatchEx("Word.Application"), True
    except Exception as first_exc:  # noqa: BLE001
        try:
            return win32com_client.GetActiveObject("Word.Application"), False
        except Exception as second_exc:  # noqa: BLE001
            raise RuntimeError(f"Unable to start or attach Word COM: {first_exc}; {second_exc}") from second_exc


def _select_word_smartart_layout(word: Any, diagram_type: str, template_id: str = "basic_hierarchy") -> Any:
    diagram_type = diagram_image_service._normalize_diagram_type(diagram_type) or "smartart"
    template_id = diagram_image_service._normalize_smartart_template(template_id) or "basic_hierarchy"
    keywords_by_type = {
        "smartart": ("horizontal", "multi", "hierarchy", "list"),
        "smartart_layer": ("horizontal", "multi", "hierarchy", "list"),
        "organization": ("organization", "org", "hierarchy"),
        "cycle": ("basic", "cycle"),
        "timeline": ("timeline", "process"),
        "matrix": ("basic", "matrix"),
        "flow": ("basic", "process"),
        "framework": ("hierarchy", "list"),
        "architecture": ("hierarchy", "layer"),
    }
    template_layout = OFFICE_SMARTART_TEMPLATE_LAYOUTS.get(template_id)
    keywords = tuple(template_layout["keywords"]) if diagram_type == "smartart" and template_layout else keywords_by_type.get(diagram_type, keywords_by_type["smartart"])
    layouts = word.SmartArtLayouts
    best = None
    best_score = -1
    for index in range(1, int(layouts.Count) + 1):
        try:
            layout = layouts.Item(index)
            haystack = f"{getattr(layout, 'Name', '')} {getattr(layout, 'Id', '')} {getattr(layout, 'Category', '')}".lower()
            score = sum(1 for keyword in keywords if keyword in haystack)
            if score > best_score:
                best = layout
                best_score = score
        except Exception:  # noqa: BLE001
            continue
    if best is None:
        raise RuntimeError("No SmartArt layout is available in Word")
    return best


def _fill_native_smartart_nodes(smartart: Any, struct_data: dict[str, Any], style_config: dict[str, Any]) -> None:
    rows = _flatten_layers_for_word_smartart(struct_data)
    if not rows:
        rows = [(1, "核心逻辑")]

    nodes = _smartart_nodes(smartart)
    if int(nodes.Count) < 1:
        raise RuntimeError("SmartArt created without editable nodes")

    current = nodes.Item(1)
    _set_smartart_node(current, rows[0][1], rows[0][0], style_config)
    previous = current
    for level, text in rows[1:12]:
        node = _add_smartart_node_after(previous)
        _set_smartart_node(node, text, level, style_config)
        previous = node

    # Remove unused placeholder nodes when the selected layout starts with several bullets.
    try:
        while int(nodes.Count) > len(rows):
            nodes.Item(int(nodes.Count)).Delete()
    except Exception:  # noqa: BLE001
        pass


def _smartart_nodes(smartart: Any) -> Any:
    for attr in ("AllNodes", "Nodes"):
        try:
            nodes = getattr(smartart, attr)
            if int(nodes.Count) >= 1:
                return nodes
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Word SmartArt nodes collection is unavailable")


def _add_smartart_node_after(previous_node: Any) -> Any:
    # MsoSmartArtNodePosition: After=3, Below=1. Different Word builds are
    # slightly picky, so try the explicit after insertion first, then below.
    for args in ((3, 1), (1, 1), (3,), tuple()):
        try:
            return previous_node.AddNode(*args)
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Unable to add SmartArt node")


def _set_smartart_node(node: Any, text: str, level: int, style_config: dict[str, Any]) -> None:
    target_level = max(1, min(6, int(level or 1)))
    try:
        # Promote/Demote makes the SmartArt text pane hierarchy reflect layers.
        for _ in range(6):
            current_level = int(getattr(node, "Level", target_level))
            if current_level == target_level:
                break
            if current_level < target_level:
                node.Demote()
            else:
                node.Promote()
    except Exception:  # noqa: BLE001
        pass

    node.TextFrame2.TextRange.Text = _short_label(text)
    font_name = _font_name(style_config)
    try:
        node.TextFrame2.TextRange.Font.Name = font_name
        node.TextFrame2.TextRange.Font.NameFarEast = font_name
        node.TextFrame2.TextRange.Font.Size = 11
    except Exception:  # noqa: BLE001
        pass


def _apply_native_smartart_style(word: Any, shape: Any, style_config: dict[str, Any]) -> None:
    style_preset = str(style_config.get("style_preset") or "tech")
    smartart = shape.SmartArt
    style_keywords = {
        "tech": ("polished", "moderate", "intense"),
        "mono": ("simple", "subtle"),
        "business": ("polished", "moderate"),
        "office": ("simple", "subtle"),
        "cartoon": ("cartoon", "polished", "moderate"),
    }.get(style_preset, ("polished", "moderate"))
    color_keywords = {
        "tech": ("accent", "colorful", "blue", "green"),
        "mono": ("black", "white", "gray", "monochrome"),
        "business": ("blue", "accent"),
        "office": ("gray", "subtle"),
        "cartoon": ("colorful", "accent"),
    }.get(style_preset, ("accent",))
    _apply_first_matching_collection_item(getattr(word, "SmartArtQuickStyles", None), smartart, "QuickStyle", style_keywords)
    _apply_first_matching_collection_item(getattr(word, "SmartArtColors", None), smartart, "Color", color_keywords)


def _apply_first_matching_collection_item(collection: Any, target: Any, attr_name: str, keywords: tuple[str, ...]) -> None:
    if collection is None:
        return
    best = None
    best_score = -1
    try:
        count = int(collection.Count)
    except Exception:  # noqa: BLE001
        return
    for index in range(1, count + 1):
        try:
            item = collection.Item(index)
            haystack = f"{getattr(item, 'Name', '')} {getattr(item, 'Id', '')} {getattr(item, 'Category', '')}".lower()
            score = sum(1 for keyword in keywords if keyword in haystack)
            if score > best_score:
                best = item
                best_score = score
        except Exception:  # noqa: BLE001
            continue
    if best is not None:
        try:
            setattr(target, attr_name, best)
        except Exception:  # noqa: BLE001
            pass


def _flatten_layers_for_word_smartart(struct_data: dict[str, Any]) -> list[tuple[int, str]]:
    modules = struct_data.get("modules") if isinstance(struct_data.get("modules"), list) else []
    if modules:
        rows: list[tuple[int, str]] = []
        for module in modules[:8]:
            if not isinstance(module, dict):
                continue
            name = str(module.get("text") or module.get("name") or "").strip()
            if not name:
                continue
            rows.append((1, _short_label(name)))
            raw_items = module.get("items") if isinstance(module.get("items"), list) else []
            for item in raw_items[:4]:
                item_text = str(item or "").strip()
                if item_text:
                    rows.append((2, _short_label(item_text)))
            if len(rows) >= 12:
                return rows[:12]
        return rows

    layers = struct_data.get("layers") if isinstance(struct_data.get("layers"), list) else []
    rows: list[tuple[int, str]] = []
    for layer_index, layer in enumerate(layers[:6], 1):
        nodes = layer.get("nodes") or []
        for node in nodes[:5]:
            rows.append((layer_index, _short_label(str(node.get("text") or ""))))
            if len(rows) >= 12:
                return rows
    return rows


def _smartart_box_points(size: str) -> tuple[float, float]:
    sizes = {
        "wide": (6.9 * 72, 3.9 * 72),
        "classic43": (6.4 * 72, 4.8 * 72),
        "a4": (5.8 * 72, 7.8 * 72),
        "ultrawide": (7.4 * 72, 3.2 * 72),
        "square": (5.8 * 72, 5.8 * 72),
        "portrait": (5.4 * 72, 7.2 * 72),
    }
    return sizes.get(size, sizes["wide"])


def _officecli_box_size(size: str) -> tuple[str, str]:
    sizes = {
        "wide": ("16cm", "9cm"),
        "classic43": ("15cm", "11.25cm"),
        "a4": ("13cm", "18cm"),
        "ultrawide": ("18cm", "7.7cm"),
        "square": ("13cm", "13cm"),
        "portrait": ("12cm", "16cm"),
    }
    return sizes.get(size, sizes["wide"])


def _generate_with_officecli(struct_data: dict[str, Any], style_config: dict[str, Any], officecli: str) -> bytes:
    mermaid = _to_mermaid(struct_data)
    width, height = _officecli_box_size(str(style_config.get("size") or "wide"))
    with tempfile.TemporaryDirectory(prefix="wordagent-smartart-officecli-") as temp_dir:
        docx_path = Path(temp_dir) / "smartart.docx"
        create = _run_officecli([officecli, "create", str(docx_path)], required=True)
        if create.returncode != 0:
            raise RuntimeError("OfficeCLI create failed")
        _run_officecli([officecli, "add", str(docx_path), "/body", "--type", "paragraph", "--prop", f"text={struct_data.get('title') or '标书逻辑图'}", "--prop", "style=Heading1"], required=False)

        # OfficeCLI docs list DOCX diagram as add-only Mermaid → native shapes or rendered image.
        attempts = (
            [
                officecli,
                "add",
                str(docx_path),
                "/body",
                "--type",
                "diagram",
                "--prop",
                f"mermaid={mermaid}",
                "--prop",
                "render=native",
                "--prop",
                f"width={width}",
                "--prop",
                f"height={height}",
            ],
            [
                officecli,
                "add",
                str(docx_path),
                "/body",
                "--type",
                "diagram",
                "--prop",
                f"text={mermaid}",
                "--prop",
                "render=native",
            ],
            [officecli, "add", str(docx_path), "/body", "--type", "flowchart", "--prop", f"text={mermaid}"],
        )
        last_message = ""
        for args in attempts:
            result = _run_officecli(args, required=False)
            if result.returncode == 0:
                _run_officecli([officecli, "set", str(docx_path), "/", "--prop", f"docDefaults.font={_font_name(style_config)}", "--prop", "docDefaults.fontSize=11pt"], required=False)
                _run_officecli([officecli, "validate", str(docx_path)], required=False)
                _run_officecli([officecli, "close", str(docx_path)], required=False)
                return docx_path.read_bytes()
            last_message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(last_message or "OfficeCLI diagram command failed")


def _generate_editable_docx_fallback(struct_data: dict[str, Any], style_config: dict[str, Any]) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)

    font_name = _font_name(style_config)
    palette = STYLE_MAP.get(str(style_config.get("style_preset") or "tech"), STYLE_MAP["tech"])
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run(str(struct_data.get("title") or "标书逻辑图"))
    title_run.bold = True
    title_run.font.name = font_name
    title_run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
    title_run.font.size = Pt(18)
    title_run.font.color.rgb = _rgb(palette["accent"])

    doc.add_paragraph("OfficeCLI 未可用时自动生成的可编辑 Word 层级图。安装 OfficeCLI 后将优先尝试生成 Office diagram。")

    layers = struct_data.get("layers") if isinstance(struct_data.get("layers"), list) else []
    max_cols = max((len(layer.get("nodes") or []) for layer in layers), default=1)
    table = doc.add_table(rows=max(1, len(layers) * 2 - 1), cols=max_cols)
    table.autofit = True
    table.style = "Table Grid"

    for layer_index, layer in enumerate(layers):
        row_index = layer_index * 2
        nodes = layer.get("nodes") or []
        for col in range(max_cols):
            cell = table.cell(row_index, col)
            _set_cell_shading(cell, palette["fills"][layer_index % len(palette["fills"])])
            _set_cell_border(cell, palette["line"])
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if col < len(nodes):
                run = paragraph.add_run(_short_label(str(nodes[col].get("text") or "")))
                run.bold = True
                run.font.name = font_name
                run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
                run.font.size = Pt(11)
                run.font.color.rgb = _rgb("2B2B26")
            else:
                paragraph.add_run(" ")
        if layer_index < len(layers) - 1:
            connector_row = table.rows[row_index + 1]
            for cell in connector_row.cells:
                paragraph = cell.paragraphs[0]
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = paragraph.add_run("↓")
                run.bold = True
                run.font.size = Pt(14)
                run.font.color.rgb = _rgb(palette["line"])

    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def _to_mermaid(struct_data: dict[str, Any]) -> str:
    diagram_type = str(struct_data.get("diagram_type") or "smartart")
    template_id = diagram_image_service._normalize_smartart_template(str(struct_data.get("template_id") or "")) or "basic_hierarchy"
    if diagram_type == "smartart":
        direction = str(OFFICE_SMARTART_TEMPLATE_LAYOUTS.get(template_id, OFFICE_SMARTART_TEMPLATE_LAYOUTS["basic_hierarchy"])["mermaid"])
    else:
        direction = OFFICE_SMARTART_LAYOUTS.get(diagram_type, OFFICE_SMARTART_LAYOUTS["smartart"])["mermaid"]
    lines = [direction]
    modules = struct_data.get("modules") if isinstance(struct_data.get("modules"), list) else []
    if modules:
        previous_module_id = ""
        for module_index, module in enumerate(modules[:8], 1):
            module_id = f"M{module_index}"
            label = _short_label(str(module.get("text") or module.get("name") or f"模块{module_index}"))
            lines.append(f'    {module_id}["{label}"]')
            if previous_module_id and template_id in {"arrow_process", "timeline_milestone", "step_hierarchy", "circle_category"}:
                lines.append(f"    {previous_module_id} --> {module_id}")
            raw_items = module.get("items") if isinstance(module.get("items"), list) else []
            for item_index, item in enumerate(raw_items[:4], 1):
                item_id = f"M{module_index}I{item_index}"
                item_label = _short_label(str(item or f"子项{item_index}"))
                lines.append(f'    {item_id}["{item_label}"]')
                lines.append(f"    {module_id} --> {item_id}")
            previous_module_id = module_id
        return "\n".join(lines)

    layers = struct_data.get("layers") if isinstance(struct_data.get("layers"), list) else []
    previous_ids: list[str] = []
    for layer_index, layer in enumerate(layers):
        current_ids: list[str] = []
        for node_index, node in enumerate(layer.get("nodes") or []):
            node_id = f"L{layer_index + 1}N{node_index + 1}"
            label = _short_label(str(node.get("text") or f"节点{node_index + 1}"))
            lines.append(f'    {node_id}["{label}"]')
            current_ids.append(node_id)
        if previous_ids and current_ids:
            for index, node_id in enumerate(current_ids):
                parent_id = previous_ids[min(index, len(previous_ids) - 1)]
                lines.append(f"    {parent_id} --> {node_id}")
        previous_ids = current_ids
    return "\n".join(lines)


def _node_label(node: dict[str, Any]) -> str:
    if node.get("moduleName"):
        return str(node.get("moduleName") or "").strip()
    label = node.get("label") if isinstance(node.get("label"), dict) else {}
    return str(label.get("text") or node.get("text") or "").strip()


def _short_label(value: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned.strip("：:，,。；;、/／|（）()[]【】《》<>-—_ ")
    return cleaned[:15] or "核心节点"


def _font_name(style_config: dict[str, Any]) -> str:
    return FONT_MAP.get(str(style_config.get("font_family") or "default_heiti"), "黑体")


def _safe_filename(value: Any, *, suffix: str) -> str:
    stem = "".join(ch for ch in str(value or "标书逻辑图") if ch not in '\\/:*?"<>|').strip()[:36] or "标书逻辑图"
    return f"{stem}{suffix}"


def _rgb(hex_color: str) -> RGBColor:
    color = str(hex_color or "3A3A32").strip().lstrip("#")[:6]
    return RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _set_cell_shading(cell: Any, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), str(fill).lstrip("#"))
    tc_pr.append(shd)


def _set_cell_border(cell: Any, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "10")
        element.set(qn("w:color"), str(color).lstrip("#"))
