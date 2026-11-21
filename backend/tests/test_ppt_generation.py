from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.services import ppt_service, storage


class PptGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connections = []
        self.addCleanup(self.close_connections)
        self.database = Path(self.temp.name) / "bid.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE bid_catalog_versions (version_id INTEGER PRIMARY KEY, bid_id INTEGER, version_name TEXT, is_final INTEGER);
                CREATE TABLE bid_catalog_chapters (chapter_id INTEGER PRIMARY KEY, bid_id INTEGER, version_id INTEGER,
                    parent_id INTEGER, chapter_title TEXT, level INTEGER, sort_order INTEGER, graphic_markdown TEXT,
                    ref_chart_ids TEXT, ref_image_asset_ids TEXT);
                CREATE TABLE bid_chapter_contents (bid_id INTEGER, version_id INTEGER, chapter_id INTEGER, content_text TEXT);
                CREATE TABLE bid_mermaid_assets (id INTEGER PRIMARY KEY, bid_id INTEGER, chart_title TEXT, chart_image_data TEXT);
                INSERT INTO bid_catalog_versions VALUES (7, 1, '最终目录', 1);
                INSERT INTO bid_catalog_chapters VALUES (10,1,7,0,'项目方案',1,1,'','','');
                INSERT INTO bid_catalog_chapters VALUES (12,1,7,10,'配送流程',2,1,
                    '配送从分拣中心开始。\\n\\n| 环节 | 要求 |\\n| --- | --- |\\n| 分拣 | 记录温度 |\\n| 运输 | 按时送达 |',
                    '[]','[]');
                INSERT INTO bid_chapter_contents VALUES (1,7,12,'每日分拣后装车配送。运输时记录温度。到达学校后完成签收。');
                INSERT INTO bid_catalog_chapters VALUES (20,1,7,0,'质量保障',1,2,'','','');
            """)
            db.execute(
                "UPDATE bid_catalog_chapters SET graphic_markdown=? WHERE chapter_id=12",
                ("配送从分拣中心开始。\n\n| 环节 | 要求 |\n| --- | --- |\n| 分拣 | 记录温度 |\n| 运输 | 按时送达 |",),
            )
        self.project_patch = patch.object(storage, "load_project_by_bid_id", return_value=SimpleNamespace(settings=SimpleNamespace(bidTitle="食材配送项目")))
        self.connect_patch = patch.object(storage, "connect", side_effect=self.connect)
        self.project_patch.start()
        self.connect_patch.start()
        self.addCleanup(self.project_patch.stop)
        self.addCleanup(self.connect_patch.stop)

    def connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        self.connections.append(db)
        return db

    def close_connections(self):
        for db in self.connections:
            db.close()

    def test_source_is_ordered_and_lightweight(self):
        source = ppt_service.get_source(1)
        self.assertEqual([row["chapter_id"] for row in source["chapters"]], [10, 12, 20])
        self.assertTrue(source["chapters"][1]["available"])
        self.assertFalse(source["chapters"][0]["available"])
        self.assertNotIn("content_text", source["chapters"][1])
        self.assertEqual(source["version_id"], 7)

    def test_generated_pptx_has_editable_text_and_table(self):
        content, filename = ppt_service.generate_pptx(
            1, version_id=7, chapter_ids=[12], title="配送方案汇报", subtitle="技术方案",
            include_images=False, max_slides_per_chapter=3,
        )
        deck = Presentation(BytesIO(content))
        self.assertEqual(len(deck.slides), 5)
        self.assertTrue(filename.endswith(".pptx"))
        self.assertTrue(any(shape.has_table for slide in deck.slides for shape in slide.shapes))
        texts = [shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame]
        self.assertTrue(any("每日分拣后装车配送" in text for text in texts))
        with self.assertRaises(ppt_service.PptServiceError):
            ppt_service.generate_pptx(1, version_id=7, chapter_ids=[10], title="", subtitle="")

    def test_existing_chart_image_is_embedded(self):
        image = BytesIO()
        Image.new("RGB", (800, 400), "green").save(image, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        with self.connect() as db:
            db.execute("INSERT INTO bid_mermaid_assets VALUES (3,1,'配送路线图',?)", (data_url,))
            db.execute("UPDATE bid_catalog_chapters SET ref_chart_ids='[3]' WHERE chapter_id=12")
        content, _ = ppt_service.generate_pptx(
            1, version_id=7, chapter_ids=[12], title="", subtitle="",
            include_images=True, max_slides_per_chapter=3,
        )
        deck = Presentation(BytesIO(content))
        self.assertTrue(any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for slide in deck.slides for shape in slide.shapes))


if __name__ == "__main__":
    unittest.main()
