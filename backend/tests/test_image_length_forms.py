import asyncio
import base64
import json
from unittest.mock import patch
import pytest
from app.services import image_relevance as images, bid_graphic_service as graphic, tender_form_service as forms
from app.schemas.bid_graphic import GraphicSectionGenerateRequest


def test_image_local_content_overrides_false_scene_label():
    asset={'asset_sub_type':'配送现场','ocr_text':'第十一条 反商业贿赂。乙方在甲方配送现场承担违约责任。','annotation':'食材配送质量现场'}
    assert images.is_document_scan(asset)
    assert images.is_document_scan({'ocr_text':'序号 地区 企业/组织名称 认证产品/范围 认证评价机构 冷链配送食品质量'})
    assert not images.is_case_illustration(asset,'配送质量')
    assert not images.is_case_illustration({'asset_sub_type':'配送现场','annotation':'配送现场 食材质量'},'配送质量')
    assert images.is_case_illustration({'ocr_text':'优质基地 原材料 冷链运输 配送中心 客户 食品安全 质量追溯管理体系','image_width':1400,'image_height':400},'冷链配送')
    assert not images.is_case_illustration({'ocr_text':'企业发展历程 2022年注册资本8000万元，配送服务网点350个，质量追溯体系'},'冷链配送')


def test_visible_length_ignores_markup_and_counts_text():
    assert graphic._graphic_word_count('# 标题\n\n| 名称 | 内容 |\n| --- | --- |\n| 食材 | **配送** |\n\n[[KNOWLEDGE_IMAGE:12345]]')==10


def test_default_limit_revises_and_reports_final_count():
    generation={'target_words':200,'chapter':{'chapter_title':'服务方案'},'context':{},'images':[],'chapter_path':'服务方案','image_strategy':'none'}
    async def stream(*args,**kwargs):yield '逐批验收并记录。'*8
    async def run():
        with patch.object(graphic,'stream_chat_completion',stream):
            text,metric=await graphic._adjust_graphic_length('逐批验收并记录。'*40,generation,GraphicSectionGenerateRequest(chapter_id=1,word_limit=200))
        assert graphic._graphic_word_count(text)<=200
        assert metric['mode']=='upper_limit' and metric['adjustment_attempts']==1
    asyncio.run(run())


def test_cannot_fit_length_does_not_silently_claim_success():
    generation={'target_words':200,'chapter':{'chapter_title':'服务方案'},'context':{},'images':[],'chapter_path':'服务方案','image_strategy':'none'}
    async def stream(*args,**kwargs):yield '逐批验收并记录。'*40
    async def run():
        with patch.object(graphic,'stream_chat_completion',stream), pytest.raises(graphic.BidGraphicServiceError,match='原正文已保留'):
            await graphic._adjust_graphic_length('逐批验收并记录。'*40,generation,GraphicSectionGenerateRequest(chapter_id=1,word_limit=200))
    asyncio.run(run())


def test_pdf_grid_retains_empty_price_columns_and_merged_cells():
    import pymupdf
    doc=pymupdf.open();page=doc.new_page()
    # Two header columns merged across the first two physical columns.
    for y in (100,140,190):page.draw_line((50,y),(450,y))
    for x in (50,300,450):page.draw_line((x,100),(x,190))
    page.draw_line((180,140),(180,190))
    page.insert_text((60,125),'Products');page.insert_text((310,125),'Price')
    page.insert_text((60,170),'Meat');page.insert_text((190,170),'kg')
    grids=list(forms.pdf_grids(page));assert len(grids)==1
    grid=grids[0];assert len(grid['widths'])==3
    assert any(c['colspan']==2 for c in grid['cells'])
    assert grid['rows'][1][2]==''
    assert 'TENDER_TABLE:' in forms._markdown_table(grid)
    doc.close()


def test_blank_source_quote_is_still_flagged_for_review():
    from app.services.bid_draft_guard import audit_chapter_text
    result=audit_chapter_text(1,'开标一览表','| 投标综合费率报价 |  |')
    assert any(r['rule']=='missing_bid_price' for r in result)


def test_catalog_does_not_split_required_quote_or_response_forms():
    from app.services.bid_catalog_service import _align_catalog_to_tender_format
    titles=['开标一览表','投标响应表','投标货物及报价表','服务方案']
    nodes=[{'title':t,'children':[{'title':'模型自创小节','children':[]}]} for t in titles]
    aligned=_align_catalog_to_tender_format(nodes,titles)
    assert all(not n['children'] for n in aligned[:3])
    assert aligned[3]['children']


def test_supplementary_quote_instruction_does_not_replace_chapter_topic():
    from unittest.mock import MagicMock
    conn=MagicMock()
    conn.execute.return_value.fetchall.return_value=[{'chapter_id':1,'parent_id':None,'level':1,'chapter_title':'冷链配送方案'}]
    with patch.object(graphic.storage,'connect') as connect:
        connect.return_value.__enter__.return_value=conn
        result=graphic._chapter_semantic_context(1,1,'未经确认的报价必须留空')
    assert result['current_title']=='冷链配送方案'
    assert '报价' not in result['image_query']
    assert '报价' in result['query']


def test_company_directory_is_not_logistics_illustration():
    assert not images.is_case_illustration({'ocr_text':'4A级物流企业（134家）：甲物流有限公司乙供应链有限公司丙物流有限公司','image_width':866,'image_height':706},'物流保障体系')


def test_specific_workflow_needs_specific_image_evidence():
    asset={'ocr_text':'采购产品进行质量检测，合格货品装车配送，客户现场验收','image_width':1200,'image_height':500}
    assert images.is_case_illustration(asset,'配送质量管理流程')
    assert not images.is_case_illustration(asset,'急件采购应急方案')
    assert not images.is_case_illustration(asset,'配合采购人考核续签措施')
    assert not images.is_case_illustration(asset,'运输过程防二次污染措施')


def test_normal_delivery_flow_is_not_food_incident_response():
    assert not images.is_case_illustration({'ocr_text':'采购货物质量检查与配送，处理驾驶突发情况，货物迟到进行处理','image_width':1200,'image_height':500},'食品安全事故解决方案')


def test_quality_word_does_not_make_weather_response_match():
    assert not images.is_case_illustration({'ocr_text':'采购入库质量检测与配送验收管理流程','image_width':1200,'image_height':500},'质量回访与灾害天气应对措施')


def test_semantic_gate_rejects_unknown_ids_and_failed_provider():
    from types import SimpleNamespace
    candidates=[{'asset_id':7,'ocr_text':'冷链运输温控管理流程'}]
    for content,used in [('{"asset_id":999}',True),('{"asset_id":7}',False),('not json',True)]:
        with patch.object(graphic,'chat_completion',return_value=SimpleNamespace(content=content,usedApi=used,error=None)):
            assert graphic._select_case_image_semantically('冷链温控',candidates) is None
    with patch.object(graphic,'chat_completion',return_value=SimpleNamespace(content='{"asset_id":7}',usedApi=True,error=None)):
        assert graphic._select_case_image_semantically('冷链温控',candidates)==7
