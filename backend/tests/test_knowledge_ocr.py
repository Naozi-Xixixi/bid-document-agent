import asyncio
from contextlib import closing
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_knowledge_reliability as fixture
from app.modules.knowledge import service, local_ocr, ocr_chunks, document_analysis
from app.services.qwen_client import LLMResult
from app.modules.knowledge.schemas import KnowledgeSearchRequest


class OCRPipelineTests(unittest.TestCase):
    setUp = fixture.KnowledgeInterpretationTests.setUp
    connect = fixture.KnowledgeInterpretationTests.connect
    saved = fixture.KnowledgeInterpretationTests.saved

    def asset(self, text='', status='not_started'):
        path = self.path.parent / 'scan.png'
        path.write_bytes(b'test-only-image')
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET status='completed'")
            conn.execute("INSERT INTO knowledge_asset (asset_id,item_id,resource_path,asset_type,page_num,deep_candidate,deep_status,ocr_text,ocr_status,create_time) VALUES (1,1,?,'图片',9,1,'pending',?,?,'x')", (str(path),text,status))
            conn.commit()
            return conn.execute("SELECT a.*,i.content_text,i.source_name FROM knowledge_asset a JOIN knowledge_item i ON a.item_id=i.item_id").fetchone()

    def test_unavailable_engine_is_failure_not_completed(self):
        row = self.asset()
        with patch.object(local_ocr,'recognize_cached',side_effect=RuntimeError('OCR unavailable')):
            with self.assertRaises(RuntimeError): service._reextract_one_asset(row,only_missing=False)
        with closing(self.connect()) as conn:
            row=conn.execute('SELECT ocr_status,ocr_error FROM knowledge_asset').fetchone()
            self.assertEqual(row['ocr_status'],'failed')
            self.assertIn('unavailable',row['ocr_error'])

    def test_no_text_is_explicit_and_excluded_from_text_index(self):
        row=self.asset()
        with patch.object(local_ocr,'recognize_cached',return_value=local_ocr.OCRResult('','no_text')), patch.object(service,'_image_profile',return_value={}):
            service._reextract_one_asset(row,only_missing=False)
        ocr_chunks.refresh_ocr_chunks(1)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT ocr_status FROM knowledge_asset').fetchone()[0],'no_text')
            self.assertEqual(conn.execute("SELECT count(*) FROM knowledge_chunk WHERE chunk_kind='ocr_text'").fetchone()[0],0)

    def test_ocr_chunks_retrievable_sourced_and_stable(self):
        self.asset('扫描独有冷链证明冷藏温度零至四度。'*100,'recognized')
        ocr_chunks.refresh_ocr_chunks(1)
        with closing(self.connect()) as conn:
            before=[dict(r) for r in conn.execute("SELECT * FROM knowledge_chunk WHERE chunk_kind='ocr_text'")]
        self.assertGreater(len(before),1)
        self.assertTrue(all(r['page_number']==9 and r['source_asset_id']==1 for r in before))
        with patch.object(service.vector_store,'enabled',return_value=False), patch.object(service.vector_store,'status',return_value={'vector_backend':'none','vector_store_enabled':False,'embedding_provider':'local'}):
            result=service.search(KnowledgeSearchRequest(query='扫描独有冷链证明',item_id=1,chunk_kind=['ocr_text'],include_images=False))
        self.assertTrue(result['results'])
        self.assertEqual(result['results'][0]['source_asset_id'],1)
        ocr_chunks.refresh_ocr_chunks(1)
        with closing(self.connect()) as conn:
            after=[dict(r) for r in conn.execute("SELECT * FROM knowledge_chunk WHERE chunk_kind='ocr_text'")]
        self.assertEqual(before,after)

    def test_added_ocr_is_analyzed_without_repeating_original_batches(self):
        self.model.return_value=LLMResult('{"summary":"正文完成"}','qwen','test-model',True)
        with patch.object(service.qwen_client,'selected_model',return_value='test-model'):
            asyncio.run(service.interpret_knowledge_item(1,role='bid'))
            self.asset('仅在扫描件中的特殊履约证明。'*60,'recognized')
            ocr_chunks.refresh_ocr_chunks(1)
            self.model.reset_mock()
            self.model.return_value=LLMResult('{"summary":"扫描文字完成"}','qwen','test-model',True)
            result=asyncio.run(service.interpret_knowledge_item(1,role='bid'))
        self.assertEqual(self.model.await_count,1)
        self.assertIn('仅在扫描件',self.model.await_args.args[0][1]['content'])
        self.assertIn('正文完成',result['analysis']['summary'])
        self.assertIn('扫描文字完成',result['analysis']['summary'])
        self.assertEqual(result['coverage']['percent'],100)

    def test_visually_similar_but_different_source_images_are_not_replaced(self):
        original = self.path.parent / 'original.png'
        original.write_bytes(b'original document')
        new = self.path.parent / 'new.png'
        new.write_bytes(b'different document')
        with patch.object(service,'_find_duplicate_asset',return_value={'asset_id':99,'resource_path':str(original)}):
            self.assertTrue(service._save_image_asset(1,new,'new.png',profile={'hash':'same','width':1000,'height':1000},defer_deep=True,deep_candidate=True))
        with closing(self.connect()) as conn:
            asset=conn.execute('SELECT resource_path,duplicate_of_asset_id FROM knowledge_asset').fetchone()
        self.assertIsNone(asset['duplicate_of_asset_id'])
        self.assertEqual(service._resolve_stored_path(asset['resource_path']),new)
        self.assertTrue(new.exists())

    def test_unrelated_document_facts_do_not_leak_into_image(self):
        row=dict(self.asset())
        row['content_text']='健康证 姓名：张三 身份证号：110000199001010011 有效期：2027-01-01'
        row['ai_description']='健康证 姓名：张三'
        row['annotation']='姓名：张三'
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_asset SET person_info=?,employee_info=?,contract_info=?", ('{"name":"张三"}', '{"name":"张三"}', '{"party_a":"旧甲方"}'))
            conn.commit()
        with patch.object(local_ocr,'recognize_cached',return_value=local_ocr.OCRResult('冷藏库温度记录：2摄氏度','recognized')), patch.object(service,'_image_profile',return_value={}):
            service._reextract_one_asset(row,only_missing=False)
        with closing(self.connect()) as conn:
            result=dict(conn.execute('SELECT person_info,employee_info,contract_info,structured_info,annotation FROM knowledge_asset').fetchone())
        self.assertNotIn('张三',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('旧甲方',json.dumps(result,ensure_ascii=False))
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET content_text='健康证 姓名：张三'");conn.commit()
        with patch.object(service.vector_store,'delete_knowledge_chunks'):
            service._refresh_asset_annotation_chunks(1,False)
        with closing(self.connect()) as conn:
            annotations=''.join(r[0] for r in conn.execute("SELECT content FROM knowledge_chunk WHERE chunk_kind='image_annotation'"))
        self.assertNotIn('张三',annotations)

    def test_embedded_image_does_not_hide_full_scan_page(self):
        self.asset()
        pdf=self.path.parent / 'scan.pdf'
        doc=service.fitz.open()
        for _ in range(9):doc.new_page(width=200,height=200)
        doc.save(str(pdf));doc.close()
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET original_file_path=?,deep_candidate_pages_json='[9]'",(str(pdf),));conn.commit()
        self.assertEqual(service._materialize_scan_page_assets(1),1)
        self.assertEqual(service._materialize_scan_page_assets(1),0)
        with closing(self.connect()) as conn:
            row=conn.execute("SELECT page_num,deep_status FROM knowledge_asset WHERE source_chapter LIKE '扫描页全文%'").fetchone()
        self.assertEqual(tuple(row),(9,'pending'))

    def test_unverified_original_stops_ocr(self):
        self.asset()
        with patch('app.modules.knowledge.ocr_sources.restore_pdf_sources',side_effect=RuntimeError('ambiguous original')), patch.object(local_ocr,'recognize_cached') as recognize:
            result=asyncio.run(service.deep_extract_item(1))
        self.assertEqual(result['failed'],1)
        recognize.assert_not_called()
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT deep_status FROM knowledge_item').fetchone()[0],'failed')

    def test_retry_preserves_already_recognized_assets(self):
        self.asset('已识别','recognized')
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_asset SET deep_status='completed'");conn.commit()
        service.prepare_deep_extract(1)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT deep_status FROM knowledge_asset').fetchone()[0],'completed')

    def mark_complete(self, vector_status='completed'):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_asset SET deep_status='completed'")
            conn.execute("UPDATE knowledge_item SET deep_status='completed',deep_progress=100,deep_error='',vector_status=?",(vector_status,))
            conn.commit()

    def test_repeated_completed_requests_do_not_mutate_or_schedule(self):
        self.asset('已识别','recognized')
        self.mark_complete()
        with closing(self.connect()) as conn:
            before=list(conn.iterdump())
        with patch.object(service,'_queue_deep_extract') as deep, patch.object(service,'_queue_item_vector_sync') as vector:
            for _ in range(2):
                result=service.queue_deep_extract(1)
                self.assertFalse(result['queued'])
                self.assertEqual(result['action'],'already_complete')
            deep.assert_not_called(); vector.assert_not_called()
        with closing(self.connect()) as conn:
            self.assertEqual(before,list(conn.iterdump()))

    def test_completed_ocr_does_not_dirty_an_active_index(self):
        self.asset('已识别','recognized')
        self.mark_complete('indexing')
        with patch.object(service,'_queue_deep_extract') as deep, patch.object(service,'_queue_item_vector_sync') as vector:
            result=service.queue_deep_extract(1)
            self.assertFalse(result['queued'])
            self.assertIn('索引正在处理中',result['message'])
            deep.assert_not_called(); vector.assert_not_called()

    def test_failed_index_retry_does_not_repeat_ocr(self):
        self.asset('已识别','recognized')
        self.mark_complete('failed')
        with patch.object(service,'_queue_deep_extract') as deep, patch.object(service,'_queue_item_vector_sync',return_value=True) as vector:
            result=service.queue_deep_extract(1)
            self.assertEqual(result['action'],'index_retry')
            deep.assert_not_called(); vector.assert_called_once_with(1)

    def test_missing_scan_page_is_not_mistaken_for_completion(self):
        self.asset('已识别内嵌图','recognized')
        self.mark_complete()
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET file_type='pdf',deep_candidate_pages_json='[9]'");conn.commit()
        with patch.object(service,'_queue_deep_extract',return_value=True) as deep:
            result=service.queue_deep_extract(1)
            self.assertTrue(result['queued']);deep.assert_called_once_with(1)

    def test_failed_ocr_is_still_retried(self):
        self.asset('','failed')
        self.mark_complete()
        with patch.object(service,'_queue_deep_extract',return_value=True) as deep:
            result=service.queue_deep_extract(1)
            self.assertTrue(result['queued']);deep.assert_called_once_with(1)

    def test_docx_original_restoration_and_repeated_materialization(self):
        import io,zipfile
        from PIL import Image
        from app.modules.knowledge.ocr_sources import restore_docx_sources
        self.asset()
        buffer=io.BytesIO();Image.new('RGB',(400,400),'blue').save(buffer,format='PNG');content=buffer.getvalue()
        profile=service._image_profile_from_bytes(content)
        original=self.path.parent/'source.docx'
        with zipfile.ZipFile(original,'w') as archive:
            archive.writestr('word/media/',b'')
            archive.writestr('word/media/image1.png',content)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET original_file_path=?,file_type='docx'",(str(original),))
            conn.execute("UPDATE knowledge_asset SET duplicate_of_asset_id=99,image_hash=?,image_width=?,image_height=?",(profile['hash'],profile['width'],profile['height']));conn.commit()
        with patch.object(service,'_docx_media_contexts',return_value={}):
            self.assertEqual(restore_docx_sources(1),1)
            self.assertEqual(restore_docx_sources(1),0)
            self.assertEqual(service._materialize_docx_images(1),0)
            self.assertEqual(service._materialize_docx_images(1),0)
        with closing(self.connect()) as conn:
            rows=conn.execute('SELECT resource_path,duplicate_of_asset_id FROM knowledge_asset').fetchall()
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]['duplicate_of_asset_id'])
        self.assertEqual(service._resolve_stored_path(rows[0]['resource_path']).read_bytes(),content)

    def test_docx_full_sequence_disambiguates_similar_reports(self):
        import zipfile
        from app.modules.knowledge.ocr_sources import restore_docx_sources
        self.asset()
        original=self.path.parent/'ordered.docx'
        with zipfile.ZipFile(original,'w') as archive:
            archive.writestr('word/media/image1.png',b'one');archive.writestr('word/media/image2.png',b'two')
        anchor=self.path.parent/'docx_image2.png';anchor.write_bytes(b'two')
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET original_file_path=?,file_type='docx'",(str(original),))
            conn.execute("UPDATE knowledge_asset SET duplicate_of_asset_id=99,image_hash='same',image_width=10,image_height=10")
            conn.execute("INSERT INTO knowledge_asset (asset_id,item_id,resource_path,asset_type,image_hash,image_width,image_height,create_time) VALUES (2,1,?,'图片','same',10,10,'x')",(str(anchor),));conn.commit()
        with patch.object(service,'_docx_media_contexts',return_value={}), patch.object(service,'_image_profile_from_bytes',return_value={'hash':'same','width':10,'height':10}):
            self.assertEqual(restore_docx_sources(1),1)
        with closing(self.connect()) as conn:path=conn.execute('SELECT resource_path FROM knowledge_asset WHERE asset_id=1').fetchone()[0]
        self.assertEqual(service._resolve_stored_path(path).read_bytes(),b'one')

    def test_docx_ambiguous_original_is_not_guessed(self):
        import zipfile
        from app.modules.knowledge.ocr_sources import restore_docx_sources
        self.asset()
        original=self.path.parent/'ambiguous.docx'
        with zipfile.ZipFile(original,'w') as archive:
            archive.writestr('word/media/image1.png',b'one');archive.writestr('word/media/image2.png',b'two')
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET original_file_path=?,file_type='docx'",(str(original),))
            conn.execute("UPDATE knowledge_asset SET duplicate_of_asset_id=99,image_hash='same',image_width=10,image_height=10");conn.commit()
        with patch.object(service,'_docx_media_contexts',return_value={}), patch.object(service,'_image_profile_from_bytes',return_value={'hash':'same','width':10,'height':10}):
            with self.assertRaisesRegex(RuntimeError,'无法唯一匹配'):restore_docx_sources(1)
        with closing(self.connect()) as conn:self.assertEqual(conn.execute('SELECT duplicate_of_asset_id FROM knowledge_asset').fetchone()[0],99)


class LocalOCRTests(unittest.TestCase):
    def test_worker_crash_and_timeout_are_explicit_failures(self):
        import subprocess
        from types import SimpleNamespace
        with patch.object(subprocess,"run",return_value=SimpleNamespace(returncode=1,stdout="",stderr="native failure")):
            with self.assertRaisesRegex(RuntimeError,"异常退出"):local_ocr.recognize_isolated(Path("unused"))
        with patch.object(subprocess,"run",side_effect=subprocess.TimeoutExpired("ocr",120)):
            with self.assertRaisesRegex(RuntimeError,"120 秒"):local_ocr.recognize_isolated(Path("unused"))

    def test_empty_result_differs_from_engine_error(self):
        with patch.object(local_ocr,'_engine',return_value=(None,None)):
            self.assertEqual(local_ocr.recognize(Path('unused')).status,'no_text')
        with patch.object(local_ocr,'_engine',side_effect=RuntimeError('broken')):
            with self.assertRaisesRegex(RuntimeError,'执行失败'): local_ocr.recognize(Path('unused'))
