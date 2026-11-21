import asyncio
from unittest.mock import AsyncMock, patch
from app.schemas.bid_graphic import GraphicSectionGenerateRequest
from app.services import bid_graphic_service as service


def test_batch_counts_null_error_as_success_and_preserves_failure():
    generated = AsyncMock(side_effect=[
        {"chapter_id": 1, "markdown": "正文", "word_count": 600, "error": None},
        RuntimeError("模型超时"),
        {"chapter_id": 3, "markdown": "固定表格", "word_count": 200, "fixed_form": True, "error": None},
    ])
    with patch.object(service, "_batch_chapter_ids", return_value=[1,2,3]), \
         patch.object(service, "_targets_for_batch", return_value={}), \
         patch.object(service, "generate_graphic_section", generated):
        result = asyncio.run(service.generate_graphic_section_batch(1, GraphicSectionGenerateRequest(chapter_id=1)))
    assert result["success_count"] == 2
    assert result["failure_count"] == 1
    assert result["total_word_count"] == 800
    assert result["chapters"][1]["error"] == "模型超时"


def test_missing_only_stream_preserves_saved_chapters_and_reports_failures():
    chapters = {
        1: {"chapter_title": "已保存", "graphic_markdown": "原正文", "edit_revision": 1, "review_state": "draft"},
        2: {"chapter_title": "待补生成", "graphic_markdown": None, "edit_revision": 0, "review_state": "draft"},
        3: {"chapter_title": "审核中", "graphic_markdown": "", "edit_revision": 1, "review_state": "in_review"},
    }

    async def collect():
        payload = GraphicSectionGenerateRequest(chapter_id=1, chapter_ids=[1, 2, 3])
        return [event async for event in service.stream_graphic_section_batch(1, payload, only_missing=True)]

    with (
        patch.object(service, "_batch_chapter_ids", return_value=[1, 2, 3]),
        patch.object(service, "_targets_for_batch", return_value={}),
        patch.object(service, "_chapter_row", side_effect=lambda _, chapter_id: chapters[chapter_id]),
        patch.object(service.chapter_collaboration.storage, "connect"),
        patch.object(service.chapter_collaboration, "chapter", side_effect=lambda _, bid_id, chapter_id: chapters[chapter_id]),
        patch.object(service, "_build_graphic_generation", return_value={"fixed_markdown": "新增正文", "target_words": 1200}) as build,
        patch.object(service, "save_graphic_section", return_value={"markdown": "新增正文"}) as save,
    ):
        events = asyncio.run(collect())

    build.assert_called_once()
    assert build.call_args.args[1].chapter_id == 2
    save.assert_called_once()
    assert save.call_args.args[1].expected_revision == 0
    assert [(event["type"], event.get("chapter_id")) for event in events if event["type"].startswith("chapter_")] == [
        ("chapter_skipped", 1), ("chapter_done", 2), ("chapter_error", 3),
    ]
    assert events[-1]["success_count"] == events[-1]["skipped_count"] == events[-1]["failure_count"] == 1
