from __future__ import annotations

import asyncio
import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from PIL import Image
from starlette.datastructures import UploadFile

from app.modules.knowledge.local_ocr import OCRResult
from app.services import blind_check
from app.api.routes import bid_graphic
from app.main import app
from app.services import auth_service
from fastapi.testclient import TestClient


def make_docx():
    doc = Document()
    doc.core_properties.author = ''
    doc.core_properties.last_modified_by = ''
    doc.add_paragraph('无身份信息的配送方案')
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def replace_zip(data, updates):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as dest:
        for item in source.infolist():
            if item.filename not in updates:
                dest.writestr(item, source.read(item.filename))
        for name, content in updates.items():
            dest.writestr(name, content)
    return output.getvalue()


class BlindCheckTests(unittest.TestCase):
    def test_clean_document_and_missing_dictionary(self):
        self.assertFalse(blind_check.inspect_docx(make_docx(), ['示例企业'])['blocked'])
        self.assertTrue(blind_check.inspect_docx(make_docx(), [])['blocked'])

    def test_split_runs_header_footer_and_metadata_are_located(self):
        doc = Document()
        doc.core_properties.author = '张三'
        doc.core_properties.last_modified_by = ''
        p = doc.add_paragraph(); p.add_run('合'); p.add_run('家 康')
        doc.sections[0].header.paragraphs[0].text = '品牌甲'
        doc.sections[0].footer.paragraphs[0].text = '电话 13800138000'
        output = io.BytesIO(); doc.save(output)
        report = blind_check.inspect_docx(output.getvalue(), ['示例公司', '品牌甲'])
        self.assertTrue(report['blocked'])
        locations = ' '.join(item['location'] for item in report['issues'])
        self.assertIn('正文', locations); self.assertIn('页眉', locations)
        self.assertIn('页脚', locations); self.assertIn('文件属性', locations)
        self.assertTrue(any(i['kind'] == 'metadata' and '张三' in i['excerpt'] for i in report['issues']))

    def test_comments_and_tracked_changes_are_blocked(self):
        comments = b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="0" w:author="test"><w:p><w:r><w:t>secret</w:t></w:r></w:p></w:comment></w:comments>'
        report = blind_check.inspect_docx(replace_zip(make_docx(), {'word/comments.xml': comments}), ['secret'])
        self.assertTrue(any(i['kind'] == 'comments' for i in report['issues']))
        self.assertTrue(any(i['kind'] == 'sensitive_text' and '批注' in i['location'] for i in report['issues']))

    def test_image_ocr_hit_is_blocked_and_locations_reported(self):
        image = io.BytesIO(); Image.new('RGB', (100, 100), 'white').save(image, 'PNG'); image.seek(0)
        doc = Document(); doc.core_properties.author = ''; doc.add_paragraph('图片位置').add_run().add_picture(image)
        output = io.BytesIO(); doc.save(output)
        with patch.object(blind_check.local_ocr, 'recognize_isolated', return_value=OCRResult('示例公司', 'recognized')):
            report = blind_check.inspect_docx(output.getvalue(), ['示例公司'])
        self.assertTrue(any(i['kind'] == 'sensitive_text' and 'OCR' in i['location'] for i in report['issues']))
        self.assertEqual(len(report['images']), 1)
        self.assertIn('第', report['images'][0]['location'])
        self.assertTrue(report['images'][0]['preview'].startswith('data:image/jpeg;base64,'))

    def test_image_without_ocr_text_still_requires_visual_review(self):
        image = io.BytesIO(); Image.new('RGB', (10, 10)).save(image, 'PNG'); image.seek(0)
        doc = Document(); doc.core_properties.author = ''; doc.add_picture(image)
        output = io.BytesIO(); doc.save(output)
        with patch.object(blind_check.local_ocr, 'recognize_isolated', return_value=OCRResult('', 'no_text')):
            report = blind_check.inspect_docx(output.getvalue(), ['企业'])
        self.assertFalse(report['blocked'])
        self.assertEqual(len(report['images']), 1)
        self.assertEqual(report['images'][0]['ocr_status'], 'no_text')

    def test_ocr_failure_and_unsupported_data_never_pass(self):
        image = io.BytesIO(); Image.new('RGB', (10, 10)).save(image, 'PNG'); image.seek(0)
        doc = Document(); doc.core_properties.author = ''; doc.add_picture(image)
        output = io.BytesIO(); doc.save(output)
        with patch.object(blind_check.local_ocr, 'recognize_isolated', side_effect=RuntimeError('OCR unavailable')):
            report = blind_check.inspect_docx(output.getvalue(), ['企业'])
        self.assertTrue(any(i['kind'] == 'unreadable_image' for i in report['issues']))
        report = blind_check.inspect_docx(replace_zip(make_docx(), {'word/embeddings/object.bin': b'unknown'}), ['企业'])
        self.assertTrue(any(i['kind'] == 'unsupported' for i in report['issues']))
        self.assertTrue(blind_check.inspect_docx(b'bad file', ['企业'])['blocked'])

    def test_blind_project_cannot_skip_check_or_use_native_download(self):
        project = SimpleNamespace(settings=SimpleNamespace(bidType='blind'))
        with patch.object(bid_graphic.storage, 'load_project_by_bid_id', return_value=project), \
             patch.object(bid_graphic, 'get_current_tenant', return_value=SimpleNamespace(company_name='测试公司')):
            response = asyncio.run(bid_graphic.check_blind_docx(1, UploadFile(filename='x.docx', file=io.BytesIO(make_docx())), terms='', force=False))
            self.assertTrue(response['data']['required'])
            native = asyncio.run(bid_graphic.download_graphic_docx(1))
            self.assertEqual(native.status_code, 409)

    def test_http_routes_require_auth_and_respect_project_access(self):
        tenant = SimpleNamespace(company_name='测试公司', company_id='test')
        with patch.object(auth_service, 'authenticate_token', side_effect=lambda token: ({}, {}) if token == 'valid' else None), \
             patch.object(auth_service, 'build_tenant_context', return_value=tenant), \
             patch.object(bid_graphic.storage, 'load_project_by_bid_id', side_effect=lambda bid: SimpleNamespace(settings=SimpleNamespace(bidType='blind')) if bid == 1 else (_ for _ in ()).throw(FileNotFoundError())):
            client = TestClient(app)
            self.assertEqual(client.get('/api/bid/1/graphic-section/blind-mode').status_code, 401)
            self.assertTrue(client.get('/api/bid/1/graphic-section/blind-mode', headers={'Authorization': 'Bearer valid'}).json()['data']['blind'])
            self.assertEqual(client.get('/api/bid/2/graphic-section/blind-mode', headers={'Authorization': 'Bearer valid'}).status_code, 404)
            self.assertEqual(client.post('/api/bid/2/graphic-section/blind-check', headers={'Authorization': 'Bearer valid'}, files={'file': ('x.docx', make_docx())}).status_code, 404)
            self.assertEqual(client.get('/api/bid/1/graphic-section/download', headers={'Authorization': 'Bearer valid'}).status_code, 409)

    def test_nonblind_project_can_skip(self):
        with patch.object(bid_graphic.storage, 'load_project_by_bid_id', return_value=SimpleNamespace(settings=SimpleNamespace(bidType='open'))):
            result = asyncio.run(bid_graphic.check_blind_docx(1, UploadFile(filename='x.docx', file=io.BytesIO(b'')), terms='', force=False))
        self.assertFalse(result['data']['required'])


if __name__ == '__main__': unittest.main()
