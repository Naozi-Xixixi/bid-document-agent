from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
import time
import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

VERIFY_DATA_DIR = Path(tempfile.gettempdir()) / "new_biaoshuai_verify_data"
os.environ.setdefault("WORDAGENT_BRIDGE_DATA_DIR", str(VERIFY_DATA_DIR))
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.services import bid_content_service


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def main() -> None:
    data_dir = VERIFY_DATA_DIR
    if data_dir.exists():
        shutil.rmtree(data_dir)

    client = TestClient(app)
    text_upload = client.post(
        "/api/knowledge-items",
        data={
            "type": "proposal",
            "industry": "学校",
            "region": "示例",
            "year": "2026",
            "project_type": "食材配送",
            "tags": "冷链配送,食品安全",
        },
        files=[("files", ("school-cold-chain.txt", "冷链配送与学校食堂食品安全响应方案", "text/plain"))],
    )
    assert text_upload.status_code == 200, text_upload.text
    assert text_upload.json()["data"]["success_count"] == 1

    image_upload = client.post(
        "/api/knowledge-items",
        data={"type": "scene_image", "industry": "学校", "tags": "冷库,实景图片"},
        files=[("files", ("cold-room.png", PNG_1X1, "image/png"))],
    )
    assert image_upload.status_code == 200, image_upload.text
    image_item = image_upload.json()["data"]["items"][0]

    search = client.get("/api/knowledge-items?q=冷链配送 学校&type=proposal")
    assert search.status_code == 200, search.text
    assert search.json()["data"]["items"], "expected at least one searchable knowledge item"

    doc = Document()
    bid_content_service._add_content_blocks(  # noqa: SLF001 - verification covers export marker rendering.
        doc,
        f"知识库素材引用测试\n[[knowledge-image:item_id={image_item['item_id']}]]",
    )
    stream = BytesIO()
    doc.save(stream)
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert media_files, "expected exported docx to contain an embedded knowledge image"

    client.close()
    _cleanup_data_dir(data_dir)
    print("P1 knowledge verification passed")


def _cleanup_data_dir(data_dir: Path) -> None:
    for _ in range(5):
        try:
            shutil.rmtree(data_dir)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
