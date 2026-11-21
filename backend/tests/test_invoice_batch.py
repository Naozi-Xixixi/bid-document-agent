from __future__ import annotations

import gc
import io
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import app
from app.services import auth_service

TEXT = '''电子发票（增值税专用发票）
发票号码：26342000003323756566
开票日期：2026年10月20日
购 名称：某某科技有限公司 销 名称：某某代理事务所
信 统一社会信用代码/纳税人识别号：91000000MAK8FMRE9Y 信 统一社会信用代码/纳税人识别号：91000000MA2TBGFA81
项目名称 金 额 税率/征收率 税 额
服务费 1089.11 1% 10.89
合 计 ¥1089.11 ¥10.89
价税合计（大写）壹仟壹佰圆整 （小写）¥1100.00'''

class InvoiceBatchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        base = Path(__file__).resolve().parents[2] / '.cache' / 'invoice-batch-tests'; base.mkdir(parents=True,exist_ok=True)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=base))
        for key,value in dict(data_dir=Path(tmp),database_path=None,db_backend='sqlite',admin_initial_username='system_admin',admin_initial_password='test-password-1234').items():
            self.stack.enter_context(patch.object(settings,key,value))
        self.stack.enter_context(patch.object(auth_service,'_AUTH_SCHEMA_PATH',None))
        self.stack.enter_context(patch('app.api.routes.invoice._read_invoice_text', return_value=(TEXT,'pdf')))
        self.client = TestClient(app); self.addCleanup(lambda: (self.client.close(),gc.collect()))
        self.admin = self.login('system_admin','test-password-1234')
        self.users = {}
        for suffix in ('a','b'):
            company=self.req('POST','/api/auth/admin/companies',self.admin,json={'company_name':'发票公司'+suffix}).json()['data']
            user=self.req('POST','/api/auth/admin/users',self.admin,json={'username':'invoice_'+suffix,'password':'user-password-1234','display_name':suffix,'company_id':company['company_id']})
            self.assertEqual(user.status_code,200,user.text)
            self.users[suffix]=self.login('invoice_'+suffix,'user-password-1234')
    def login(self,username,password):
        r=self.client.post('/api/auth/login',json={'username':username,'password':password});self.assertEqual(r.status_code,200,r.text);return r.json()['data']['access_token']
    def req(self,method,url,token=None,**kwargs):
        return self.client.request(method,url,headers={'Authorization':'Bearer '+(token or self.users['a'])},**kwargs)
    def test_failed_ocr_can_retry_and_records_survive_new_client(self):
        with patch('app.api.routes.invoice._read_invoice_text', side_effect=RuntimeError('模拟识别失败')):
            created=self.req('POST','/api/invoice/batches',files=[('files',('retry.pdf',b'fake','application/pdf'))])
        bid=created.json()['data']['id']; base='/api/invoice/batches/'+bid
        first=self.req('GET',base).json()['data']['items'][0]
        self.assertEqual(first['ocr_status'],'failed');self.assertIn('模拟识别失败',first['error'])
        r=self.req('POST',base+'/resume');self.assertEqual(r.status_code,200,r.text)
        completed=self.req('GET',base).json()['data']['items'][0];self.assertEqual(completed['ocr_status'],'completed')
        client=TestClient(app)
        reopened=client.get(base,headers={'Authorization':'Bearer '+self.users['a']})
        self.assertEqual(reopened.json()['data']['items'][0]['result']['fields']['invoice_number'],'26342000003323756566')
        client.close()

    def test_batch_isolation_fields_evidence_history_retry_and_report(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/invoice/batches').status_code,401)
        r=self.req('POST','/api/invoice/batches',files=[('files',('invoice.pdf',b'fake pdf','application/pdf')),('files',('bad.exe',b'bad','application/octet-stream'))])
        self.assertEqual(r.status_code,200,r.text); bid=r.json()['data']['id']; base='/api/invoice/batches/'+bid
        batch=self.req('GET',base).json()['data']; self.assertEqual(batch['summary']['total'],2); self.assertEqual(batch['summary']['ocr_failed'],1)
        item=next(x for x in batch['items'] if x['filename']=='invoice.pdf'); self.assertEqual(item['ocr_status'],'completed')
        itempath=base+'/items/'+item['id']; rev=item['revision']
        self.assertEqual(self.req('GET',base,self.users['b']).status_code,404)
        self.assertEqual(self.req('GET',itempath+'/source',self.users['b']).status_code,404)
        self.assertEqual(self.req('POST',itempath+'/verification',data={'status':'passed','revision':str(rev)}).status_code,400)
        self.assertEqual(self.req('POST',itempath+'/verification',data={'status':'passed','revision':str(rev)},files={'evidence':('result.png',b'not an image','image/png')}).status_code,400)
        img=io.BytesIO(); Image.new('RGB',(20,20),'white').save(img,'PNG'); data=img.getvalue()
        r=self.req('POST',itempath+'/verification',data={'status':'passed','revision':str(rev),'note':'人工查看官网'},files={'evidence':('result.png',data,'image/png')})
        self.assertEqual(r.status_code,200,r.text); verified=r.json()['data']; self.assertEqual(verified['verification_status'],'passed'); self.assertEqual(verified['verified_by'],'invoice_a');self.assertTrue(verified['verified_at']);self.assertTrue(verified['has_evidence'])
        self.assertEqual(self.req('GET',itempath+'/evidence').content,data)
        self.assertEqual(self.req('GET',itempath+'/evidence',self.users['b']).status_code,404)
        self.assertEqual(self.req('PUT',itempath+'/fields',json={'revision':rev,'fields':{'title':'stale'}}).status_code,409)
        r=self.req('PUT',itempath+'/fields',json={'revision':verified['revision'],'fields':{'invoice_number':'99999999','title':'=formula'}})
        self.assertEqual(r.status_code,200,r.text); changed=r.json()['data'];self.assertEqual(changed['verification_status'],'manual_pending');self.assertFalse(changed['has_evidence'])
        history=self.req('GET',itempath+'/history').json()['data']; old=next(h for h in history if h['action']=='manual_verification');self.assertEqual(old['snapshot']['verification_status'],'passed')
        self.assertEqual(self.req('GET',itempath+'/history/'+old['id']+'/evidence').content,data)
        self.assertEqual(self.req('GET',itempath+'/history/'+old['id']+'/evidence',self.users['b']).status_code,404)
        csv=self.req('GET',base+'/report.csv').content.decode('utf-8-sig');self.assertIn("'=formula",csv);self.assertIn('待人工查验',csv)
        self.assertEqual(self.req('POST',itempath+'/retry').status_code,409)
        bad=next(x for x in batch['items'] if x['filename']=='bad.exe');self.assertEqual(self.req('POST',base+'/items/'+bad['id']+'/retry').status_code,400)
        self.assertEqual(self.req('GET',itempath+'/source').content,b'fake pdf')

if __name__ == '__main__': unittest.main()
