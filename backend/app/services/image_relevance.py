"""Content checks shared by automatic image retrieval and selection.

A document's title/category is not evidence that each extracted image is a scene.
"""
import re

TOPICS = (
    ('冷链', '冷藏', '冷库', '温控', '保鲜'),
    ('配送', '物流', '运输', '装车', '车辆'),
    ('仓储', '仓库', '分拣', '入库', '库存'),
    ('检测', '检验', '验收', '质量', '食品安全', '追溯'),
    ('采购', '供应', '基地', '原材料', '食材'),
)
DOCUMENT = re.compile(r'营业执照|许可证|身份证|健康证|检验报告|检测报告|认证证书|认证产品|认证评价|评价机构|企业/组织名称|获奖名单|资质认定|发票|统一社会信用代码|法定代表人|投标报价|授权委托|违约责任|不可抗力|甲方|乙方|第[一二三四五六七八九十]+条|签订合同')


def own_text(asset):
    # OCR belongs to the image. annotation/source_chapter/item_title can be
    # synthesized from the entire bid and must not establish visual relevance.
    return re.sub(r'\s+', '', str(asset.get('ocr_text') or '')).lower()


def is_document_scan(asset):
    text = own_text(asset)
    return bool(DOCUMENT.search(text) or asset.get('contract_info') or
                re.search(r'证书|执照|发票|身份证|健康证|合同|行驶证', str(asset.get('asset_sub_type') or '')))


def content_matches(asset, query):
    text = own_text(asset)
    if not text:
        return []
    return [list(group) for group in TOPICS if any(t in query for t in group) and any(t in text for t in group)]


def is_case_illustration(asset, query):
    text = own_text(asset)
    if re.search(r'证件|证书|证明|执照|许可证|发票|合同|授权|报价|业绩|报告|人员|团队|岗位|组织架构', query):
        return False
    if text.startswith('目录') or ('食品安全事故' in text and '事故' not in query):
        return False
    if is_document_scan(asset) or len(text) > 650 or text.count('有限公司') >= 3 or re.search(r'[1-5一二三四五]a级物流企业|企业名单|企业名录', text):
        return False
    # Positive, image-local evidence is required. Generic category labels or
    # the source document's project name never qualify a historical image.
    if re.search(r'http[s]?://|网址导航|登录|新建文件夹', text):
        return False
    if re.search(r'发展历程|注册资本|荣获|入选|201[0-9]|202[0-9]', text) and not re.search(r'企业简介|发展历程|公司简介', query):
        return False
    width, height = asset.get('image_width') or 0, asset.get('image_height') or 0
    if height and width / height < 1.2 and len(text) > 120:
        return False
    focuses = (
        (('天气', '灾害', '暴雨', '台风'), ('天气', '灾害', '暴雨', '台风', '防汛')),
        (('食品安全事故',), ('食品安全事故', '食物中毒', '封存', '召回', '救治')),
        (('应急', '突发', '急件'), ('应急', '突发', '急件', '备用', '紧急')),
        (('争议',), ('争议', '纠纷', '协商')),
        (('考核', '续签', '评估'), ('考核', '续签', '评估', '评价')),
        (('溯源', '追溯'), ('溯源', '追溯', '批次')),
        (('污染',), ('污染', '消毒', '清洁', '卫生')),
        (('留样',), ('留样',)),
        (('投诉',), ('投诉', '意见', '反馈')),
        (('冷链', '温控', '保鲜'), ('冷链', '温控', '冷藏', '冷库', '保鲜')),
    )
    if any(any(t in query for t in required) and not any(t in text for t in evidence) for required, evidence in focuses):
        return False
    topics = content_matches(asset, query)
    image_topics = sum(any(t in text for t in group) for group in TOPICS)
    return bool(topics and image_topics >= 2 and len(text) >= 15)
