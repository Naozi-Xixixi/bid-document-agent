import pytest
from contextlib import ExitStack
from generation_fixtures import seed_generation_db

@pytest.fixture(autouse=True)
def isolated_database():
    with ExitStack() as stack:
        seed_generation_db(stack)
        yield

from unittest.mock import patch

from app.services.bid_parse_service import _extract_project_number
from app.services.bid_graphic_service import _ensure_chapter_images


def test_project_number_comes_from_tender_and_rejects_ambiguous_text():
    assert _extract_project_number({}, "项目编号：2024AYYWQ00011\n项目编号：2024AYYWQ00011") == "2024AYYWQ00011"
    assert _extract_project_number({}, "项目编号：A1234\n项目编号：B1234") == ""
    assert _extract_project_number({}, "没有项目编号") == ""


def test_selected_historical_images_are_inserted_with_reference_warning():
    image = {"asset_id": 12, "item_id": 6, "asset_sub_type": "冷库仓储", "page_num": 477}
    generation = {"images": [image], "selected_asset_ids": [12], "image_strategy": "rag_auto"}
    with patch("app.services.bid_case_reference_service.historical_case_item_ids", return_value={6}):
        result = _ensure_chapter_images("正文", generation)
        assert "[[KNOWLEDGE_IMAGE:12]]" in result
        assert "历史案例图片，仅供参考" in result
        assert _ensure_chapter_images(result, generation).count("[[KNOWLEDGE_IMAGE:12]]") == 1


def test_auto_insert_respects_disabled_strategy_and_does_not_use_unmatched_images():
    image = {"asset_id": 12, "item_id": 7, "match_scope": "group"}
    assert _ensure_chapter_images("正文", {"images": [image], "image_strategy": "none"}) == "正文"
    assert _ensure_chapter_images("正文", {"images": [image], "image_strategy": "rag_auto"}) == "正文"
