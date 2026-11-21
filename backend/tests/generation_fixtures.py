"""Isolated real SQLite fixtures for generation and image selection tests."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from app.core.config import settings
from app.services import storage

def seed_generation_db(stack):
    root = Path(__file__).resolve().parents[2] / ".cache" / "generation-fixtures"
    root.mkdir(parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(dir=root)  # Retain isolated DB for failure inspection on F:
    stack.enter_context(patch.object(settings, "data_dir", Path(directory)))
    stack.enter_context(patch.object(settings, "database_path", None))
    stack.enter_context(patch.object(settings, "db_backend", "sqlite"))
    storage.init_db()
    from app.modules.knowledge import service as knowledge
    knowledge.ensure_schema()
    project = storage.create_project()
    bid_id = storage.get_bid_id(project.id)
    assert bid_id == 1
    with storage.connect() as conn:
        now = storage.now_utc().isoformat()
        conn.execute("INSERT INTO bid_catalog_versions(version_id,bid_id,version_name,version_style,catalog_content,create_time,update_time) VALUES (2,1,'测试','standard','[]',?,?)",(now,now))
        for chapter_id in (1,2):
            conn.execute("INSERT INTO bid_catalog_chapters(chapter_id,bid_id,version_id,chapter_title,level,sort_order,create_time) VALUES (?,1,2,'配送安排',1,?,?)",(chapter_id,chapter_id,now))
        conn.commit()
