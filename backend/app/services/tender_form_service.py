"""Extract editable source forms; never ask an LLM to redesign a tender table."""
from __future__ import annotations
import base64
import json
import re
from pathlib import Path
from functools import lru_cache
from threading import RLock

_FORM_LOCK = RLock()
_FIXED_LETTERS = {
    '投标函', '投标函正文', '投标授权书', '投标人信用承诺', '生产厂商授权书',
    '投标保证金退还声明', '磋商响应函', '响应函', '授权书',
}
_FORMAT_CHAPTER = re.compile(
    r'^[ \t]*第\s*[一二三四五六七八九十百\d]+\s*章\s*(?:投标|响应|磋商)文件格式[ \t]*$',
    re.M,
)
_CHAPTER_HEADING = re.compile(r'^[ \t]*第\s*[一二三四五六七八九十百\d]+\s*章[^\n]+', re.M)
_LIST_HEADING = re.compile(r'(?:投标|响应|磋商)文件资料清单')
_NUMBERED_HEADING = re.compile(r'^([一二三四五六七八九十百]+|\d{1,2})[.．、]\s*(.{1,65})$')
_CONDITIONAL_FORM = re.compile(r'最后(?:承诺)?报价表|残疾人福利性单位声明函|本项目不采用|本项目不适用|(?:如有|如适用)[）)]?$')


def required_format_sections(raw_text: str) -> list[str]:
    """Read the actual format chapter or its explicit list, in source order."""
    for heading in reversed(list(_FORMAT_CHAPTER.finditer(raw_text or ''))):
        following = raw_text[heading.end():]
        end = _CHAPTER_HEADING.search(following)
        block = following[:end.start()] if end else following
        checklist = _LIST_HEADING.search(block[:600])
        if checklist:
            block = block[checklist.end():checklist.end() + 3500].split('---', 1)[0]
            block = re.split(r'第\s*\d+\s*页\s*共\s*\d+\s*页', block, maxsplit=1)[0]
        sections = []
        pending = False
        last_number = 0
        number_style = None
        for line in block.splitlines():
            line = line.strip()
            if checklist:
                if line.startswith('备注'):
                    break
                match = re.match(r'^([一二三四五六七八九十]{1,3}|\d{1,2})[.．、]?\s*(.*?)$', line)
                if pending and line:
                    title = line
                    pending = False
                elif match:
                    title = match.group(2).strip()
                    pending = not title
                else:
                    continue
            else:
                match = _NUMBERED_HEADING.fullmatch(line)
                if not match:
                    continue
                index, title = match.groups()
                style = 'arabic' if index.isdigit() else 'chinese'
                number = int(index) if index.isdigit() else _chinese_ordinal(index)
                if number != last_number + 1 or (number_style and number_style != style) or re.search(r'[。；！？]', title):
                    continue
                number_style = style
                last_number = number
            if title in {'报价表格式', '开标一览表格式'}:
                title = title.removesuffix('格式')
            if title and title not in sections and not _CONDITIONAL_FORM.search(title):
                sections.append(title)
        if sections and (checklist or last_number >= 2):
            return sections
    return []


def _chinese_ordinal(value: str) -> int:
    digits = {digit: i for i, digit in enumerate('零一二三四五六七八九')}
    if '十' in value:
        tens, _, units = value.partition('十')
        return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return digits.get(value, 0)


def procurement_method(raw_text: str) -> str:
    methods = r'竞争性磋商|竞争性谈判|公开招标|邀请招标|单一来源|询价采购'
    head = (raw_text or '')[:5000]
    explicit = re.search(r'(?:采购方式|招标方式)\s*[：:]\s*(' + methods + ')', head)
    if explicit:
        return explicit[1]
    document_type = re.search(r'(' + methods + r')\s*(?:采购)?文件', head)
    if document_type:
        return document_type[1]
    if re.search(r'第一章\s*磋商邀请', head):
        return '竞争性磋商'
    return ''


@lru_cache(maxsize=24)
def price_score_conflict(raw_text: str) -> str:
    text = re.sub(r'\s+', '', raw_text or '')
    number = r'(\d{1,3}(?:\.\d+)?)'
    weights = re.findall(r'价格(?:分值|分)?(?:占总分值的)?权重(?:为|[:：])?' + number + r'[%％]', text)
    formulas = re.findall(r'(?:价格|磋商报价|投标报价|报价)得分[=＝][^。；]{0,120}?[×xX*]' + number + r'[%％]', text)
    scores = re.findall(r'价格分[（(]' + number + r'分[）)]', text)
    total_is_100 = bool(re.search(r'(?:综合评分|评审总分|总分)(?:满分|为|[:：]){0,2}100分', text))
    weights_set = {float(value) for value in weights}
    formula_set = {float(value) for value in formulas}
    comparable = weights_set | formula_set | ({float(value) for value in scores} if total_is_100 else set())
    if len(comparable) < 2:
        return ''
    evidence = []
    if weights_set:
        evidence.append('权重说明 ' + '、'.join(f'{value:g}%' for value in sorted(weights_set)))
    if scores and total_is_100:
        evidence.append('评分表 ' + '、'.join(f'{value:g} 分' for value in sorted({float(item) for item in scores})))
    if formula_set:
        evidence.append('公式 ' + '、'.join(f'{value:g}%' for value in sorted(formula_set)))
    return '价格评分口径待核对：' + '，'.join(evidence) + '。按采购人正式澄清确认后填写。'


_PRICE_SCORING = re.compile(r"价格(?:评分|分值|得分|分|权重)|(?:磋商|投标)?报价得分|低价优先法")
_PRICE_CALCULATION = re.compile(r"[×xX*]\s*\d{1,3}(?:\.\d+)?\s*[%％]\s*[×xX*]\s*100")


def review_price_score(markdown: str, conflict: str) -> str:
    if not conflict or not markdown or '<!--TENDER_FORM:SOURCE-->' in markdown:
        return markdown
    found = False

    def review(value: str) -> str:
        nonlocal found
        if _PRICE_CALCULATION.search(value) or (
            _PRICE_SCORING.search(value) and re.search(r'\d+\s*[%％分]|[=＝]|基准价|低价优先法', value)
        ):
            found = True
            return '价格评分口径待核对'
        return value

    lines = []
    for line in markdown.splitlines():
        if line == conflict or line.startswith('<!--TENDER_'):
            lines.append(line)
        elif line.strip().startswith('|'):
            lines.append('|'.join(review(cell) for cell in line.split('|')))
        else:
            lines.append(''.join(review(part) for part in re.split(r'([。；])', line)))
    if found and conflict not in lines:
        lines.extend(['', conflict])
    return '\n'.join(lines)


def is_fixed_form(title: str) -> bool:
    return any(
        name in _FIXED_LETTERS
        or name.endswith(('声明函', '响应函', '承诺函'))
        or (name.endswith('表') and any(key in name for key in ('报价', '开标', '响应', '偏离', '业绩')))
        for name in _form_titles(_title(title))
    )

def _title(value):
    value = re.sub(r'^\s*(?:\d+(?:[-.．]\d+)*|[一二三四五六七八九十百]+)[.．、\s-]+', '', value)
    return re.sub(r'\s+', '', value).strip()


_DOCX_ALIASES = {
    "报价费率表": ("报价费率表", "报价表"),
    "报价表": ("报价表", "报价费率表"),
    "无重大违法记录声明函": ("无重大违法记录声明函", "无重大违法记录声明函、无不良信用记录声明函"),
    "无不良信用记录声明函": ("无不良信用记录声明函", "无重大违法记录声明函、无不良信用记录声明函"),
    "投标函正文": ("投标函", "磋商响应函", "响应函"),
    "投标函": ("投标函", "磋商响应函", "响应函"),
    "投标授权书": ("投标授权书", "授权书"),
    "分项报价明细表": ("分项报价明细表", "分项报价表"),
    "业绩一览表": ("业绩一览表", "类似项目业绩列表", "磋商业绩承诺函"),
}


def _form_titles(title):
    title = re.sub(r'[（(](?:不超过|最高|上限).*?[）)]$', '', title)
    if re.search(r'(?:供货|配送|类似项目).*业绩(?:一览表|列表)$', title):
        return (title, *_DOCX_ALIASES["业绩一览表"])
    return _DOCX_ALIASES.get(title, (title,))


def _merge_coords(values):
    result = []
    for value in sorted(values):
        if not result or value - result[-1] > 1.5:
            result.append(value)
    return result


def pdf_grids(page):
    """Read vector borders, including cells merged horizontally/vertically.

    Text-based table inference loses empty quote columns, so no text fallback.
    """
    horizontal, vertical = [], []
    for path in page.get_drawings():
        for item in path['items']:
            segments = []
            if item[0] == 'l':
                segments = [(item[1].x, item[1].y, item[2].x, item[2].y)]
            elif item[0] == 're':
                r = item[1]
                if r.height <= 1.5:
                    segments = [(r.x0, r.y0, r.x1, r.y0)]
                elif r.width <= 1.5:
                    segments = [(r.x0, r.y0, r.x0, r.y1)]
                else:
                    segments = [(r.x0,r.y0,r.x1,r.y0),(r.x0,r.y1,r.x1,r.y1),(r.x0,r.y0,r.x0,r.y1),(r.x1,r.y0,r.x1,r.y1)]
            for x0,y0,x1,y1 in segments:
                if abs(y1-y0)<1 and abs(x1-x0)>8:
                    horizontal.append((min(x0,x1),y0,max(x0,x1)))
                elif abs(x1-x0)<1 and abs(y1-y0)>8:
                    vertical.append((x0,min(y0,y1),max(y0,y1)))
    # Each table's left/right outer borders run over its full height.
    seen = set()
    for left in sorted(vertical):
        for right in sorted(vertical):
            x0,y0,y1 = left; x1,top,bottom = right
            if x1-x0<100 or abs(top-y0)>2 or abs(bottom-y1)>2:
                continue
            if not any(abs(h[1]-y0)<2 and h[0]<=x0+2 and h[2]>=x1-2 for h in horizontal):
                continue
            if not any(abs(h[1]-y1)<2 and h[0]<=x0+2 and h[2]>=x1-2 for h in horizontal):
                continue
            xs=_merge_coords([x0,x1]+[v[0] for v in vertical if x0<v[0]<x1 and v[1]>=y0-2 and v[2]<=y1+2])
            ys=_merge_coords([y0,y1]+[h[1] for h in horizontal if y0+2<h[1]<y1-2 and h[0]>=x0-2 and h[2]<=x1+2])
            if len(xs)<3 or len(ys)<3 or len(xs)>21 or len(ys)>101:
                continue
            # A candidate spanning a subset of columns is not the outer grid.
            if any(v[0]<x0-2 and abs(v[1]-y0)<2 and abs(v[2]-y1)<2 for v in vertical) or any(v[0]>x1+2 and abs(v[1]-y0)<2 and abs(v[2]-y1)<2 for v in vertical):
                continue
            key=tuple(round(v) for v in (x0,y0,x1,y1))
            if key in seen: continue
            seen.add(key)
            nr,nc=len(ys)-1,len(xs)-1
            parent=list(range(nr*nc))
            def find(a):
                while parent[a]!=a: a=parent[a]
                return a
            def union(a,b): parent[find(b)]=find(a)
            for r in range(nr):
                for c in range(nc):
                    if c+1<nc and not any(abs(v[0]-xs[c+1])<2 and v[1]<=ys[r]+2 and v[2]>=ys[r+1]-2 for v in vertical): union(r*nc+c,r*nc+c+1)
                    if r+1<nr and not any(abs(h[1]-ys[r+1])<2 and h[0]<=xs[c]+2 and h[2]>=xs[c+1]-2 for h in horizontal): union(r*nc+c,(r+1)*nc+c)
            groups={}
            for n in range(nr*nc): groups.setdefault(find(n),[]).append(divmod(n,nc))
            cells=[]; rows=[['']*nc for _ in range(nr)]
            valid=True
            for group in groups.values():
                r0=min(r for r,c in group);r1=max(r for r,c in group)+1;c0=min(c for r,c in group);c1=max(c for r,c in group)+1
                if len(group)!=(r1-r0)*(c1-c0): valid=False;break
                text=page.get_textbox((xs[c0]+1,ys[r0]+1,xs[c1]-1,ys[r1]-1))
                text=re.sub(r'\s+', '', text)
                underlines=[h for h in horizontal if h[0]>xs[c0]+3 and h[2]<xs[c1]-3 and ys[r0]+3<h[1]<ys[r1]-3]
                if underlines:
                    text=text.replace('第标段', '第____标段')
                    if text=='费率':text='费率________'
                rows[r0][c0]=text
                cells.append(dict(row=r0,col=c0,rowspan=r1-r0,colspan=c1-c0))
            if valid:
                yield dict(widths=[round(xs[i+1]-xs[i],2) for i in range(nc)],heights=[round(ys[i+1]-ys[i],2) for i in range(nr)],cells=cells,rows=rows,bbox=[x0,y0,x1,y1],header=nc>=3 and any("序号" in v for v in rows[0]))


def _markdown_table(table):
    meta={k:v for k,v in table.items() if k not in ('rows','bbox')}
    encoded=base64.b64encode(json.dumps(meta,ensure_ascii=True,separators=(',',':')).encode()).decode()
    rows=table['rows']
    clean=lambda x:str(x or '').replace('|','｜').replace('\n',' ')
    lines=['<!--TENDER_TABLE:'+encoded+'-->','| '+' | '.join(map(clean,rows[0]))+' |','| '+' | '.join(['---']*len(rows[0]))+' |']
    lines.extend('| '+' | '.join(map(clean,row))+' |' for row in rows[1:])
    return '\n'.join(lines)


@lru_cache(maxsize=64)
def _extract(pathstr,mtime,size,title):
    path=Path(pathstr)
    if path.suffix.lower()=='.pdf':
        import pymupdf
        with pymupdf.open(path) as doc:
            for page in doc:
                text=page.get_text()
                headings=[b for b in page.get_text('blocks') if any(_title(line) in _form_titles(title) for line in b[4].splitlines())]
                if not headings: continue
                # Lists of all forms and qualification checklists are not a form.
                if '投标文件资料清单' in text or '废标指标一览表' in text: continue
                if title in _FIXED_LETTERS:
                    body=page.get_textbox((45,max(h[3] for h in headings)+1,page.rect.width-45,page.rect.height-70)).strip()
                    if re.search(r'签章|公章|盖章', body):
                        return '# '+title+'\n\n<!--TENDER_FORM:SOURCE-->\n\n'+body
                    continue
                tables=[t for t in pdf_grids(page) if t['bbox'][1]>min(h[1] for h in headings)]
                if not tables: continue
                # Refuse ambiguous pages instead of silently using a different form.
                if len(tables)!=1: continue
                table=tables[0]; y0=table['bbox'][1];y1=table['bbox'][3]
                before=page.get_textbox((45,max(h[3] for h in headings)+1,page.rect.width-45,y0-2)).strip()
                after=page.get_textbox((45,y1+2,page.rect.width-45,page.rect.height-70)).strip()
                table['source_page']=page.number+1
                return '\n\n'.join(s for s in ('# '+title,before,_markdown_table(table),after) if s)
    elif path.suffix.lower() == '.docx':
        return _extract_docx(path, title)
    return None


def _docx_text(node, *, rich=False):
    if not rich:
        return ''.join(node.xpath('.//w:t/text()'))
    from docx.oxml.ns import qn

    values = []
    for run in node.xpath('.//w:r'):
        text = ''.join(run.xpath('./w:t/text()'))
        bold = run.xpath('./w:rPr/w:b')
        if text.strip() and bold and bold[0].get(qn('w:val'), '1') not in {'0', 'false', 'off'}:
            values.append(f'**{text}**')
        else:
            values.append(text)
    return ''.join(values)


def _cell_paragraphs(cell):
    from docx.oxml.ns import qn

    paragraphs = []
    for paragraph in cell.xpath('./w:p'):
        runs = []
        for run in paragraph.xpath('.//w:r'):
            text = ''.join(
                child.text or '' if child.tag == qn('w:t') else
                '\t' if child.tag == qn('w:tab') else '\n' if child.tag in {qn('w:br'), qn('w:cr')} else ''
                for child in run
            )
            if not text:
                continue
            props = {"text": text}
            for key, name in (('bold', 'b'), ('italic', 'i'), ('underline', 'u')):
                value = run.xpath('./w:rPr/w:' + name)
                if value and value[0].get(qn('w:val'), '1') not in {'0', 'false', 'off', 'none'}:
                    props[key] = True
            runs.append(props)
        alignment = paragraph.xpath('./w:pPr/w:jc')
        align = alignment[0].get(qn('w:val')) if alignment else None
        properties = {"runs": runs, "align": align if align in {'left', 'center', 'right', 'both'} else 'left'}
        indent = paragraph.xpath('./w:pPr/w:ind')
        if indent:
            for side in ('left', 'right'):
                value = indent[0].get(qn('w:' + side), '0')
                if value.isdigit() and 0 < int(value) <= 5000:
                    properties[side + '_indent'] = int(value)
        # Some source forms rely on Word wrapping two fields at an indented
        # margin. Keep the field boundary explicit when transplanting the cell.
        labels = [i for i, run in enumerate(runs) if re.match(r'^费率[大小]写[：:]', run["text"])]
        if len(labels) == 2 and properties.get("right_indent"):
            boundary = labels[1]
            paragraphs.extend([
                {**properties, "runs": runs[:boundary]},
                {**properties, "runs": runs[boundary:]},
            ])
        else:
            paragraphs.append(properties)
    return paragraphs


def _docx_table(node):
    from docx.oxml.ns import qn

    widths = [int(n.get(qn('w:w')) or 1000) / 20 for n in node.xpath('./w:tblGrid/w:gridCol')]
    if not widths:
        return None
    rows, cells, heights = [], [], []
    open_merges = {}
    for ri, tr in enumerate(node.xpath('./w:tr')):
        row, ci = [''] * len(widths), 0
        height = tr.xpath('./w:trPr/w:trHeight')
        heights.append(max(1, int(height[0].get(qn('w:val'), '480')) / 20) if height else 24)
        for tc in tr.xpath('./w:tc'):
            spans = tc.xpath('./w:tcPr/w:gridSpan')
            span = int(spans[0].get(qn('w:val'))) if spans else 1
            if ci + span > len(widths):
                return None
            merge = tc.xpath('./w:tcPr/w:vMerge')
            if merge and merge[0].get(qn('w:val')) != 'restart' and ci in open_merges:
                open_merges[ci]['rowspan'] += 1
            else:
                cell = dict(row=ri, col=ci, rowspan=1, colspan=span)
                cells.append(cell)
                if merge:
                    open_merges[ci] = cell
                else:
                    open_merges.pop(ci, None)
                paragraphs = _cell_paragraphs(tc)
                plain = ' '.join(_docx_text(p) for p in tc.xpath('./w:p')).replace('|', '｜').strip()
                row[ci] = ' '.join(_docx_text(p, rich=True) for p in tc.xpath('./w:p'))
                if len(paragraphs) > 1 or any(
                    p["align"] != 'center' or p.get("left_indent") or p.get("right_indent") or any(set(run) != {'text'} or '\n' in run['text'] or '\t' in run['text'] for run in p["runs"])
                    for p in paragraphs
                ):
                    cell.update(paragraphs=paragraphs, source_text=plain)
            ci += span
        if ci != len(widths):
            return None
        rows.append(row)
    if len(rows) < 2:
        return None
    return dict(widths=widths, heights=heights, cells=cells, rows=rows, header=False)


def _extract_docx(path, chapter_title):
    from docx import Document
    from docx.oxml.ns import qn

    body = list(Document(path).element.body)
    texts = [_docx_text(node).strip() if node.tag == qn('w:p') else '' for node in body]
    format_sections = [i for i, text in enumerate(texts) if re.match(r'^第[一二三四五六七八九十\d]+章\s*(?:响应|投标)文件格式$', text)]
    start = format_sections[-1] + 1 if format_sections else 0
    finish = next((i for i in range(start, len(body)) if re.match(r'^第[一二三四五六七八九十\d]+章\s*\S', texts[i])), len(body))
    titles = _form_titles(chapter_title)
    for i in range(start, finish):
        if _title(texts[i]) not in titles:
            continue
        source_title = _title(texts[i])
        content = []
        tables = 0
        for j in range(i + 1, finish):
            if re.match(r'^[一二三四五六七八九十百]+[、．.]', texts[j]):
                break
            if body[j].tag == qn('w:tbl'):
                table = _docx_table(body[j])
                if table is None:
                    return None
                content.append(_markdown_table(table))
                tables += 1
            elif texts[j]:
                paragraph = _docx_text(body[j], rich=True).strip()
                if re.match(r'^(?:供应商(?:电子)?签章|日\s*期)\s*[：:]', texts[j]):
                    alignment = body[j].xpath('./w:pPr/w:jc')
                    indent = body[j].xpath('./w:pPr/w:ind')
                    right_aligned = alignment and alignment[0].get(qn('w:val')) == 'right'
                    shifted_right = indent and int(indent[0].get(qn('w:left'), '0')) >= 2400
                    if right_aligned or shifted_right:
                        paragraph = '<!--TENDER_ALIGN:RIGHT-->' + paragraph
                content.append(paragraph)
        letter = source_title in _FIXED_LETTERS or source_title.endswith(('声明函', '响应函', '承诺函'))
        if tables or (letter and any(re.search(r'签章|公章|盖章', part) for part in content)):
            return '\n\n'.join(['# ' + texts[i], '<!--TENDER_FORM:SOURCE-->', *content])
    return None


def extract_form(path: Path, chapter_title: str):
    title=_title(chapter_title)
    # Only fixed response forms, not arbitrary scoring/technical tables.
    if not is_fixed_form(title):
        return None
    if not path.is_file():return None
    stat=path.stat()
    with _FORM_LOCK:
        return _extract(str(path.resolve()),stat.st_mtime_ns,stat.st_size,title)
