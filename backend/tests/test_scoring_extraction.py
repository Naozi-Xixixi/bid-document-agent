"""Deterministic scoring regressions, including real tender table excerpts."""
from pathlib import Path
import pytest
from app.utils.bid_extractor.scoring_extractor import extract_scoring, _extract_score

@pytest.mark.parametrize("text",[
    "网上询问截止时间：2026年10月22日17时30分",
    "响应文件提交截止时间后30分钟内",
    "价格100元30分", "会议于9:30分开始",
])
def test_time_and_money_are_not_scores(text):
    assert _extract_score(text) is None
    assert extract_scoring(text)["items"] == []

def test_time_in_legitimate_criterion_preserved_without_becoming_score():
    text = "评分标准\n服务承诺：15分钟内响应、30分钟内到达得3分"
    assert _extract_score(text) == 3
    items=extract_scoring(text)["items"]
    assert items and all(i["score"] == 3 for i in items)

@pytest.mark.parametrize("sample,expected",[
 (0,{"食材质量管控方案":5,"重难点解决方案":5,"专项服务方案":5,"追溯保障方案":5,
     "企业内部管理制度方案":5,"食材存储方案":5,"仓储管理方案":5,"配送服务方案与应急预案":5,
     "一体化服务平台":7,"供货渠道":8,"体系认证":6,"人员配备":6,"仓储能力":5,"供应商业绩":10,"客户反馈":8,"价格评分":10}),
 (1,{"投标人供货业绩":9,"运输车辆":6,"食品安全检测":6,"投标人荣誉":6,"人员配备":4,"企业实力":7,
     "公司管理制度":6,"配送服务能力":8,"应急处理方案":10,"食材管理方案":8,"投标综合费率得分":30}),
 (2,{"体系认证":6,"投标人业绩":9,"履约反馈":9,"车辆配备情况":3,"服务承诺":3,"配送方案":10,
     "质量控制方案":10,"应急实施方案":10,"食材可追溯体系":10,"售后服务方案":10,"价格评分":20}),
])
def test_real_tender_rows_and_totals(sample,expected):
    text=(Path(__file__).parent/'fixtures'/'scoring'/f'sample-{sample}.md').read_text(encoding='utf8')
    result=extract_scoring(text)
    assert {i["title"]:i["score"] for i in result["items"]} == expected
    assert result["scoring_structure"]["total_score"] == 100
    assert all(i["source_page"] and i["source_text"] for i in result["items"])
    assert any(i['category']=='价格' for i in result['items'])
    assert result['requires_review'] is True

def test_continuation_keeps_title_criteria_and_page_range():
    text = """<!-- 第4页 -->
| 类别 | 评分内容 | 评分标准 | 分值范围 |
| --- | --- | --- | --- |
| 技术 | 食材质量管控 | 方案，得5分； | 0-5分 |
<!-- 第5页 -->
### 表格 5-1
| | 方案 | 未提供不得分；提供扫描件。 | |
| --- | --- | --- | --- |
| | 服务承诺 | 15分钟响应得3分 | 0～3分 |
# 第六章 合同
| 其他项目 | 时间 | 30分 | 10分 |
"""
    items=extract_scoring(text)['items']
    assert len(items)==2
    assert items[0]['title']=='食材质量管控方案'
    assert items[0]['source_page']==4 and items[0]['source_page_end']==5
    assert '未提供不得分' in items[0]['text']
    assert items[1]['score']==3

def test_columns_may_be_reordered_and_range_uses_maximum():
    text="""| 满分 | 评审要素 | 评分标准 |
| --- | --- | --- |
| 0-6分 | 体系认证 | 每证2分，最多6分 |
"""
    items=extract_scoring(text)['items']
    assert [(i['title'],i['score']) for i in items]==[('体系认证',6)]

def test_toc_and_incidental_reference_do_not_capture_deadlines():
    text="""目录
第五章 评标办法 .... 22
评审方法：综合评分法
网上询问截止时间17时30分
响应文件解密时间30分钟
# 第五章 评标办法
评分标准
配送方案 10分
# 第六章 合同
违约金每分钟30元
"""
    items=extract_scoring(text)['items']
    assert items and all(i['score']==10 for i in items)


def test_ai_chinese_keys_and_score_ranges_are_normalized():
    from app.utils.bid_extractor.ai_extractor import _dedupe_items
    item = _dedupe_items([{"指标":"运输车辆","分值范围":"0-6分","评审标准":"每辆3分"}])[0]
    assert item['title'] == '运输车辆'
    assert item['criteria'] == '每辆3分'
    assert item['score'] == 6


def test_ai_summary_cannot_drop_rows_from_complete_source_table():
    from app.utils.bid_extractor import extract_bid_info
    from app.utils.bid_extractor.ai_extractor import _normalize_final_result
    text=(Path(__file__).parent/'fixtures'/'scoring'/'sample-1.md').read_text(encoding='utf8')
    result=_normalize_final_result({}, {'tech_scoring_items':[{'指标':'运输车辆','分值范围':'0-5分','评审标准':'错误摘要'}]}, extract_bid_info(text), True, [])
    rows=result['tech_scoring_items']+result['biz_scoring_items']
    assert len(rows)==11
    assert sum(row['score'] for row in rows)==100
    assert next(row for row in rows if row['title']=='运输车辆')['score']==6
    assert all(row.get('source_text') for row in rows)
    assert '未命名条目' not in result['parsed_score_requirements']
    assert '投标综合费率得分' in result['parsed_score_requirements']


def test_ai_summary_keeps_category_rows_and_separate_price_items():
    from app.utils.bid_extractor.ai_extractor import _normalize_final_result
    categories={'scoring':{'tech_scoring_items':[{'title':'方案','score':60},{'title':'服务','score':10}], 'price_scoring_items':[{'title':'价格评分','score':30}]}}
    result=_normalize_final_result(categories,{'tech_scoring_items':[{'title':'方案','score':60}]},{},True,[])
    rows=result['tech_scoring_items']+result['biz_scoring_items']
    assert {i['title']:i['score'] for i in rows}=={'方案':60,'服务':10,'价格评分':30}


def test_rules_only_preserves_all_source_scoring_rows():
    from app.utils.bid_extractor import extract_bid_info
    from app.utils.bid_extractor.ai_extractor import _fallback_from_rules
    text=(Path(__file__).parent/'fixtures'/'scoring'/'sample-1.md').read_text(encoding='utf8')
    result=_fallback_from_rules(extract_bid_info(text),{})
    rows=result['tech_scoring_items']+result['biz_scoring_items']
    assert len(rows)==11 and sum(i['score'] for i in rows)==100


def test_no_score_document_cannot_be_promoted_to_scoring_fallback():
    from app.utils.bid_extractor import extract_bid_info
    from app.utils.bid_extractor.ai_extractor import _fallback_from_rules
    rule=extract_bid_info('项目名称：配送项目\n截止时间17时30分，开标后30分钟内解密。')
    assert rule['score_requirements']==''
    result=_fallback_from_rules(rule,{})
    assert result['tech_scoring_items']==[]
    assert result['biz_scoring_items']==[]
