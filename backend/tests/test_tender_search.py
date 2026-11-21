import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from app.api.routes import tender_search as search


SAMPLE = '''<html><head><meta charset="utf-8"></head><body><ul>
<li><a href="/cggg/dfgg/jzxcs/202611/t20261109_1.htm" title="示例市食堂食材采购项目">示例市食堂食材采购项目</a><span>发布时间：2026-11-09 09:26 地域：华东 采购人：示例市某单位</span></li>
<li><a href="https://evil.example/2.htm">示例食材</a><span>发布时间：2026-11-09 地域：华东</span></li>
<li><a href="/cggg/dfgg/jzxcs/202611/t20261109_3.htm">电子设备</a><span>发布时间：2026-11-09 地域：华东</span></li>
</ul></body></html>'''.encode('utf8')


class TenderSearchTests(unittest.TestCase):
    def test_parse_and_source_restrictions(self):
        rows = search.parse_notices(SAMPLE, '来源', 'https://www.ccgp.gov.cn/cggg/dfgg/jzxcs/')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '示例市食堂食材采购项目')
        self.assertEqual(rows[0]['region'], '华东')
        self.assertEqual(rows[0]['buyer'], '示例市某单位')
        self.assertEqual(rows[0]['published_at'], '2026-11-09 09:26')
        self.assertTrue(rows[0]['url'].startswith('https://www.ccgp.gov.cn/'))

    def test_changed_layout_is_not_reported_as_empty_success(self):
        with self.assertRaises(ValueError):
            search.parse_notices(b'<html>access denied</html>', 'x', 'https://www.ccgp.gov.cn/')

    def test_database_deduplicates_and_prioritizes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.cache') as tmp:
            path = Path(tmp)/'test.sqlite'
            def connect():
                conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row; return conn
            with patch.object(search.storage, 'connect', side_effect=connect):
                first = search.parse_notices(SAMPLE, '来源', 'https://www.ccgp.gov.cn/')
                other = dict(first[0], notice_id='2', title='示例甲食品项目', buyer='示例甲市某单位')
                search.save_notices([other]+first+first)
                rows=search.list_notices()
                self.assertEqual(len(rows),2)
                self.assertIn('示例',rows[0]['title'])
                self.assertEqual(len(search.list_notices('示例甲')),1)
                self.assertEqual(len(search.list_notices(keyword='食堂')),1)
                search.update_tracking(first[0]['notice_id'], search.TrackingUpdate(state='跟进中'))
                self.assertEqual(search.list_notices('示例')[0]['follow_state'], '跟进中')
                with self.assertRaises(Exception):
                    search.update_tracking(first[0]['notice_id'], search.TrackingUpdate(state='已中标'))

    def test_source_failure_does_not_delete_cached_results(self):
        async def run():
            class Client:
                def __init__(self, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def get(self, *args, **kwargs): raise httpx.ConnectError('offline')
            with patch.object(search.httpx, 'AsyncClient', Client), \
                 patch.object(search, 'save_notices') as save, \
                 patch.object(search, 'save_source_status'):
                result=await search._refresh_notices()
            self.assertEqual(len(result['sources']),4)
            self.assertTrue(all(not r['ok'] for r in result['sources']))
            save.assert_not_called()
        asyncio.run(run())

    def test_robot_rules_prevent_disallowed_fetch(self):
        async def run():
            calls = []
            class Client:
                def __init__(self, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def get(self, url, **kwargs):
                    calls.append(url)
                    return httpx.Response(200, text='User-agent: *\nDisallow: /cggg/\n', request=httpx.Request('GET', url))
            with patch.object(search.httpx, 'AsyncClient', Client), patch.object(search, 'save_notices') as save, patch.object(search, 'save_source_status'):
                result = await search._refresh_notices()
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(not r['ok'] for r in result['sources']))
            save.assert_not_called()
        asyncio.run(run())
