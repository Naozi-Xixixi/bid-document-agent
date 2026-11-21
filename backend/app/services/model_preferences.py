"""Company-scoped model choices for the application's independent AI tasks."""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.tenant_context import get_current_tenant


SCENES = {
    "tender_parse": ("招标文件解读", "text"),
    "catalog": ("目录与大纲", "text"),
    "bid_content": ("标书正文", "text"),
    "knowledge_analysis": ("知识库文档分析", "text"),
    "knowledge_ocr": ("知识库图片识别", "vision"),
    "audit": ("标书审核", "text"),
    "diagram": ("图表结构生成", "text"),
    "embedding": ("云端向量化", "embedding"),
}

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def default_model(scene: str) -> str:
    if scene == "knowledge_ocr":
        return settings.qwen_ocr_model or "qwen3.5-ocr"
    if scene == "embedding":
        return settings.qwen_embedding_model or "text-embedding-v4"
    if scene == "diagram":
        return "qwen3.7-max-2026-05-17"
    return settings.qwen_generation_model or "qwen3.7-flash"


def _ensure_table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS model_preferences ("
        "scene VARCHAR(64) PRIMARY KEY, model_id VARCHAR(128) NOT NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


def selected_model(scene: str) -> str:
    if scene not in SCENES:
        raise ValueError("Unknown model scene")
    if get_current_tenant() is None:
        return default_model(scene)
    from app.services.storage import connect

    db = connect()
    try:
        _ensure_table(db)
        row = db.execute("SELECT model_id FROM model_preferences WHERE scene=?", (scene,)).fetchone()
        return str(row[0]) if row else default_model(scene)
    finally:
        db.close()


def list_selections() -> list[dict[str, str]]:
    return [
        {"scene": scene, "label": label, "kind": kind, "model": selected_model(scene), "default": default_model(scene)}
        for scene, (label, kind) in SCENES.items()
    ]


def save_selection(scene: str, model: str | None) -> str:
    if scene not in SCENES:
        raise ValueError("Unknown model scene")
    model = (model or "").strip()
    if model.startswith("deepseek/") and SCENES[scene][1] != "text":
        raise ValueError("DeepSeek 当前仅支持文本生成场景")
    if model == "deepseek/":
        raise ValueError("请填写 DeepSeek 模型 ID")
    if model and not _MODEL_ID.fullmatch(model):
        raise ValueError("模型 ID 格式不正确")
    if get_current_tenant() is None:
        raise RuntimeError("缺少公司上下文")
    from app.services.storage import connect

    db = connect()
    try:
        _ensure_table(db)
        db.execute("DELETE FROM model_preferences WHERE scene=?", (scene,))
        if model:
            db.execute("INSERT INTO model_preferences(scene, model_id) VALUES (?, ?)", (scene, model))
        db.commit()
    finally:
        db.close()
    return model or default_model(scene)
