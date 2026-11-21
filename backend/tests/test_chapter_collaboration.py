"""Real HTTP permissions/CAS/workflow tests, isolated databases on the project drive."""
import asyncio
import gc
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.tenant_context import set_current_tenant, reset_current_tenant
from app.services import auth_service, storage, bid_graphic_service, chapter_collaboration
from app.schemas.bid_graphic import GraphicSectionGenerateRequest, GraphicSectionSaveRequest


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        test_root = Path(__file__).resolve().parents[2] / '.cache' / 'collaboration-tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=test_root))
        for key, value in dict(data_dir=Path(self.temp), database_path=None, db_backend='sqlite', admin_initial_username='system_admin', admin_initial_password='test-password-1234').items():
            self.stack.enter_context(patch.object(settings, key, value))
        self.stack.enter_context(patch.object(auth_service, '_AUTH_SCHEMA_PATH', None))
        self.client = TestClient(app)
        self.addCleanup(lambda: (self.client.close(), gc.collect()))
        self.tokens, self.ids = {}, {}
        self.login('system_admin', 'test-password-1234')
        self.company = self.req('POST', '/api/auth/admin/companies', 'system_admin', {'company_name': '协作测试公司'}).json()['data']
        self.other = self.req('POST', '/api/auth/admin/companies', 'system_admin', {'company_name': '其他公司'}).json()['data']
        for name, role, company in [('writer', 'company_user', self.company), ('writer2', 'company_user', self.company), ('reviewer', 'company_user', self.company), ('manager', 'admin', self.company), ('outsider', 'company_user', self.other)]:
            r = self.req('POST', '/api/auth/admin/users', 'system_admin', {'username': name, 'password': 'test-password-1234', 'display_name': name, 'company_id': company['company_id'], 'role': role})
            self.assertEqual(r.status_code, 200, r.text)
            self.ids[name] = r.json()['data']['user_id']
            self.login(name, 'test-password-1234')
        project = self.req('POST', '/api/projects', 'writer', {'settings': {'bidTitle': '协作验收'}}).json()
        self.bid = project['bidId']
        user, company = auth_service.authenticate_token(self.tokens['writer'])
        self.tenant = auth_service.build_tenant_context(user, company)
        token = set_current_tenant(self.tenant)
        try:
            with storage.connect() as conn:
                now = storage.now_utc().isoformat()
                v = conn.execute("INSERT INTO bid_catalog_versions (bid_id,version_name,version_style,catalog_content,create_time,update_time) VALUES (?, '测试目录','standard','[]',?,?)", (self.bid, now, now)).lastrowid
                self.chapter = conn.execute("INSERT INTO bid_catalog_chapters (bid_id,version_id,chapter_title,level,sort_order,create_time) VALUES (?,?,'配送方案',1,1,?)", (self.bid,v,now)).lastrowid
                conn.commit()
        finally:
            reset_current_tenant(token)
        # No knowledge/model access is necessary to test persistence and authorization.
        self.stack.enter_context(patch.object(bid_graphic_service, '_knowledge_images_by_ids', return_value=[]))
        self.stack.enter_context(patch.object(bid_graphic_service, '_audit_chapter_assets', return_value=[]))
        self.base = f'/api/bid/{self.bid}/chapters/{self.chapter}/collaboration'

    def login(self, username, password):
        r = self.client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        self.tokens[username] = r.json()['data']['access_token']

    def req(self, method, url, user='writer', data=None):
        return self.client.request(method, url, headers={'Authorization': 'Bearer ' + self.tokens[user]}, **({'json': data} if data is not None else {}))

    def state(self, user='writer'):
        r = self.req('GET', self.base, user)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['data']

    def save(self, text, revision=0, user='writer'):
        return self.req('PUT', f'/api/bid/{self.bid}/graphic-section/save', user, {'chapter_id':self.chapter, 'markdown':text, 'expected_revision':revision})

    def action(self, action, user, revision=None, note='测试意见'):
        return self.req('POST', self.base+'/workflow', user, {'action':action, 'expected_revision': self.state(user)['revision'] if revision is None else revision, 'note':note})

    def assign(self):
        r=self.req('PUT', self.base+'/assignment', 'manager', {'assigned_user_id': self.ids['writer'], 'reviewer_user_id': self.ids['reviewer'], 'expected_revision':self.state()['revision']})
        self.assertEqual(r.status_code, 200, r.text)

    def test_two_writers_cannot_overwrite_same_revision(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda name:self.save(name,0,name), ['writer','writer2']))
        self.assertEqual(sorted(r.status_code for r in results), [200,409])
        winner=next(r.json()['data']['markdown'] for r in results if r.status_code==200)
        r=self.req('GET',f'/api/bid/{self.bid}/graphic-section/{self.chapter}')
        self.assertEqual(r.json()['data']['markdown'],winner)
        self.assertEqual(self.state()['revision'],1)

    def test_missing_revision_rejected(self):
        r=self.req('PUT', f'/api/bid/{self.bid}/graphic-section/save', 'writer', {'chapter_id':self.chapter,'markdown':'不能盲写'})
        self.assertEqual(r.status_code,428,r.text)
        self.assertEqual(self.state()['revision'],0)

    def test_assignment_enforced_and_only_company_members(self):
        payload={'expected_revision':0,'assigned_user_id':self.ids['outsider'],'reviewer_user_id':self.ids['reviewer']}
        self.assertEqual(self.req('PUT',self.base+'/assignment','manager',payload).status_code,400)
        payload['assigned_user_id']=self.ids['writer']
        self.assertEqual(self.req('PUT',self.base+'/assignment','writer',payload).status_code,403)
        self.assign()
        self.assertEqual(self.save('他人不能改',1,'writer2').status_code,403)
        self.assertEqual(self.save('负责人修改',1).status_code,200)
        members=self.req('GET','/api/bid/collaboration/members').json()['data']
        self.assertNotIn(self.ids['outsider'],[m['user_id'] for m in members])
        self.assertTrue(all('password_hash' not in m for m in members))

    def test_review_lock_comments_approval_and_reopen(self):
        self.assign()
        self.assertEqual(self.save('实际正文',1).status_code,200)
        self.assertEqual(self.action('submit','writer').status_code,200)
        self.assertEqual(self.save('不允许修改',3).status_code,409)
        self.assertEqual(self.action('approve','writer').status_code,403)
        r=self.req('POST',self.base+'/comments','reviewer',{'expected_revision':3,'body':'补充依据','quote':'实际正文'})
        self.assertEqual(r.status_code,200,r.text)
        comment=r.json()['data']['comments'][0]
        self.assertEqual(self.action('approve','reviewer').status_code,409)
        self.assertEqual(self.req('POST',self.base+f"/comments/{comment['id']}/resolve",'writer',{'expected_revision':4}).status_code,403)
        self.assertEqual(self.req('POST',self.base+f"/comments/{comment['id']}/resolve",'reviewer',{'expected_revision':4}).status_code,200)
        self.assertEqual(self.action('approve','reviewer',revision=3).status_code,409)
        self.assertEqual(self.action('approve','reviewer').status_code,200)
        self.assertEqual(self.state()['state'],'approved')
        self.assertEqual(self.save('定稿也不能覆盖',6,'manager').status_code,409)
        self.assertEqual(self.action('reopen','writer').status_code,403)
        self.assertEqual(self.action('reopen','reviewer',note='').status_code,400)
        self.assertEqual(self.action('reopen','reviewer').status_code,200)
        self.assertEqual(self.save('重新修改',7).status_code,200)
        self.assertEqual(self.state()['versions'][0]['actor_name'],'writer')

    def test_reject_and_resubmit_preserves_audit(self):
        self.assign(); self.save('草稿',1); self.action('submit','writer')
        self.assertEqual(self.action('reject','reviewer',note='').status_code,400)
        self.assertEqual(self.action('reject','reviewer').status_code,200)
        self.assertEqual(self.state()['state'],'draft')
        self.assertEqual(self.save('修订稿',4).status_code,200)
        self.assertEqual(self.action('submit','writer').status_code,200)
        self.assertEqual(self.action('approve','reviewer').status_code,200)
        self.assertIn('reject',[v['action'] for v in self.state()['versions']])

    def test_versions_compare_restore_create_new_revision(self):
        self.assertEqual(self.save('第一版').status_code,200)
        self.assertEqual(self.save('第二版',1).status_code,200)
        r=self.req('GET',self.base+'/compare?left=1&right=2')
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('第一版',r.json()['data']['diff'])
        self.assertIn('第二版',r.json()['data']['diff'])
        r=self.req('POST',self.base+'/restore','writer',{'expected_revision':2,'revision':1})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['data']['revision'],3)
        r=self.req('GET',f'/api/bid/{self.bid}/graphic-section/{self.chapter}')
        self.assertEqual(r.json()['data']['markdown'],'第一版')
        self.assertEqual(len(self.state()['versions']),4)
        self.assertEqual(self.req('POST',self.base+'/restore','writer',{'expected_revision':2,'revision':0}).status_code,409)

    def test_company_boundary_on_all_collaboration_endpoints(self):
        self.save('公司机密')
        for method,path,data in [('GET','',None),('GET','/compare?left=0&right=1',None),('POST','/restore',{'expected_revision':1,'revision':0}),('POST','/comments',{'expected_revision':1,'body':'越权'}),('POST','/workflow',{'expected_revision':1,'action':'reopen','note':'越权'})]:
            r=self.req(method,self.base+path,'outsider',data)
            self.assertEqual(r.status_code,404,r.text)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(self.base).status_code,401)

    def test_generation_does_not_overwrite_edits_made_while_model_runs(self):
        token=set_current_tenant(self.tenant)
        async def delayed(*args, **kwargs):
            bid_graphic_service.save_graphic_section(self.bid,GraphicSectionSaveRequest(chapter_id=self.chapter,markdown='别人先保存',expected_revision=0))
            return SimpleNamespace(usedApi=True,content='模型的旧上下文内容',error=None)
        generation={'messages':[], 'max_tokens':1200,'fallback':'','length_controlled':False,'existing_markdown':'','images':[],'chapter_path':['配送方案']}
        try:
            with patch.object(bid_graphic_service,'_build_graphic_generation',return_value=generation), patch.object(bid_graphic_service,'chat_completion',side_effect=delayed), patch.object(bid_graphic_service,'_ensure_chapter_images',side_effect=lambda text,g:text):
                with self.assertRaises(Exception) as caught:
                    asyncio.run(bid_graphic_service.generate_graphic_section(self.bid,GraphicSectionGenerateRequest(chapter_id=self.chapter)))
                self.assertIn('未覆盖',str(caught.exception))
            self.assertEqual(bid_graphic_service.get_graphic_section(self.bid,self.chapter)['markdown'],'别人先保存')
        finally:
            reset_current_tenant(token)

    def test_approved_generation_rejected_before_model_call(self):
        self.assign(); self.save('完成稿',1); self.action('submit','writer'); self.action('approve','reviewer')
        token=set_current_tenant(self.tenant)
        try:
            with patch.object(bid_graphic_service,'_build_graphic_generation') as build:
                with self.assertRaises(Exception):
                    asyncio.run(bid_graphic_service.generate_graphic_section(self.bid,GraphicSectionGenerateRequest(chapter_id=self.chapter)))
                build.assert_not_called()
        finally:
            reset_current_tenant(token)

    def test_baseline_retains_preexisting_body(self):
        token=set_current_tenant(self.tenant)
        try:
            with storage.connect() as conn:
                conn.execute('UPDATE bid_catalog_chapters SET graphic_markdown = ? WHERE chapter_id = ?',('接入之前的正文',self.chapter));conn.commit()
        finally:
            reset_current_tenant(token)
        self.save('新正文')
        r=self.req('GET',self.base+'/compare?left=0&right=1')
        self.assertEqual(r.json()['data']['left'],'接入之前的正文')

    def test_empty_body_cannot_submit(self):
        self.assign()
        self.assertEqual(self.action('submit','writer').status_code,400)
        self.assertEqual(self.state()['revision'],1)

    def test_restore_cannot_bypass_approved_lock(self):
        self.assign();self.save('正文',1);self.action('submit','writer');self.action('approve','reviewer')
        self.assertEqual(self.req('POST',self.base+'/restore','manager',{'expected_revision':4,'revision':0}).status_code,409)

    def test_catalog_cannot_delete_approved_chapters(self):
        from app.services import bid_catalog_service
        self.assign();self.save('正文',1);self.action('submit','writer');self.action('approve','reviewer')
        token=set_current_tenant(self.tenant)
        try:
            with self.assertRaises(Exception):
                bid_catalog_service.delete_chapter(self.bid,self.chapter)
            with self.assertRaises(Exception):
                bid_catalog_service._clear_catalog_versions(self.bid)
            self.assertEqual(bid_graphic_service.get_graphic_section(self.bid,self.chapter)['markdown'],'正文')
        finally:
            reset_current_tenant(token)

    def test_same_numeric_ids_are_isolated_by_company(self):
        project=self.req('POST','/api/projects','outsider',{'settings':{'bidTitle':'其他公司项目'}}).json()
        user, company=auth_service.authenticate_token(self.tokens['outsider'])
        token=set_current_tenant(auth_service.build_tenant_context(user,company))
        try:
            with storage.connect() as conn:
                now=storage.now_utc().isoformat()
                version_id=conn.execute("INSERT INTO bid_catalog_versions (bid_id,version_name,version_style,catalog_content,create_time,update_time) VALUES (?,'其他目录','standard','[]',?,?)",(project['bidId'],now,now)).lastrowid
                chapter_id=conn.execute("INSERT INTO bid_catalog_chapters (bid_id,version_id,chapter_title,level,sort_order,create_time) VALUES (?,?,'其他章节',1,1,?)",(project['bidId'],version_id,now)).lastrowid
                conn.commit()
            self.assertEqual(chapter_id,self.chapter)
            self.assertEqual(project['bidId'],self.bid)
        finally:
            reset_current_tenant(token)
        self.assertEqual(self.save('A公司的正文').status_code,200)
        self.assertEqual(self.save('B公司的正文',user='outsider').status_code,200)
        self.assertEqual(self.req('GET',self.base+'/compare?left=0&right=1','writer').json()['data']['right'],'A公司的正文')
        self.assertEqual(self.req('GET',self.base+'/compare?left=0&right=1','outsider').json()['data']['right'],'B公司的正文')

    def test_streaming_conflict_preserves_newer_manual_save(self):
        token=set_current_tenant(self.tenant)
        async def tokens(*args,**kwargs):
            yield '旧生成'
            bid_graphic_service.save_graphic_section(self.bid,GraphicSectionSaveRequest(chapter_id=self.chapter,markdown='已保存的新内容',expected_revision=0))
            yield '结果'
        generation={'messages':[],'max_tokens':1200,'length_controlled':False,'existing_markdown':'','images':[],'chapter_path':['配送方案']}
        async def run():
            return [event async for event in bid_graphic_service.stream_graphic_section(self.bid,GraphicSectionGenerateRequest(chapter_id=self.chapter))]
        try:
            with patch.object(bid_graphic_service,'_build_graphic_generation',return_value=generation),patch.object(bid_graphic_service,'stream_chat_completion',side_effect=tokens),patch.object(bid_graphic_service,'_ensure_chapter_images',side_effect=lambda text,g:text):
                with self.assertRaises(Exception) as caught:
                    asyncio.run(run())
                self.assertIn('未覆盖',str(caught.exception))
            self.assertEqual(bid_graphic_service.get_graphic_section(self.bid,self.chapter)['markdown'],'已保存的新内容')
        finally:
            reset_current_tenant(token)

    def test_legacy_content_roundtrip_restore_and_conflict(self):
        from app.schemas.bid_content import ChapterContentSaveRequest
        from app.services import bid_content_service
        from app.api.routes.collaboration import Restore
        token=set_current_tenant(self.tenant)
        try:
            result=bid_content_service.save_chapter_content(self.bid,self.chapter,ChapterContentSaveRequest(content_text='旧正文接口内容',expected_revision=0))
            self.assertEqual(result['revision'],1)
            with self.assertRaises(Exception):
                bid_content_service.save_chapter_content(self.bid,self.chapter,ChapterContentSaveRequest(content_text='过期写入',expected_revision=0))
            chapter_collaboration.restore(self.bid,self.chapter,Restore(expected_revision=1,revision=0))
            chapter_collaboration.restore(self.bid,self.chapter,Restore(expected_revision=2,revision=1))
            self.assertEqual(bid_content_service.get_chapter_content(self.bid,self.chapter)['content_text'],'旧正文接口内容')
        finally:
            reset_current_tenant(token)

    def test_save_response_keeps_its_own_body_and_revision(self):
        token=set_current_tenant(self.tenant)
        original=bid_graphic_service.get_graphic_section
        intervened=False
        def read_after_concurrent_save(bid_id,chapter_id,**kwargs):
            nonlocal intervened
            if not intervened:
                intervened=True
                bid_graphic_service.save_graphic_section(bid_id,GraphicSectionSaveRequest(chapter_id=chapter_id,markdown='稍后保存的正文',expected_revision=1))
            return original(bid_id,chapter_id,**kwargs)
        try:
            with patch.object(bid_graphic_service,'get_graphic_section',side_effect=read_after_concurrent_save):
                first=bid_graphic_service.save_graphic_section(self.bid,GraphicSectionSaveRequest(chapter_id=self.chapter,markdown='先保存的正文',expected_revision=0))
            self.assertEqual((first['markdown'],first['revision']),('先保存的正文',1))
            latest=original(self.bid,self.chapter)
            self.assertEqual((latest['markdown'],latest['revision']),('稍后保存的正文',2))
        finally:
            reset_current_tenant(token)
