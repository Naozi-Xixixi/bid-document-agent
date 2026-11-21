"""Inspect the exact final DOCX for identifiable content before blind export.

This is a conservative screening tool, not a proof of anonymity. Callers must
obtain human confirmation for every image, even if OCR did not recognize text.
"""
from __future__ import annotations

import io
import base64
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from lxml import etree
from PIL import Image

from app.modules.knowledge import local_ocr

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'w': W, 'r': R}
MAX_UNPACKED = 512 * 1024 * 1024
MAX_IMAGE = 32 * 1024 * 1024
MAX_ENTRIES = 10000


def _normalized(value: str) -> str:
    return re.sub(r'[\s\u200b\u200c\u200d\ufeff]+', '', unicodedata.normalize('NFKC', value)).casefold()


def _section(name: str) -> str:
    if name == 'word/document.xml': return '正文'
    if name.startswith('word/header'): return '页眉'
    if name.startswith('word/footer'): return '页脚'
    if name.startswith('word/comments'): return '批注'
    if name.startswith('docProps/'): return '文件属性'
    if name.startswith('word/footnotes'): return '脚注'
    if name.startswith('word/endnotes'): return '尾注'
    return '文档内部内容'


def inspect_docx(data: bytes, words: list[str]) -> dict:
    issues, images = [], []
    def issue(kind, location, detail, excerpt=''):
        issues.append({'kind': kind, 'location': location, 'detail': detail, 'excerpt': excerpt[:180]})
    terms = sorted({s.strip() for s in words if len(_normalized(s)) >= 2}, key=len, reverse=True)
    if not terms:
        issue('dictionary', '检查词库', '请至少填写一个有效的企业名称或身份识别词。')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(x.file_size for x in entries) > MAX_UNPACKED:
            raise ValueError('文档解压后的内容过大')
        names = {x.filename for x in entries}
        if len(names) != len(entries) or 'word/document.xml' not in names or '[Content_Types].xml' not in names:
            raise ValueError('文档结构无效或包含重复文件')
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
        def check_text(text, location):
            normalized = _normalized(text)
            for term in terms:
                if _normalized(term) in normalized:
                    issue('sensitive_text', location, '发现身份识别词：' + term, text)
            for pattern, label in [(r'(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b', '电子邮箱'), (r'(?i)\b(?:https?://|www\.)[^\s<>]+', '网址'), (r'(?<!\d)1[3-9]\d{9}(?!\d)', '手机号码')]:
                match = re.search(pattern, unicodedata.normalize('NFKC', text))
                if match: issue('identity_pattern', location, '发现可能泄露身份的' + label, match.group())
        relmaps = {}
        for entry in entries:
            name = entry.filename
            if not name.endswith('.rels'): continue
            try:
                tree = etree.fromstring(archive.read(name), parser)
                mapping = {}
                for rel in tree:
                    target = rel.get('Target', '')
                    if rel.get('TargetMode', '').lower() == 'external':
                        issue('external_link', name, '存在外部链接或外部资源，请删除并复核。', target)
                    if '/_rels/' in name:
                        import posixpath
                        source = name.replace('/_rels/', '/').removesuffix('.rels')
                        mapping[rel.get('Id')] = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
                        relmaps[source] = mapping
            except Exception:
                issue('unreadable', name, '关系文件无法解析。')
        references = {}
        for entry in entries:
            name = entry.filename
            if not name.endswith('.xml') or not (name.startswith('word/') or name.startswith('docProps/') or name.startswith('customXml/')): continue
            try:
                tree = etree.fromstring(archive.read(name), parser)
            except Exception:
                issue('unreadable', name, '文档内容无法解析，不能确认是否包含身份信息。')
                continue
            section = _section(name)
            if name.startswith('docProps/'):
                for node in tree.iter():
                    if len(node) == 0 and (node.text or '').strip():
                        value = node.text.strip()
                        field = etree.QName(node).localname
                        check_text(value, f'{section} / {field}')
                        if field.lower() in {'creator', 'lastmodifiedby', 'manager', 'company'}:
                            issue('metadata', f'{section} / {field}', '文件属性含作者、修改者或公司信息，需清除。', value)
                continue
            paragraphs = tree.xpath('//w:p', namespaces=NS)
            for index, para in enumerate(paragraphs, 1):
                text = ''.join(para.xpath('.//w:t/text() | .//w:delText/text() | .//w:instrText/text()', namespaces=NS))
                location = f'{section} / {name} / 第{index}段'
                if text: check_text(text, location)
                for node in para.xpath('.//*[@r:embed or @r:id or @r:link]', namespaces=NS):
                    rid = node.get(f'{{{R}}}embed') or node.get(f'{{{R}}}id') or node.get(f'{{{R}}}link')
                    target = relmaps.get(name, {}).get(rid)
                    if target and target.startswith('word/media/'):
                        references.setdefault(target, []).append(location)
            if name.startswith('word/comments') and tree.xpath('//*[local-name()="comment"]'):
                issue('comments', section + ' / ' + name, '文档包含批注及作者信息，请删除所有批注。')
            if tree.xpath('//*[local-name()="ins" or local-name()="del" or local-name()="moveFrom" or local-name()="moveTo"]'):
                issue('tracked_changes', section + ' / ' + name, '文档包含修订记录，请接受或拒绝修订后重新检查。')
            # Cover drawing descriptions, alternate text and custom XML text too.
            for node in tree.iter():
                for key, value in node.attrib.items():
                    if etree.QName(key).localname in {'descr', 'title', 'author', 'initials', 'string'} and value.strip():
                        check_text(value, section + ' / ' + name + ' / 图形或批注属性')
            if not paragraphs and (name.startswith('word/') or name.startswith('customXml/')):
                text = ' '.join(t.strip() for t in tree.itertext() if t.strip())
                if text: check_text(text, section + ' / ' + name)
        for entry in entries:
            name = entry.filename
            if name.startswith(('word/embeddings/', 'word/activeX/')):
                issue('unsupported', name, '包含嵌入对象或自定义数据，无法完整检查，请删除后重试。')
            if not name.startswith('word/media/') or entry.is_dir(): continue
            location = '；'.join(references.get(name, [])) or '文档媒体（未找到引用位置）'
            location += ' / ' + name
            if entry.file_size > MAX_IMAGE:
                issue('unreadable_image', location, '图片过大，无法执行 OCR。')
                continue
            try:
                image_data = archive.read(name)
                with Image.open(io.BytesIO(image_data)) as image:
                    if image.width * image.height > 80_000_000: raise ValueError('图片像素过大')
                    image.load()
                    if getattr(image, 'n_frames', 1) != 1: raise ValueError('多帧图片')
                    preview = image.convert('RGB')
                    preview.thumbnail((800, 800))
                    preview_bytes = io.BytesIO()
                    preview.save(preview_bytes, format='JPEG', quality=75)
                    preview_url = 'data:image/jpeg;base64,' + base64.b64encode(preview_bytes.getvalue()).decode('ascii')
                    with tempfile.TemporaryDirectory(prefix='blind-ocr-') as folder:
                        image_path = Path(folder) / 'image.png'
                        image.convert('RGB').save(image_path)
                        result = local_ocr.recognize_isolated(image_path)
                if result.status not in {'recognized', 'no_text'}: raise ValueError('OCR 状态未知')
                if result.text: check_text(result.text, location + ' / OCR')
                images.append({'location': location, 'ocr_status': result.status, 'ocr_text': result.text[:300], 'preview': preview_url})
            except Exception as exc:
                issue('unreadable_image', location, '图片无法完整识别，请处理后重试：' + str(exc)[:200])
        archive.close()
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        issue('invalid_docx', '文件', '无法检查 Word 文件：' + str(exc)[:200])
    return {'issues': issues, 'images': images, 'blocked': bool(issues), 'terms_count': len(terms),
            'notice': 'OCR 和词库不能保证识别所有姓名、品牌、Logo、水印及其他身份线索。请逐图和全文人工复核。'}
