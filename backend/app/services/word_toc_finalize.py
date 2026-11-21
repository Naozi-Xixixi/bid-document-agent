"""Use Word's paginator to save a DOCX with current TOC page numbers.

The browser can create chapter headings but cannot know where Word will place
them after pagination. This helper runs in a separate process so a stalled Word
instance does not block the API indefinitely.
"""

from __future__ import annotations

import subprocess
import sys
import os
import tempfile
import zipfile
from pathlib import Path

from lxml import etree


class TocFinalizationError(Exception):
    pass


def finalize_docx(source: Path, destination: Path, timeout: int = 90) -> None:
    if sys.platform != "win32":
        raise TocFinalizationError("当前服务器没有可用的 Word 分页引擎，无法写入准确的目录页码。")
    backend_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.services.word_toc_finalize", str(source), str(destination)],
            cwd=backend_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TocFinalizationError("Word 更新目录超时，请关闭占用该文件的 Word 窗口后重试。") from exc
    if result.returncode or not destination.is_file():
        detail = (result.stderr or result.stdout or "Word 未生成文件").strip().splitlines()[-1]
        raise TocFinalizationError(f"Word 更新目录失败：{detail}")


def _run_word(source: Path, destination: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source.resolve()), ReadOnly=False, ConfirmConversions=False, AddToRecentFiles=False
        )
        if document.TablesOfContents.Count == 0:
            raise TocFinalizationError("导出模板没有可更新的 Word 目录。")
        document.Repaginate()
        for index in range(1, document.TablesOfContents.Count + 1):
            toc = document.TablesOfContents(index)
            toc.Update()
            section = toc.Range.Sections(1).PageSetup
            right_edge = section.PageWidth - section.LeftMargin - section.RightMargin
            for paragraph in toc.Range.Paragraphs:
                if "\t" not in paragraph.Range.Text:
                    continue
                tabs = paragraph.Range.ParagraphFormat.TabStops
                tabs.ClearAll()
                tabs.Add(Position=right_edge, Alignment=2, Leader=1)  # Right aligned, dot leader.
        document.SaveAs2(str(destination.resolve()), FileFormat=16)
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    anchors = _freeze_export_layout(destination)
    _stabilize_toc_pages(destination, anchors)


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}


def _tag(name: str) -> str:
    return f"{{{_W}}}{name}"


def _plain_run(text: str, *, tab: bool = False) -> etree._Element:
    run = etree.Element(_tag("r"))
    props = etree.SubElement(run, _tag("rPr"))
    etree.SubElement(props, _tag("rFonts"), {
        _tag("ascii"): "SimSun", _tag("hAnsi"): "SimSun", _tag("eastAsia"): "宋体"
    })
    etree.SubElement(props, _tag("color"), {_tag("val"): "000000"})
    etree.SubElement(props, _tag("sz"), {_tag("val"): "24"})
    if tab:
        etree.SubElement(run, _tag("tab"))
    else:
        node = etree.SubElement(run, _tag("t"))
        node.text = text
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return run


def _toc_layout_table(rows: list[tuple[str, str, str]], width: int) -> etree._Element:
    """Lay out page numbers in their own borderless column for DOCX viewers."""
    page_column = max(420, max((len(page.strip()) * 120 + 60 for _, _, page in rows), default=0))
    title_column = width - page_column
    if title_column <= 0:
        raise TocFinalizationError("目录页面宽度无效。")
    table = etree.Element(_tag("tbl"))
    properties = etree.SubElement(table, _tag("tblPr"))
    etree.SubElement(properties, _tag("tblW"), {_tag("w"): str(width), _tag("type"): "dxa"})
    etree.SubElement(properties, _tag("tblInd"), {_tag("w"): "0", _tag("type"): "dxa"})
    borders = etree.SubElement(properties, _tag("tblBorders"))
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        etree.SubElement(borders, _tag(side), {_tag("val"): "nil"})
    etree.SubElement(properties, _tag("tblLayout"), {_tag("type"): "fixed"})
    margins = etree.SubElement(properties, _tag("tblCellMar"))
    for side in ("top", "left", "bottom", "right"):
        etree.SubElement(margins, _tag(side), {_tag("w"): "0", _tag("type"): "dxa"})
    grid = etree.SubElement(table, _tag("tblGrid"))
    for column_width in (title_column, page_column):
        etree.SubElement(grid, _tag("gridCol"), {_tag("w"): str(column_width)})
    for style, title, page in rows:
        level = int(style[-1]) if style[-1].isdigit() else 1
        indent = min(max(level - 1, 0), 2) * 420
        row = etree.SubElement(table, _tag("tr"))
        etree.SubElement(etree.SubElement(row, _tag("trPr")), _tag("cantSplit"))
        for text, column_width, alignment in (
            (title, title_column, "left"), (page, page_column, "right")
        ):
            cell = etree.SubElement(row, _tag("tc"))
            cell_properties = etree.SubElement(cell, _tag("tcPr"))
            etree.SubElement(cell_properties, _tag("tcW"), {
                _tag("w"): str(column_width), _tag("type"): "dxa"
            })
            paragraph = etree.SubElement(cell, _tag("p"))
            paragraph_properties = etree.SubElement(paragraph, _tag("pPr"))
            etree.SubElement(paragraph_properties, _tag("jc"), {_tag("val"): alignment})
            if alignment == "left":
                tabs = etree.SubElement(paragraph_properties, _tag("tabs"))
                etree.SubElement(tabs, _tag("tab"), {
                    _tag("val"): "right", _tag("leader"): "dot", _tag("pos"): str(title_column - 40)
                })
            etree.SubElement(paragraph_properties, _tag("ind"), {
                _tag("left"): str(indent if alignment == "left" else 0)
            })
            etree.SubElement(paragraph_properties, _tag("spacing"), {
                _tag("before"): "0", _tag("after"): "80"
            })
            paragraph.append(_plain_run(text))
            if alignment == "left":
                paragraph.append(_plain_run("", tab=True))
    etree.SubElement(properties, _tag("tblCaption"), {_tag("val"): "WordAgent TOC"})
    return table


def _freeze_export_layout(destination: Path) -> list[str]:
    """Keep Word-calculated pages, but remove viewer-dependent TOC links/fields.

    Some DOCX viewers ignore right-aligned tab stops even for plain paragraphs.
    A borderless two-column layout keeps the title and page number separate
    while looking like a normal directory rather than an editing status grid.
    Re-export after editing the document to recalculate page numbers.
    """
    with zipfile.ZipFile(destination) as source:
        document = etree.fromstring(source.read("word/document.xml"))
        styles = etree.fromstring(source.read("word/styles.xml"))
        title_style_ids = {
            style.get(_tag("styleId"))
            for style in styles.xpath("//w:style[w:name[@w:val='Title']]", namespaces=_NS)
        }
        anchors: list[str] = []
        toc_rows: list[tuple[str, str, str]] = []
        toc_paragraphs: list[etree._Element] = []
        toc_width = 0
        for paragraph in document.xpath("//w:p[w:pPr/w:pStyle]", namespaces=_NS):
            style = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=_NS)[0]
            if style in title_style_ids:
                for run in paragraph.xpath("./w:r[w:t]", namespaces=_NS):
                    properties = run.find(_tag("rPr"))
                    if properties is None:
                        properties = etree.Element(_tag("rPr"))
                        run.insert(0, properties)
                    for name in ("rFonts", "color", "sz", "szCs"):
                        for old in properties.findall(_tag(name)):
                            properties.remove(old)
                    etree.SubElement(properties, _tag("rFonts"), {
                        _tag("ascii"): "SimHei", _tag("hAnsi"): "SimHei", _tag("eastAsia"): "黑体"
                    })
                    etree.SubElement(properties, _tag("color"), {_tag("val"): "000000"})
                    etree.SubElement(properties, _tag("sz"), {_tag("val"): "48"})
                    etree.SubElement(properties, _tag("szCs"), {_tag("val"): "48"})
            if not style.startswith("TOC"):
                continue
            hyperlink = paragraph.find(_tag("hyperlink"))
            if hyperlink is None:
                continue
            before_tab: list[str] = []
            after_tab: list[str] = []
            seen_tab = False
            for node in hyperlink.iter():
                if node.tag == _tag("tab"):
                    seen_tab = True
                elif node.tag == _tag("t"):
                    (after_tab if seen_tab else before_tab).append(node.text or "")
            title = "".join(before_tab).strip()
            page = "".join(after_tab).strip()
            if not title or not page:
                raise TocFinalizationError("Word 更新后的目录缺少标题或页码。")
            if not toc_width:
                stops = paragraph.xpath("./w:pPr/w:tabs/w:tab/@w:pos", namespaces=_NS)
                toc_width = int(stops[0]) if stops else 0
            anchors.append(hyperlink.get(_tag("anchor")) or "")
            toc_rows.append((style, title, page))
            toc_paragraphs.append(paragraph)
        if not toc_rows:
            raise TocFinalizationError("Word 没有生成可用的目录页码。")
        first = toc_paragraphs[0]
        parent = first.getparent()
        parent.replace(first, _toc_layout_table(toc_rows, toc_width))
        for paragraph in toc_paragraphs[1:]:
            paragraph.getparent().remove(paragraph)
        updated_document = etree.tostring(document, encoding="UTF-8", xml_declaration=True)
        temporary = tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".docx", delete=False)
        temporary_path = Path(temporary.name)
        temporary.close()
        try:
            with zipfile.ZipFile(temporary_path, "w") as output:
                for member in source.infolist():
                    data = updated_document if member.filename == "word/document.xml" else source.read(member.filename)
                    output.writestr(member, data)
            source.close()  # Windows cannot replace a DOCX while its ZIP handle is open.
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    return anchors


def _read_bookmark_pages(destination: Path, anchors: list[str]) -> list[str]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        document = word.Documents.Open(str(destination.resolve()), ReadOnly=True, AddToRecentFiles=False)
        document.Repaginate()
        pages = []
        for anchor in anchors:
            if not anchor or not document.Bookmarks.Exists(anchor):
                raise TocFinalizationError("目录对应的章节书签不存在，无法核对最终页码。")
            position = document.Bookmarks(anchor).Range.Duplicate
            position.Collapse(1)  # Heading start, not the end of a wrapped heading.
            pages.append(str(position.Information(1)))
        return pages
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def _write_toc_pages(destination: Path, pages: list[str]) -> bool:
    with zipfile.ZipFile(destination) as source:
        document = etree.fromstring(source.read("word/document.xml"))
        cells = document.xpath(
            '//w:tbl[w:tblPr/w:tblCaption[@w:val="WordAgent TOC"]]/w:tr/w:tc[2]', namespaces=_NS
        )
        if len(cells) != len(pages):
            raise TocFinalizationError("目录行数与章节页码数量不一致。")
        changed = False
        for cell, page in zip(cells, pages):
            texts = cell.xpath(".//w:t", namespaces=_NS)
            if not texts:
                raise TocFinalizationError("目录页码单元格为空。")
            if "".join(t.text or "" for t in texts) != page:
                texts[0].text = page
                for text in texts[1:]:
                    text.text = ""
                changed = True
        if not changed:
            return False
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".docx", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            with zipfile.ZipFile(temporary_path, "w") as output:
                for member in source.infolist():
                    data = etree.tostring(document, encoding="UTF-8", xml_declaration=True) if member.filename == "word/document.xml" else source.read(member.filename)
                    output.writestr(member, data)
            source.close()
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    return True


def _stabilize_toc_pages(destination: Path, anchors: list[str]) -> None:
    # Freezing the directory and normalizing the cover can change pagination.
    # Recheck the final layout, including any page-number digit-width change.
    for _ in range(3):
        if not _write_toc_pages(destination, _read_bookmark_pages(destination, anchors)):
            return
    raise TocFinalizationError("目录页码未能在限定次数内稳定，请检查模板分页。")


if __name__ == "__main__":
    _run_word(Path(sys.argv[1]), Path(sys.argv[2]))
