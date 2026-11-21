from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import mimetypes
import posixpath
import re
import shutil
import sqlite3
import threading
import tempfile
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pdfplumber
from fastapi import UploadFile

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.services import qwen_client, storage, vector_store, word_template_service

from .schemas import KnowledgeAssetBatchTagRequest, KnowledgeAssetDimensionsUpdate, KnowledgeBatchRequest, KnowledgeCategoryCreate, KnowledgeCategoryUpdate, KnowledgeEvidenceReview, KnowledgeItemUpdate, KnowledgeSearchRequest


class KnowledgeModuleError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)



EVIDENCE_CATEGORIES = {
    "营业执照": "资质证书",
    "食品经营许可证": "资质证书",
    "食品安全管理体系认证证书": "资质证书",
    "质量管理体系认证证书": "资质证书",
    "诚信管理体系认证证书": "资质证书",
    "供应链安全管理体系认证证书": "资质证书",
    "食品可追溯管理体系认证证书": "资质证书",
    "认证查询截图": "资质证书",
    "业绩合同": "方案案例",
    "业绩证明": "方案案例",
    "车辆行驶证": "设备资产",
    "车辆租赁合同": "设备资产",
    "车辆购买合同": "设备资产",
    "车辆购置发票": "设备资产",
    "车辆照片": "设备资产",
}
EVIDENCE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DOCX_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_XML_NS = {"w": DOCX_WORD_NS}
DOCX_BLOCK_CONTAINERS = {
    f"{{{DOCX_WORD_NS}}}sdt",
    f"{{{DOCX_WORD_NS}}}sdtContent",
    f"{{{DOCX_WORD_NS}}}customXml",
    f"{{{DOCX_WORD_NS}}}ins",
    f"{{{DOCX_WORD_NS}}}moveTo",
}
DEFAULT_CATEGORIES = [
    ("招标文件", 5),
    ("资质证书", 10),
    ("方案案例", 20),
    ("现场图片", 30),
    ("制度规范", 40),
    ("人员资质", 50),
    ("设备资产", 60),
]
VECTOR_DIM = 128
OCR_INSTALL_COMMAND = "pip install paddlepaddle paddleocr -i https://pypi.tuna.tsinghua.edu.cn/simple"
OCR_SCAN_THRESHOLD = 50
IMAGE_SCENE_CATEGORIES = ["资质证书类", "现场场景类", "人员资质类", "业绩证明类", "设备资产类", "其他附件类"]
EMPLOYEE_ROLE_CATEGORIES = ["项目经理", "技术人员", "卡车司机", "配送司机", "食品安全员", "检测人员", "仓储分拣人员", "客服人员", "后勤人员", "其他人员"]
IMAGE_SCENE_TO_KNOWLEDGE_CATEGORY = {
    "资质证书类": "资质证书",
    "现场场景类": "现场图片",
    "人员资质类": "人员资质",
    "业绩证明类": "方案案例",
    "设备资产类": "设备资产",
    "其他附件类": "现场图片",
}
IMAGE_CLASSIFY_RULES: list[tuple[str, list[str]]] = [
    ("资质证书类", ["营业执照", "许可证", "经营许可证", "食品经营", "认证", "证书", "资质", "荣誉", "检验报告", "检测报告", "体系认证", "资质证照"]),
    ("现场场景类", ["冷库", "冷链", "冷链车辆", "冷库实景", "车辆", "配送车", "配送网点", "分拣", "分拣中心", "仓库", "现场", "食材", "食材样品", "蔬菜", "肉类", "实验室", "检测实验室", "检测室"]),
    ("人员资质类", ["健康证", "身份证", "社保", "人员", "从业", "资格证", "人员证件", "员工", "厨师", "项目经理"]),
    ("业绩证明类", ["中标", "合同", "验收", "客户评价", "业绩", "通知书", "证明", "服务协议", "案例截图"]),
    ("设备资产类", ["设备", "检测仪", "冷藏", "保鲜", "信息化", "追溯", "系统", "仪器", "台账", "配送设备"]),
]

# P1 structured asset extraction is intentionally dependency-free. It reuses
# the existing OCR text, document context, and image profile.
ASSET_SUBTYPE_OTHER = "\u5176\u4ed6\u9644\u4ef6"
ASSET_CATEGORY_QUALIFICATION = "\u8d44\u8d28\u8bc1\u4e66\u7c7b"
ASSET_CATEGORY_PERSONNEL = "\u4eba\u5458\u8d44\u8d28\u7c7b"
ASSET_CATEGORY_VEHICLE = "\u8f66\u8f86\u8d44\u4ea7\u7c7b"
ASSET_CATEGORY_DOCUMENT = "\u7968\u636e\u51ed\u8bc1\u7c7b"
ASSET_CATEGORY_SCENE = "\u573a\u666f\u73b0\u573a\u7c7b"

# Keep the canonical names separate from the older short labels. Existing
# rows are not rewritten; classification accepts both forms during re-extract.
ASSET_SUBTYPE_IDENTITY = "\u5c45\u6c11\u8eab\u4efd\u8bc1"
ASSET_SUBTYPE_HEALTH = "\u5065\u5eb7\u8bc1\u660e"
PERSONNEL_SCENARIO_TAGS = {
    ASSET_SUBTYPE_IDENTITY: ["\u4eba\u5458\u8eab\u4efd\u6838\u9a8c", "\u6295\u6807\u4eba\u5458\u4fe1\u606f\u767b\u8bb0"],
    ASSET_SUBTYPE_HEALTH: ["\u4ece\u4e1a\u4eba\u5458\u5065\u5eb7\u8d44\u8d28", "\u98df\u54c1\u914d\u9001\u4eba\u5458\u4e0a\u5c97\u8d44\u8d28"],
}

PERSONNEL_DOCUMENT_RULES: list[dict[str, Any]] = [
    {
        "subtype": ASSET_SUBTYPE_IDENTITY,
        "core": ["\u5c45\u6c11\u8eab\u4efd\u8bc1", "\u516c\u6c11\u8eab\u4efd\u53f7\u7801", "\u59d3\u540d", "\u6027\u522b", "\u6c11\u65cf", "\u51fa\u751f", "\u4f4f\u5740", "\u7b7e\u53d1\u673a\u5173", "\u6709\u6548\u671f\u9650"],
        "auxiliary": ["\u53cd\u9762", "\u5934\u50cf", "\u516c\u7ae0", "\u8eab\u4efd\u8bc1\u53f7"],
    },
    {
        "subtype": ASSET_SUBTYPE_HEALTH,
        "core": ["\u5065\u5eb7\u8bc1\u660e", "\u9884\u9632\u6027\u5065\u5eb7\u68c0\u67e5", "\u4ece\u4e1a\u4eba\u5458\u5065\u5eb7\u5408\u683c\u8bc1", "\u6709\u6548\u671f", "\u4ece\u4e1a\u7c7b\u522b", "\u98df\u54c1\u536b\u751f"],
        "auxiliary": ["\u536b\u751f\u90e8\u95e8", "\u53d1\u8bc1\u5355\u4f4d", "\u5065\u5eb7\u5408\u683c", "\u516c\u7ae0"],
    },
]

ASSET_SUBTYPE_RULES: list[dict[str, Any]] = [
    {"category": ASSET_CATEGORY_QUALIFICATION, "subtype": "\u8425\u4e1a\u6267\u7167", "keywords": ["\u8425\u4e1a\u6267\u7167", "\u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801", "\u6cd5\u5b9a\u4ee3\u8868\u4eba"]},
    {"category": ASSET_CATEGORY_QUALIFICATION, "subtype": "\u98df\u54c1\u7ecf\u8425\u8bb8\u53ef\u8bc1", "keywords": ["\u98df\u54c1\u7ecf\u8425\u8bb8\u53ef\u8bc1", "\u98df\u54c1\u7ecf\u8425\u8bb8\u53ef"]},
    {"category": ASSET_CATEGORY_QUALIFICATION, "subtype": "\u6709\u673a\u4ea7\u54c1\u8ba4\u8bc1", "keywords": ["\u6709\u673a\u4ea7\u54c1\u8ba4\u8bc1", "\u6709\u673a\u8ba4\u8bc1"]},
    {"category": ASSET_CATEGORY_QUALIFICATION, "subtype": "ISO\u4f53\u7cfb\u8ba4\u8bc1", "keywords": ["ISO", "\u8d28\u91cf\u7ba1\u7406\u4f53\u7cfb", "\u73af\u5883\u7ba1\u7406\u4f53\u7cfb", "\u804c\u4e1a\u5065\u5eb7\u5b89\u5168"]},
    {"category": ASSET_CATEGORY_QUALIFICATION, "subtype": "\u68c0\u6d4b\u62a5\u544a", "keywords": ["\u68c0\u6d4b\u62a5\u544a", "\u68c0\u9a8c\u62a5\u544a", "\u68c0\u9a8c\u68c0\u6d4b\u62a5\u544a"]},
    {"category": ASSET_CATEGORY_PERSONNEL, "subtype": ASSET_SUBTYPE_IDENTITY, "keywords": ["\u5c45\u6c11\u8eab\u4efd\u8bc1", "\u8eab\u4efd\u8bc1", "\u516c\u6c11\u8eab\u4efd\u53f7\u7801", "\u516c\u6c11\u8eab\u4efd\u53f7"]},
    {"category": ASSET_CATEGORY_PERSONNEL, "subtype": ASSET_SUBTYPE_HEALTH, "keywords": ["\u5065\u5eb7\u8bc1\u660e", "\u5065\u5eb7\u8bc1", "\u9884\u9632\u6027\u5065\u5eb7\u68c0\u67e5", "\u5065\u5eb7\u68c0\u67e5"]},
    {"category": ASSET_CATEGORY_PERSONNEL, "subtype": "\u4ece\u4e1a\u8d44\u683c\u8bc1", "keywords": ["\u4ece\u4e1a\u8d44\u683c\u8bc1", "\u804c\u4e1a\u8d44\u683c\u8bc1"]},
    {"category": ASSET_CATEGORY_PERSONNEL, "subtype": "\u5b89\u5168\u5458\u8bc1", "keywords": ["\u5b89\u5168\u5458\u8bc1", "\u5b89\u5168\u751f\u4ea7\u8003\u6838"]},
    {"category": ASSET_CATEGORY_PERSONNEL, "subtype": "\u5c97\u4f4d\u8bc1\u4e66", "keywords": ["\u5c97\u4f4d\u8bc1\u4e66", "\u4e0a\u5c97\u8bc1", "\u5c97\u4f4d\u57f9\u8bad"]},
    {"category": ASSET_CATEGORY_VEHICLE, "subtype": "\u51b7\u94fe\u914d\u9001\u8f66", "keywords": ["\u51b7\u94fe\u914d\u9001\u8f66", "\u51b7\u85cf\u8f66", "\u51b7\u85cf\u8fd0\u8f93"]},
    {"category": ASSET_CATEGORY_VEHICLE, "subtype": "\u666e\u901a\u8d27\u8f66", "keywords": ["\u666e\u901a\u8d27\u8f66", "\u8d27\u8f66", "\u53a2\u5f0f\u8d27\u8f66"]},
    {"category": ASSET_CATEGORY_VEHICLE, "subtype": "\u8f66\u8f86\u884c\u9a76\u8bc1", "keywords": ["\u8f66\u8f86\u884c\u9a76\u8bc1", "\u884c\u9a76\u8bc1", "\u8f66\u8f86\u767b\u8bb0证"]},
    {"category": ASSET_CATEGORY_VEHICLE, "subtype": "\u8f66\u8f86\u4fdd\u9669\u5355", "keywords": ["\u8f66\u8f86\u4fdd\u9669\u5355", "\u4ea4\u5f3a\u9669", "\u5546\u4e1a\u9669"]},
    {"category": ASSET_CATEGORY_DOCUMENT, "subtype": "\u589e\u503c\u7a0e\u53d1\u7968", "keywords": ["\u589e\u503c\u7a0e\u4e13\u7528\u53d1\u7968", "\u589e\u503c\u7a0e\u53d1\u7968", "\u53d1\u7968\u4ee3\u7801"]},
    {"category": ASSET_CATEGORY_DOCUMENT, "subtype": "\u94f6\u884c\u8f6c\u8d26\u51ed\u8bc1", "keywords": ["\u94f6\u884c\u8f6c\u8d26\u51ed\u8bc1", "\u8f6c\u8d26\u51ed\u8bc1", "\u4ea4\u6613\u56de\u5355"]},
    {"category": ASSET_CATEGORY_DOCUMENT, "subtype": "\u7f34\u8d39\u8bc1\u660e", "keywords": ["\u7f34\u8d39\u8bc1\u660e", "\u7f34\u6b3e\u51ed\u8bc1"]},
    {"category": ASSET_CATEGORY_DOCUMENT, "subtype": "\u4fdd\u8bc1\u91d1\u5355\u636e", "keywords": ["\u4fdd\u8bc1\u91d1\u5355\u636e", "\u6295\u6807\u4fdd\u8bc1\u91d1", "\u5c65\u7ea6\u4fdd\u8bc1\u91d1"]},
    {"category": ASSET_CATEGORY_SCENE, "subtype": "\u51b7\u5e93\u4ed3\u50a8", "keywords": ["\u51b7\u5e93\u4ed3\u50a8", "\u51b7\u5e93", "\u51b7\u85cf\u5e93"]},
    {"category": ASSET_CATEGORY_SCENE, "subtype": "\u5206\u62e3\u4e2d\u5fc3", "keywords": ["\u5206\u62e3\u4e2d\u5fc3", "\u5206\u62e3\u95f4", "\u5206\u62e3"]},
    {"category": ASSET_CATEGORY_SCENE, "subtype": "\u95e8\u5e97\u5b9e\u666f", "keywords": ["\u95e8\u5e97\u5b9e\u666f", "\u95e8\u5e97", "\u8425\u4e1a\u95e8\u5e97"]},
    {"category": ASSET_CATEGORY_SCENE, "subtype": "\u914d\u9001\u73b0\u573a", "keywords": ["\u914d\u9001\u73b0\u573a", "\u914d\u9001\u4e2d\u5fc3", "\u914d\u9001\u70b9"]},
]

SCENE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "仓储类": ("冷库", "冷链仓储", "配送中心", "食材存储", "分拣车间", "温湿度监控", "仓储"),
    "车辆类": ("冷链运输车", "冷藏车", "配送车队", "物流运输", "车辆行驶证", "车辆配置", "配送车辆"),
    "资质类": ("营业执照", "经营许可证", "资质证书", "认证证书", "检测报告", "食品经营许可证"),
    "人员类": ("健康证", "从业资格", "安全员", "配送人员", "岗位培训", "人员配置", "项目经理"),
    "票据类": ("增值税发票", "发票号码", "开票日期", "保证金", "缴费证明", "转账凭证"),
    "履约场景类": ("配送现场", "分拣中心", "门店实景", "质量保障", "应急预案", "履约服务"),
}

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY_PATHS: set[str] = set()
_PROCESSING_SEMAPHORE = asyncio.Semaphore(1)
_DEEP_SEMAPHORE = asyncio.Semaphore(1)
_PROCESSING_ITEMS: set[tuple[str, int]] = set()
_DEEP_TASKS: set[asyncio.Task[Any]] = set()
_DEEP_ITEM_IDS: set[tuple[str, int]] = set()
_VECTOR_TASKS: set[asyncio.Task[Any]] = set()
_VECTOR_ITEM_IDS: set[tuple[str, int]] = set()
_VECTOR_DIRTY_ITEM_IDS: set[tuple[str, int]] = set()
_VECTOR_SEMAPHORE = asyncio.Semaphore(1)
_QWEN_DOCUMENT_SEMAPHORE = asyncio.Semaphore(2)
_OCR_STATUS_LOGGED = False
_OCR_ENGINE: Any | None = None

SMALL_PDF_PAGE_LIMIT = 50
PDF_OCR_DPI = 150
DEEP_ASSET_KEYWORDS = (
    "居民身份证", "公民身份号码", "健康证明", "健康证", "食品经营许可证",
    "营业执照", "资质证书", "认证证书", "检测报告", "行驶证", "保险单",
    "发票", "发证机关", "有效期限", "有效期", "从业类别", "冷库",
    "分拣中心", "配送现场", "冷链", "人员证件",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _looks_like_raw_asset_payload(value: str | None) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text[:1000])
    if compact in {"{}", "[]", "null", "None"}:
        return True
    if re.match(r"^[\[{]", compact):
        try:
            parsed = json.loads(compact)
            if isinstance(parsed, (dict, list)):
                return True
        except Exception:
            return True
    return bool(re.search(r"['\"]?(person_info|employee_info|contract_info|ocr_text|ai_description|resource_path)['\"]?\s*[:=]", compact, re.I))


def _stored_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(settings.backend_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _resolve_stored_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    return path if path.is_absolute() else (settings.backend_root / path).resolve()


def _knowledge_root() -> Path:
    root = settings.upload_dir / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tenant_item_key(item_id: int) -> tuple[str, int]:
    tenant = get_current_tenant()
    company_id = tenant.company_id if tenant is not None else "company_default"
    return str(company_id), int(item_id)


def ensure_schema() -> None:
    database_key = str(settings.database_file.resolve())
    if database_key in _SCHEMA_READY_PATHS:
        return
    with storage.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_category (
                category_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES knowledge_category(category_id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_item (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                title TEXT NOT NULL,
                file_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                original_file_path TEXT,
                source_local_path TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                content_text TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                process_progress INTEGER NOT NULL DEFAULT 0,
                process_error TEXT,
                processing_lock TEXT,
                version_no INTEGER NOT NULL DEFAULT 1,
                valid_until TEXT,
                is_expired INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                vector_status TEXT NOT NULL DEFAULT 'unknown',
                vector_indexed_chunks INTEGER NOT NULL DEFAULT 0,
                vector_error TEXT,
                vector_updated_at TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(category_id) REFERENCES knowledge_category(category_id) ON DELETE SET NULL
            )
            """
        )
        item_columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_item)").fetchall()}
        if "source_local_path" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN source_local_path TEXT")
        if "file_size" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN file_size INTEGER NOT NULL DEFAULT 0")
        if "total_pages" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN total_pages INTEGER NOT NULL DEFAULT 0")
        if "current_page" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN current_page INTEGER NOT NULL DEFAULT 0")
        if "extracted_image_count" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN extracted_image_count INTEGER NOT NULL DEFAULT 0")
        if "text_char_count" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN text_char_count INTEGER NOT NULL DEFAULT 0")
        if "image_category_stats_json" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN image_category_stats_json TEXT NOT NULL DEFAULT '{}'")
        if "processing_lock" not in item_columns:
            conn.execute("ALTER TABLE knowledge_item ADD COLUMN processing_lock TEXT")
        def add_column_if_missing(table: str, columns: set[str], name: str, definition: str) -> None:
            if name in columns:
                return
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            columns.add(name)

        add_column_if_missing("knowledge_item", item_columns, "evidence_kind", "TEXT")
        add_column_if_missing("knowledge_item", item_columns, "evidence_review_json", "TEXT")
        add_column_if_missing("knowledge_item", item_columns, "text_page_count", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_item", item_columns, "scan_page_count", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_item", item_columns, "deep_status", "TEXT NOT NULL DEFAULT 'not_started'")
        add_column_if_missing("knowledge_item", item_columns, "deep_progress", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_item", item_columns, "deep_total", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_item", item_columns, "deep_error", "TEXT")
        add_column_if_missing("knowledge_item", item_columns, "deep_candidate_pages_json", "TEXT NOT NULL DEFAULT '[]'")
        add_column_if_missing("knowledge_item", item_columns, "vector_status", "TEXT NOT NULL DEFAULT 'unknown'")
        add_column_if_missing("knowledge_item", item_columns, "vector_indexed_chunks", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_item", item_columns, "vector_error", "TEXT")
        add_column_if_missing("knowledge_item", item_columns, "vector_updated_at", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_item_version (
                version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                version_no INTEGER NOT NULL,
                original_file_path TEXT,
                content_text TEXT,
                create_time TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_asset (
                asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                asset_type TEXT NOT NULL,
                resource_path TEXT,
                category TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                bid_type TEXT,
                bid_scenario TEXT NOT NULL DEFAULT '',
                length_level TEXT,
                dimension_tags TEXT NOT NULL DEFAULT '{}',
                extend_tags TEXT NOT NULL DEFAULT '[]',
                ai_description TEXT,
                storage_location TEXT,
                page_num INTEGER,
                source_chapter TEXT,
                create_time TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_asset)").fetchall()}
        if "category" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN category TEXT")
        if "page_num" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN page_num INTEGER")
        if "source_chapter" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN source_chapter TEXT")
        if "annotation" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN annotation TEXT")
        if "image_hash" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN image_hash TEXT")
        if "duplicate_of_asset_id" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN duplicate_of_asset_id INTEGER")
        if "image_width" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN image_width INTEGER")
        if "image_height" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN image_height INTEGER")
        if "person_info" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN person_info TEXT")
        if "ocr_text" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN ocr_text TEXT")
        if "employee_info" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN employee_info TEXT")
        if "employee_role" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN employee_role TEXT")
        if "contract_info" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN contract_info TEXT")
        if "archive_date" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN archive_date TEXT")
        if "bid_type" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN bid_type TEXT")
        if "bid_scenario" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN bid_scenario TEXT NOT NULL DEFAULT ''")
        if "length_level" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN length_level TEXT")
        if "dimension_tags" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN dimension_tags TEXT NOT NULL DEFAULT '{}'")
        if "extend_tags" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN extend_tags TEXT NOT NULL DEFAULT '[]'")
        if "structured_info" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN structured_info TEXT NOT NULL DEFAULT ''")
        if "asset_sub_type" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN asset_sub_type TEXT NOT NULL DEFAULT ''")
        if "expire_date" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN expire_date TEXT NOT NULL DEFAULT ''")
        if "cert_no" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN cert_no TEXT NOT NULL DEFAULT ''")
        if "holder_name" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN holder_name TEXT NOT NULL DEFAULT ''")
        if "project_id" not in asset_columns:
            conn.execute("ALTER TABLE knowledge_asset ADD COLUMN project_id INTEGER")
        add_column_if_missing("knowledge_asset", asset_columns, "deep_candidate", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("knowledge_asset", asset_columns, "deep_status", "TEXT NOT NULL DEFAULT 'not_started'")
        for name, definition in [("ocr_status", "TEXT NOT NULL DEFAULT 'not_started'"), ("ocr_error", "TEXT"), ("ocr_engine", "TEXT"), ("ocr_confidence", "REAL")]:
            add_column_if_missing("knowledge_asset", asset_columns, name, definition)
        conn.execute(
            """
            UPDATE knowledge_asset
            SET structured_info = COALESCE(structured_info, ''),
                asset_sub_type = COALESCE(asset_sub_type, ''),
                expire_date = COALESCE(expire_date, ''),
                cert_no = COALESCE(cert_no, ''),
                holder_name = COALESCE(holder_name, ''),
                bid_scenario = COALESCE(bid_scenario, ''),
                dimension_tags = COALESCE(NULLIF(dimension_tags, ''), '{}')
            WHERE structured_info IS NULL OR asset_sub_type IS NULL OR expire_date IS NULL OR cert_no IS NULL OR holder_name IS NULL OR bid_scenario IS NULL OR dimension_tags IS NULL OR dimension_tags = ''
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_project (
                project_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                tender_doc_id INTEGER,
                bid_doc_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(tender_doc_id) REFERENCES knowledge_item(item_id) ON DELETE SET NULL,
                FOREIGN KEY(bid_doc_id) REFERENCES knowledge_item(item_id) ON DELETE SET NULL
            )
            """
        )
        project_columns = {row[1] for row in conn.execute("PRAGMA table_info(bid_project)").fetchall()}
        add_column_if_missing("bid_project", project_columns, "format_template_id", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_path", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_name", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_status", "TEXT NOT NULL DEFAULT 'not_started'")
        add_column_if_missing("bid_project", project_columns, "format_template_error", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_rules", "TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing("bid_project", project_columns, "format_template_source_file", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_updated_at", "TEXT")
        add_column_if_missing("bid_project", project_columns, "format_template_default", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE bid_project SET format_template_status = COALESCE(NULLIF(format_template_status, ''), 'not_started'), format_template_rules = COALESCE(NULLIF(format_template_rules, ''), '{}')")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requirement_solution_map (
                map_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                requirement_id INTEGER,
                requirement_content TEXT NOT NULL,
                solution_id INTEGER,
                solution_content TEXT,
                match_score REAL NOT NULL DEFAULT 0,
                source_chapter TEXT,
                create_time TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES bid_project(project_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_item_interpretation (
                item_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                summary TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                source_hash TEXT,
                source_chunk_count INTEGER NOT NULL,
                result_count INTEGER NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunk (
                chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                section_title TEXT,
                page_number INTEGER,
                content TEXT NOT NULL,
                tokens_json TEXT NOT NULL DEFAULT '[]',
                embedding_json TEXT NOT NULL DEFAULT '[]',
                project_id INTEGER,
                doc_role TEXT NOT NULL DEFAULT 'knowledge',
                chunk_kind TEXT NOT NULL DEFAULT 'document_text',
                section_path TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_chunk)").fetchall()}
        add_column_if_missing("knowledge_chunk", chunk_columns, "project_id", "INTEGER")
        add_column_if_missing("knowledge_chunk", chunk_columns, "doc_role", "TEXT NOT NULL DEFAULT 'knowledge'")
        add_column_if_missing("knowledge_chunk", chunk_columns, "chunk_kind", "TEXT NOT NULL DEFAULT 'document_text'")
        add_column_if_missing("knowledge_chunk", chunk_columns, "source_asset_id", "INTEGER")
        add_column_if_missing("knowledge_chunk", chunk_columns, "section_path", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            UPDATE knowledge_chunk
            SET project_id = COALESCE(project_id, (
                    SELECT MAX(p.project_id) FROM bid_project p
                    WHERE p.tender_doc_id = knowledge_chunk.item_id OR p.bid_doc_id = knowledge_chunk.item_id
                )),
                doc_role = CASE
                    WHEN EXISTS (SELECT 1 FROM bid_project p WHERE p.tender_doc_id = knowledge_chunk.item_id) THEN 'tender'
                    WHEN EXISTS (SELECT 1 FROM bid_project p WHERE p.bid_doc_id = knowledge_chunk.item_id) THEN 'bid'
                    ELSE COALESCE(NULLIF(doc_role, ''), 'knowledge')
                END,
                section_path = COALESCE(NULLIF(section_path, ''), section_title, '')
            WHERE project_id IS NULL OR doc_role = 'knowledge' OR section_path = ''
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_rag_task (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lock_owner TEXT,
                lease_until TEXT,
                last_error TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_entity (
                entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                category_id INTEGER,
                item_id INTEGER,
                create_time TEXT NOT NULL,
                UNIQUE(name, entity_type, item_id),
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_relation (
                relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                item_id INTEGER,
                create_time TEXT NOT NULL,
                UNIQUE(source_entity_id, target_entity_id, relation_type, item_id),
                FOREIGN KEY(source_entity_id) REFERENCES knowledge_entity(entity_id) ON DELETE CASCADE,
                FOREIGN KEY(target_entity_id) REFERENCES knowledge_entity(entity_id) ON DELETE CASCADE,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_import_task (
                task_id TEXT PRIMARY KEY,
                dir_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                current_file TEXT,
                failures_json TEXT NOT NULL DEFAULT '[]',
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_import_file (
                import_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                project_name TEXT,
                item_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                error_msg TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES knowledge_import_task(task_id) ON DELETE CASCADE,
                FOREIGN KEY(item_id) REFERENCES knowledge_item(item_id) ON DELETE SET NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_item_category ON knowledge_item(category_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_item_status ON knowledge_item(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_item_source_local ON knowledge_item(source_local_path, file_size)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_category ON knowledge_asset(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_bid_type ON knowledge_asset(bid_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_length_level ON knowledge_asset(length_level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_dimensions ON knowledge_asset(category, asset_sub_type, bid_scenario, length_level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_hash ON knowledge_asset(image_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_sub_type ON knowledge_asset(asset_sub_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_expire_date ON knowledge_asset(expire_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_cert_no ON knowledge_asset(cert_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_asset_project_id ON knowledge_asset(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bid_project_tender_doc ON bid_project(tender_doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bid_project_bid_doc ON bid_project(bid_doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bid_project_template_status ON bid_project(format_template_status, format_template_default)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_requirement_solution_project ON requirement_solution_map(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_interpretation_status ON knowledge_item_interpretation(status, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_item ON knowledge_chunk(item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_item_role ON knowledge_chunk(item_id, doc_role, chunk_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_project_role ON knowledge_chunk(project_id, doc_role, chunk_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_section_title ON knowledge_chunk(section_title)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_rag_task_status_lease ON knowledge_rag_task(status, lease_until)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entity_item ON knowledge_entity(item_id)")
        if (settings.db_backend or "sqlite").strip().lower() == "mysql":
            _ensure_mysql_knowledge_fulltext_index(conn)
        from . import document_analysis, recovery
        document_analysis.create_schema(conn)
        recovery.create_schema(conn)
        now = _now()
        for name, sort_order in DEFAULT_CATEGORIES:
            exists = conn.execute("SELECT 1 FROM knowledge_category WHERE name = ?", (name,)).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO knowledge_category (name, parent_id, sort_order, create_time, update_time) VALUES (?, NULL, ?, ?, ?)",
                    (name, sort_order, now, now),
                )
        conn.commit()
    _SCHEMA_READY_PATHS.add(database_key)


def _ok(data: Any) -> dict[str, Any]:
    return data


def _normalize_dimension_value(value: str | None, kind: str) -> str:
    mapping = {
        "bid_scenario": {"open": "\u660e\u6807", "blind": "\u6697\u6807"},
        "bid_type": {"open": "明标", "blind": "暗标"},
        "length_level": {"short": "精简版", "standard": "标准版", "detailed": "详细版"},
    }
    normalized = (value or "").strip()
    return mapping.get(kind, {}).get(normalized, normalized)


DIMENSION_CONFIDENCE_THRESHOLD = 0.80
BID_SCENARIO_OPTIONS = ["\u660e\u6807", "\u6697\u6807"]
LENGTH_LEVEL_OPTIONS = ["\u7cbe\u7b80\u7248", "\u6807\u51c6\u7248", "\u8be6\u7ec6\u7248"]


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _dimension_text(row: Any) -> str:
    values: list[str] = []
    for key in (
        "source_name",
        "title",
        "item_title",
        "category",
        "annotation",
        "ai_description",
        "ocr_text",
        "source_chapter",
        "content_text",
        "tags_json",
        "tags",
        "structured_info",
    ):
        value = _row_value(row, key, "")
        if isinstance(value, (dict, list)):
            value = _json(value)
        if value:
            values.append(str(value))
    return "\n".join(values)


def _length_dimension_text(row: Any) -> str:
    values: list[str] = []
    for key in ("content_text", "ocr_text", "annotation", "ai_description", "source_chapter"):
        value = _row_value(row, key, "")
        if value:
            values.append(str(value))
    return "\n".join(values)


def _weighted_keyword_score(text: str, rules: list[tuple[str, float]]) -> tuple[float, list[str]]:
    haystack = (text or "").casefold()
    score = 0.0
    matched: list[str] = []
    for keyword, weight in rules:
        if keyword.casefold() in haystack:
            score += weight
            matched.append(keyword)
    return score, matched


def _infer_bid_scenario(row: Any) -> tuple[str, float, list[str]]:
    text = _dimension_text(row)
    open_rules = [
        ("\u4f01\u4e1a\u540d\u79f0", 1.8),
        ("\u516c\u53f8\u540d\u79f0", 1.8),
        ("\u6709\u9650\u516c\u53f8", 1.3),
        ("logo", 1.6),
        ("\u8054\u7cfb\u65b9\u5f0f", 1.5),
        ("\u8054\u7cfb\u7535\u8bdd", 1.4),
        ("\u6cd5\u5b9a\u4ee3\u8868\u4eba", 1.5),
        ("\u62a5\u4ef7\u660e\u7ec6", 2.2),
        ("\u62a5\u4ef7", 1.5),
        ("\u4e1a\u7ee9\u6848\u4f8b", 1.8),
        ("\u4e2d\u6807\u901a\u77e5\u4e66", 1.6),
        ("\u516c\u5f00\u4fe1\u606f", 1.2),
    ]
    blind_rules = [
        ("\u6697\u6807", 3.2),
        ("\u533f\u540d", 2.4),
        ("\u9690\u53bb\u4f01\u4e1a", 3.2),
        ("\u4f01\u4e1a\u8131\u654f", 3.0),
        ("\u901a\u7528\u65b9\u6848", 2.0),
        ("\u4e0d\u5f97\u51fa\u73b0\u4f01\u4e1a\u540d\u79f0", 3.4),
        ("\u4e0d\u5f97\u51fa\u73b0 logo", 3.0),
        ("\u9690\u53bb\u4f01\u4e1a\u4fe1\u606f", 3.4),
    ]
    open_score, open_matches = _weighted_keyword_score(text, open_rules)
    blind_score, blind_matches = _weighted_keyword_score(text, blind_rules)
    category = str(_row_value(row, "category", ""))
    if any(token in category for token in ("\u4e1a\u7ee9", "\u62a5\u4ef7")):
        open_score += 0.35
    if max(open_score, blind_score) < 1.8:
        return "", 0.0, []
    if abs(open_score - blind_score) < 0.6:
        return "", round(min(0.79, 0.55 + max(open_score, blind_score) * 0.05), 3), open_matches + blind_matches
    value = "\u660e\u6807" if open_score > blind_score else "\u6697\u6807"
    score = max(open_score, blind_score)
    margin = abs(open_score - blind_score)
    confidence = min(0.99, 0.70 + score * 0.08 + margin * 0.05)
    matches = open_matches if value == "\u660e\u6807" else blind_matches
    if confidence < DIMENSION_CONFIDENCE_THRESHOLD:
        return "", round(confidence, 3), matches
    return value, round(confidence, 3), matches


def _infer_length_level(row: Any) -> tuple[str, float, list[str]]:
    text = _length_dimension_text(row)
    normalized = re.sub(r"\s+", " ", text).strip()
    meaningful = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
    char_count = len(meaningful)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    list_lines = [line for line in lines if re.match(r"^(?:[-*\u2022]|\d+[.\u3001)]|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.)])", line)]
    if char_count < 24 and len(list_lines) < 2:
        return "", 0.0, []
    list_ratio = len(list_lines) / max(len(lines), 1)
    sentence_parts = [part.strip() for part in re.split(r"[\u3002\uff01\uff1f!?\uff1b;\n]", text) if part.strip()]
    average_sentence = char_count / max(len(sentence_parts), 1)
    detail_terms = sum(1 for term in ("\u6570\u636e", "\u6848\u4f8b", "\u6b65\u9aa4", "\u6307\u6807", "\u53c2\u6570", "\u65b9\u6848") if term in text)
    if char_count <= 180:
        confidence = 0.68 + (0.12 if char_count <= 120 else 0.05) + (0.14 if list_ratio >= 0.25 else 0.03) + (0.05 if average_sentence <= 32 else 0)
        if confidence >= DIMENSION_CONFIDENCE_THRESHOLD:
            return "\u7cbe\u7b80\u7248", round(min(0.98, confidence), 3), ["short_text"]
        return "", round(confidence, 3), []
    if char_count <= 900:
        confidence = 0.79 + (0.07 if 260 <= char_count <= 720 else 0.02) + (0.04 if len(lines) >= 2 else 0) + (0.03 if list_ratio < 0.65 else 0)
        if confidence >= DIMENSION_CONFIDENCE_THRESHOLD:
            return "\u6807\u51c6\u7248", round(min(0.96, confidence), 3), ["medium_text"]
        return "", round(confidence, 3), []
    confidence = 0.84 + (0.05 if len(lines) >= 4 else 0) + (0.03 if detail_terms >= 2 else 0)
    return "\u8be6\u7ec6\u7248", round(min(0.99, confidence), 3), ["long_text"]


def _infer_asset_dimensions(row: Any) -> dict[str, Any]:
    scenario, scenario_confidence, scenario_matches = _infer_bid_scenario(row)
    length_level, length_confidence, length_matches = _infer_length_level(row)
    return {
        "bid_scenario": scenario,
        "length_level": length_level,
        "confidence": {
            "bid_scenario": scenario_confidence,
            "length_level": length_confidence,
        },
        "matches": {
            "bid_scenario": scenario_matches,
            "length_level": length_matches,
        },
        "source": "auto",
    }


def list_categories() -> list[dict[str, Any]]:
    ensure_schema()
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, COUNT(i.item_id) AS item_count
            FROM knowledge_category c
            LEFT JOIN knowledge_item i ON i.category_id = c.category_id
            GROUP BY c.category_id
            ORDER BY COALESCE(c.parent_id, 0), c.sort_order, c.category_id
            """
        ).fetchall()
    return [_row_category(row) for row in rows]


def create_category(payload: KnowledgeCategoryCreate) -> dict[str, Any]:
    ensure_schema()
    now = _now()
    with storage.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO knowledge_category (name, parent_id, sort_order, create_time, update_time) VALUES (?, ?, ?, ?, ?)",
            (payload.name.strip(), payload.parent_id, payload.sort_order, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT *, 0 AS item_count FROM knowledge_category WHERE category_id = ?", (int(cursor.lastrowid),)).fetchone()
    return _row_category(row)


def update_category(category_id: int, payload: KnowledgeCategoryUpdate) -> dict[str, Any]:
    ensure_schema()
    assignments: list[str] = []
    params: list[Any] = []
    if payload.name is not None:
        assignments.append("name = ?")
        params.append(payload.name.strip())
    if payload.parent_id is not None:
        if payload.parent_id == category_id:
            raise KnowledgeModuleError(40021, "分类不能设置自己为父级")
        assignments.append("parent_id = ?")
        params.append(payload.parent_id)
    if payload.sort_order is not None:
        assignments.append("sort_order = ?")
        params.append(payload.sort_order)
    if not assignments:
        return get_category(category_id)
    assignments.append("update_time = ?")
    params.extend([_now(), category_id])
    with storage.connect() as conn:
        cursor = conn.execute(f"UPDATE knowledge_category SET {', '.join(assignments)} WHERE category_id = ?", params)
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeModuleError(40421, "分类不存在", 404)
    return get_category(category_id)


def delete_category(category_id: int) -> None:
    ensure_schema()
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_item SET category_id = NULL WHERE category_id = ?", (category_id,))
        conn.execute("UPDATE knowledge_category SET parent_id = NULL WHERE parent_id = ?", (category_id,))
        cursor = conn.execute("DELETE FROM knowledge_category WHERE category_id = ?", (category_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeModuleError(40421, "分类不存在", 404)


def get_category(category_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT c.*, COUNT(i.item_id) AS item_count
            FROM knowledge_category c
            LEFT JOIN knowledge_item i ON i.category_id = c.category_id
            WHERE c.category_id = ?
            GROUP BY c.category_id
            """,
            (category_id,),
        ).fetchone()
    if row is None:
        raise KnowledgeModuleError(40421, "分类不存在", 404)
    return _row_category(row)


async def upload_items(files: list[UploadFile], *, category_id: int | None, tags: str = "", valid_until: str | None = None, evidence_kind: str | None = None) -> dict[str, Any]:
    ensure_schema()
    if evidence_kind is not None and evidence_kind not in EVIDENCE_CATEGORIES:
        raise KnowledgeModuleError(40001, "请选择有效的企业证明材料类型")
    if not files:
        raise KnowledgeModuleError(40001, "请至少上传一个知识文件")
    created: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    root = _knowledge_root()
    for file in files:
        filename = Path((file.filename or "").replace("\\", "/")).name or "knowledge-file"
        suffix = Path(filename).suffix.lower()
        if evidence_kind and suffix not in EVIDENCE_EXTENSIONS:
            failures.append({"file_name": filename, "reason": "证明材料请上传 PDF 或 JPG、PNG、WEBP 图片"})
            continue
        if suffix not in SUPPORTED_EXTENSIONS:
            failures.append({"file_name": filename, "reason": "文件格式暂不支持"})
            continue
        try:
            file_dir = root / uuid.uuid4().hex
            file_dir.mkdir(parents=True, exist_ok=True)
            target = file_dir / filename
            # UploadFile is backed by a temporary file after multipart
            # parsing. Stream it to the tenant directory instead of copying
            # a 300–600 MB Word file into a second in-memory bytes object.
            file_size = await asyncio.to_thread(_persist_upload_file, file, target)
            now = _now()
            title = Path(filename).stem
            version_no = _next_version(title)
            item_category = category_id or _guess_category_id(filename, "")
            if evidence_kind:
                item_category = next((c["category_id"] for c in list_categories() if c["name"] == EVIDENCE_CATEGORIES[evidence_kind]), item_category)
            with storage.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_item (
                        category_id, title, file_type, source_name, original_file_path, source_local_path, file_size,
                        content_text, status, process_progress, version_no, valid_until,
                        is_expired, tags_json, create_time, update_time, evidence_kind
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, '', 'pending', 5, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (item_category, title, suffix.lstrip("."), filename, _stored_path(target), file_size, version_no, valid_until, _json(_parse_tags(tags)), now, now, evidence_kind),
                )
                item_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO knowledge_item_version (item_id, title, version_no, original_file_path, content_text, create_time) VALUES (?, ?, ?, ?, '', ?)",
                    (item_id, title, version_no, _stored_path(target), now),
                )
                conn.commit()
            created.append(get_item(item_id))
        except Exception as exc:
            failures.append({"file_name": filename, "reason": str(exc) or "上传失败"})
    return {"items": created, "failures": failures, "success_count": len(created), "failure_count": len(failures)}


def _persist_upload_file(file: UploadFile, target: Path) -> int:
    """Copy an already parsed multipart upload to disk with bounded memory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source = getattr(file, "file", None)
    if source is None:
        raise OSError("上传文件流不可用")
    source.seek(0)
    with target.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    return int(target.stat().st_size)


def batch_import_local_dir(dir_path: str | None = None) -> dict[str, Any]:
    """Create a persistent local-directory import task.

    The task only scans and records files here; actual parsing is delegated to
    run_batch_import_task(), which reuses process_item() for the heavy pipeline.
    """

    ensure_schema()
    root = Path(dir_path).expanduser() if dir_path else settings.resolve_local_path(settings.data_dir) / "imports" / "knowledge"
    root = root.resolve()
    print(f"[KnowledgeBatchImport] scan_start dir={root}", flush=True)
    if not root.exists() or not root.is_dir():
        print(f"[KnowledgeBatchImport] scan_failed reason=dir_not_found dir={root}", flush=True)
        raise KnowledgeModuleError(40441, f"批量导入目录不存在：{root}", 404)
    files = _scan_supported_files(root)
    print(f"[KnowledgeBatchImport] scan_done total={len(files)}", flush=True)
    for path in files:
        try:
            print(f"[KnowledgeBatchImport] candidate file={path} size={path.stat().st_size}", flush=True)
        except OSError as exc:
            print(f"[KnowledgeBatchImport] candidate_stat_failed file={path} error={exc}", flush=True)
    task_id = uuid.uuid4().hex
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_import_task (
                task_id, dir_path, status, total_files, processed_files, success_count,
                failed_count, skipped_count, current_file, failures_json, create_time, update_time
            ) VALUES (?, ?, 'processing', ?, 0, 0, 0, 0, NULL, '[]', ?, ?)
            """,
            (task_id, str(root), len(files), now, now),
        )
        for path in files:
            stat = path.stat()
            conn.execute(
                """
                INSERT INTO knowledge_import_file (task_id, file_path, file_name, file_size, project_name, status, create_time, update_time)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (task_id, str(path), path.name, int(stat.st_size), _project_name_from_file(root, path), now, now),
            )
        conn.commit()
    print(f"[KnowledgeBatchImport] task_created task_id={task_id} total={len(files)}", flush=True)
    return {"task_id": task_id, "dir_path": str(root), "total_files": len(files), "status": "processing"}


async def run_batch_import_task(task_id: str) -> None:
    ensure_schema()
    print(f"[KnowledgeBatchImport] task_run_start task_id={task_id}", flush=True)
    with storage.connect() as conn:
        task = conn.execute("SELECT * FROM knowledge_import_task WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            print(f"[KnowledgeBatchImport] task_missing task_id={task_id}", flush=True)
            return
        file_rows = conn.execute("SELECT * FROM knowledge_import_file WHERE task_id = ? AND status IN ('pending', 'failed') ORDER BY import_file_id", (task_id,)).fetchall()
        conn.execute("UPDATE knowledge_import_task SET status = 'processing', update_time = ? WHERE task_id = ?", (_now(), task_id))
        conn.commit()
    if not file_rows:
        print(f"[KnowledgeBatchImport] task_no_pending task_id={task_id}", flush=True)
        _finish_import_task(task_id)
        return
    for row in file_rows:
        path = Path(row["file_path"])
        import_file_id = int(row["import_file_id"])
        _mark_import_current(task_id, import_file_id, path.name)
        print(f"[KnowledgeBatchImport] file_start task_id={task_id} file={path}", flush=True)
        try:
            if not path.exists():
                raise KnowledgeModuleError(40442, "文件不存在")
            item_id = int(row["item_id"] or 0)
            if item_id:
                print(f"[KnowledgeBatchImport] file_retry_existing_item file={path.name} item_id={item_id}", flush=True)
                if item_status(item_id)["status"] != "completed":
                    await process_item(item_id)
                item = await asyncio.to_thread(get_item, item_id, include_chunks=False)
                if item["status"] == "failed":
                    print(f"[KnowledgeBatchImport] file_failed file={path.name} item_id={item_id} error={item.get('process_error')}", flush=True)
                    _mark_import_file(task_id, import_file_id, "failed", item.get("process_error") or "文档处理失败", failed=True, item_id=item_id)
                else:
                    print(f"[KnowledgeBatchImport] file_completed file={path.name} item_id={item_id}", flush=True)
                    _mark_import_file(task_id, import_file_id, "completed", None, success=True, item_id=item_id)
                continue
            duplicate = _find_duplicate_local_item(path)
            if duplicate is not None:
                if duplicate["status"] in {"failed", "pending", "processing"}:
                    item_id = int(duplicate["item_id"])
                    print(f"[KnowledgeBatchImport] file_duplicate_retry file={path.name} item_id={item_id} status={duplicate['status']}", flush=True)
                    await process_item(item_id)
                    item = get_item(item_id, include_chunks=False)
                    if item["status"] == "failed":
                        print(f"[KnowledgeBatchImport] file_failed file={path.name} item_id={item_id} error={item.get('process_error')}", flush=True)
                        _mark_import_file(task_id, import_file_id, "failed", item.get("process_error") or "文档处理失败", failed=True, item_id=item_id)
                    else:
                        print(f"[KnowledgeBatchImport] file_completed file={path.name} item_id={item_id}", flush=True)
                        _mark_import_file(task_id, import_file_id, "completed", None, success=True, item_id=item_id)
                    continue
                print(f"[KnowledgeBatchImport] file_skipped_duplicate file={path.name} item_id={duplicate['item_id']}", flush=True)
                _mark_import_file(task_id, import_file_id, "skipped", None, skipped=True)
                continue
            item_id = _create_item_from_local_file(path, project_name=row["project_name"] or _project_name_from_file(Path(task["dir_path"]), path))
            with storage.connect() as conn:
                conn.execute("UPDATE knowledge_import_file SET item_id=? WHERE import_file_id=?", (item_id, import_file_id))
                conn.commit()
            print(f"[KnowledgeBatchImport] item_created file={path.name} item_id={item_id}", flush=True)
            await process_item(item_id)
            item = get_item(item_id, include_chunks=False)
            if item["status"] == "failed":
                print(f"[KnowledgeBatchImport] file_failed file={path.name} item_id={item_id} error={item.get('process_error')}", flush=True)
                _mark_import_file(task_id, import_file_id, "failed", item.get("process_error") or "文档处理失败", failed=True, item_id=item_id)
            else:
                print(f"[KnowledgeBatchImport] file_completed file={path.name} item_id={item_id}", flush=True)
                _mark_import_file(task_id, import_file_id, "completed", None, success=True, item_id=item_id)
        except Exception as exc:
            message = exc.message if isinstance(exc, KnowledgeModuleError) else str(exc)
            print(f"[KnowledgeBatchImport] file_exception file={path} error={message}", flush=True)
            _mark_import_file(task_id, import_file_id, "failed", message or "导入失败", failed=True)
    _finish_import_task(task_id)
    print(f"[KnowledgeBatchImport] task_run_done task_id={task_id}", flush=True)


def get_batch_import_status(task_id: str) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        task = conn.execute("SELECT * FROM knowledge_import_task WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            raise KnowledgeModuleError(40443, "导入任务不存在", 404)
        files = conn.execute("SELECT * FROM knowledge_import_file WHERE task_id = ? ORDER BY import_file_id", (task_id,)).fetchall()
    return _row_import_task(task, files)


def retry_failed_import(task_id: str) -> dict[str, Any]:
    ensure_schema()
    now = _now()
    with storage.connect() as conn:
        task = conn.execute("SELECT * FROM knowledge_import_task WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            raise KnowledgeModuleError(40443, "导入任务不存在", 404)
        failed_rows = conn.execute("SELECT import_file_id FROM knowledge_import_file WHERE task_id = ? AND status = 'failed'", (task_id,)).fetchall()
        if not failed_rows:
            return get_batch_import_status(task_id)
        conn.execute("UPDATE knowledge_import_file SET status = 'pending', error_msg = NULL, update_time = ? WHERE task_id = ? AND status = 'failed'", (now, task_id))
        conn.execute(
            """
            UPDATE knowledge_import_task
            SET status = 'processing', current_file = NULL, failures_json = '[]', update_time = ?
            WHERE task_id = ?
            """,
            (now, task_id),
        )
        conn.commit()
    _sync_import_task_counts(task_id, clear_current=True)
    return get_batch_import_status(task_id)


async def process_item(item_id: int) -> None:
    ensure_schema()
    task_key = _tenant_item_key(item_id)
    if task_key in _PROCESSING_ITEMS:
        print(f"[KnowledgeProcess] duplicate_skip item_id={item_id}", flush=True)
        return
    _PROCESSING_ITEMS.add(task_key)
    lock_id = uuid.uuid4().hex
    try:
        async with _PROCESSING_SEMAPHORE:
            _set_item_processing_lock(item_id, lock_id)
            for attempt in range(1, 4):
                try:
                    await _process_item_once(item_id)
                    return
                except Exception as exc:
                    message = _failure_message(exc)
                    if _is_database_locked_error(exc) and attempt < 3:
                        print(f"[KnowledgeProcess] sqlite_locked_retry item_id={item_id} attempt={attempt} error={message}", flush=True)
                        await asyncio.sleep(0.8 * attempt)
                        continue
                    error_type = "数据库锁冲突" if _is_database_locked_error(exc) else "文档解析异常"
                    _safe_set_item_failed(item_id, f"{error_type}：{message or '处理失败'}")
                    _maybe_finalize_project_for_item(item_id)
                    return
    finally:
        _clear_item_processing_lock(item_id, lock_id)
        _PROCESSING_ITEMS.discard(task_key)


def _deep_candidate_asset_count(item_id: int) -> int:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS count FROM knowledge_asset WHERE item_id = ? AND deep_candidate = 1 AND COALESCE(deep_status, 'not_started') <> 'completed'",
            (item_id,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def _queue_deep_extract(item_id: int) -> bool:
    task_key = _tenant_item_key(item_id)
    if task_key in _DEEP_ITEM_IDS:
        return False
    _DEEP_ITEM_IDS.add(task_key)
    async def run_deep_and_followup():
        try:
            async with _DEEP_SEMAPHORE:
                await deep_extract_item(item_id)
        finally:
            _DEEP_ITEM_IDS.discard(task_key)
        # Persist the follow-up before invoking it so a restart can recover it.
        from . import recovery
        with storage.connect() as conn:
            projects = conn.execute("SELECT project_id,tender_doc_id,bid_doc_id FROM bid_project WHERE tender_doc_id=? OR bid_doc_id=?", (item_id, item_id)).fetchall()
        for project in projects:
            state = recovery.workflow_state(project["project_id"])
            if state and state["status"] in {"pending", "processing"}:
                continue
            recovery.prepare_project(project["project_id"])
            with storage.connect() as conn:
                conn.execute("UPDATE knowledge_project_workflow SET status='pending',phase='interpret',error=NULL,updated_at=? WHERE project_id=?", (_now(), project["project_id"]))
                conn.commit()
            await recovery.run_project(project["project_id"], project["tender_doc_id"], project["bid_doc_id"])
    task = asyncio.create_task(run_deep_and_followup())
    _DEEP_TASKS.add(task)
    def _finish(done: asyncio.Task[Any]) -> None:
        _DEEP_TASKS.discard(done)
        _DEEP_ITEM_IDS.discard(task_key)
    task.add_done_callback(_finish)
    return True


def _queue_item_vector_sync(item_id: int) -> bool:
    """Persist and asynchronously run a tenant-scoped, resumable vector job."""
    task_key = _tenant_item_key(item_id)
    if not vector_store.enabled():
        _set_vector_item_state(item_id, "not_configured", 0, "Qdrant vector backend is not enabled")
        return False
    if task_key in _VECTOR_ITEM_IDS:
        # Deep OCR can commit new annotation chunks while the earlier text
        # index is running. Follow it with a fresh pass instead of losing it.
        _VECTOR_DIRTY_ITEM_IDS.add(task_key)
        return False
    _VECTOR_ITEM_IDS.add(task_key)

    async def run() -> None:
        try:
            task_id = await asyncio.to_thread(_persist_vector_sync_task, item_id)
            max_attempts = max(1, min(int(settings.rag_vector_task_max_attempts or 3), 8))
            retry_base_seconds = max(0.25, min(float(settings.rag_vector_task_retry_base_seconds or 2.0), 60.0))
            result: dict[str, Any] = {"complete": False, "claimed": True, "attempted": 0, "indexed": 0}
            for attempt in range(max_attempts):
                if attempt:
                    await asyncio.sleep(min(retry_base_seconds * (2 ** (attempt - 1)), 60.0))
                    await asyncio.to_thread(_requeue_vector_sync_task, item_id, task_id)
                try:
                    async with _VECTOR_SEMAPHORE:
                        result = await asyncio.to_thread(_run_vector_sync_task, item_id, task_id)
                except Exception as exc:
                    result = {"complete": False, "claimed": True, "error": str(exc)}
                    print(f"[KnowledgeRAG] vector_sync_attempt_failed item_id={item_id} attempt={attempt + 1} error={exc}", flush=True)
                if result.get("complete") or not result.get("claimed", True):
                    break
            if not result.get("complete"):
                print(
                    f"[KnowledgeRAG] vector_sync_incomplete item_id={item_id} "
                    f"indexed={result.get('indexed', 0)}/{result.get('attempted', 0)} "
                    f"attempts={max_attempts} error={result.get('error') or ''}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[KnowledgeRAG] vector_sync_failed item_id={item_id} error={exc}", flush=True)
        finally:
            _VECTOR_ITEM_IDS.discard(task_key)
            if task_key in _VECTOR_DIRTY_ITEM_IDS:
                _VECTOR_DIRTY_ITEM_IDS.discard(task_key)
                if asyncio.current_task() and asyncio.current_task().cancelling():
                    _set_vector_item_state(item_id, "pending", 0)
                else:
                    _queue_item_vector_sync(item_id)
            # Paired-document semantic matching waits until both vector writes
            # have settled, then uses dense retrieval with a lexical fallback.
            try:
                await asyncio.to_thread(_maybe_finalize_project_for_item, item_id)
            except Exception as exc:
                print(f"[KnowledgeProject] finalize_after_index_failed item_id={item_id} error={exc}", flush=True)

    task = asyncio.create_task(run())
    _VECTOR_TASKS.add(task)
    task.add_done_callback(_VECTOR_TASKS.discard)
    return True


def _set_vector_item_state(item_id: int, status: str, indexed: int, error: str | None = None) -> None:
    with storage.connect() as conn:
        conn.execute(
            "UPDATE knowledge_item SET vector_status = ?, vector_indexed_chunks = ?, vector_error = ?, vector_updated_at = ? WHERE item_id = ?",
            (status, max(0, int(indexed)), (error or "")[:1000] or None, _now(), item_id),
        )
        conn.commit()


def _persist_vector_sync_task(item_id: int) -> int:
    now = _now()
    with storage.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_rag_task (item_id, status, attempt_count, create_time, update_time) VALUES (?, 'pending', 0, ?, ?)",
            (item_id, now, now),
        )
        conn.execute(
            "UPDATE knowledge_rag_task SET status = 'pending', attempt_count = 0, lock_owner = NULL, lease_until = NULL, last_error = NULL, update_time = ? WHERE item_id = ? AND status IN ('completed', 'failed')",
            (now, item_id),
        )
        row = conn.execute("SELECT task_id, status FROM knowledge_rag_task WHERE item_id = ?", (item_id,)).fetchone()
        conn.execute(
            "UPDATE knowledge_item SET vector_status = CASE WHEN vector_status = 'completed' THEN 'pending' ELSE vector_status END, vector_error = NULL, vector_updated_at = ? WHERE item_id = ?",
            (now, item_id),
        )
        conn.commit()
    if row is None:
        raise RuntimeError(f"Unable to persist vector task for item {item_id}")
    return int(row["task_id"])


def _requeue_vector_sync_task(item_id: int, task_id: int) -> None:
    now = _now()
    with storage.connect() as conn:
        cursor = conn.execute(
            "UPDATE knowledge_rag_task SET status = 'pending', lock_owner = NULL, lease_until = NULL, update_time = ? WHERE task_id = ? AND status = 'failed'",
            (now, task_id),
        )
        if cursor.rowcount:
            conn.execute(
                "UPDATE knowledge_item SET vector_status = 'pending', vector_error = NULL, vector_updated_at = ? WHERE item_id = ?",
                (now, item_id),
            )
        conn.commit()


def _run_vector_sync_task(item_id: int, task_id: int | None = None) -> dict[str, Any]:
    if task_id is None:
        task_id = _persist_vector_sync_task(item_id)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    lease_seconds = max(60, min(int(settings.rag_vector_task_lease_seconds or 21600), 86400))
    lease_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
    lock_owner = uuid.uuid4().hex
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE knowledge_rag_task
            SET status = 'processing', attempt_count = attempt_count + 1,
                lock_owner = ?, lease_until = ?, last_error = NULL, update_time = ?
            WHERE task_id = ?
              AND (status = 'pending' OR (status = 'processing' AND (lease_until IS NULL OR lease_until < ?)))
            """,
            (lock_owner, lease_until, now, task_id, now),
        )
        claimed = cursor.rowcount > 0
        conn.commit()
    if not claimed:
        return {"attempted": 0, "indexed": 0, "complete": False, "claimed": False}

    _set_vector_item_state(item_id, "indexing", 0)
    try:
        result = _sync_item_chunks_to_vector_store(item_id)
        attempted = int(result.get("attempted") or 0)
        indexed = int(result.get("indexed") or 0)
        complete = bool(result.get("complete"))
        status = "completed" if complete else "failed"
        error = None if complete else str(vector_store.status().get("vector_store_error") or "向量索引未完整寫入")
        with storage.connect() as conn:
            conn.execute(
                "UPDATE knowledge_rag_task SET status = ?, lock_owner = NULL, lease_until = NULL, last_error = ?, update_time = ? WHERE task_id = ? AND lock_owner = ?",
                (status, error, _now(), task_id, lock_owner),
            )
            conn.commit()
        _set_vector_item_state(item_id, status, indexed, error)
        return {"attempted": attempted, "indexed": indexed, "complete": complete, "claimed": True, "error": error}
    except Exception as exc:
        error = str(exc)[:1000]
        with storage.connect() as conn:
            conn.execute(
                "UPDATE knowledge_rag_task SET status = 'failed', lock_owner = NULL, lease_until = NULL, last_error = ?, update_time = ? WHERE task_id = ? AND lock_owner = ?",
                (error, _now(), task_id, lock_owner),
            )
            conn.commit()
        _set_vector_item_state(item_id, "failed", 0, error)
        raise


def _pending_vector_sync_item_ids(limit: int) -> list[int]:
    ensure_schema()
    now = _now()
    with storage.connect() as conn:
        # The desktop deployment uses one backend process and one local Qdrant
        # owner. A processing row from a previous process can safely be
        # returned to the queue; live jobs are protected by the process lock.
        conn.execute(
            "UPDATE knowledge_rag_task SET status = 'pending', lock_owner = NULL, lease_until = NULL, update_time = ? WHERE status = 'processing'",
            (now,),
        )
        rows = conn.execute(
            "SELECT item_id FROM knowledge_rag_task WHERE status = 'pending' ORDER BY update_time, task_id LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [int(row["item_id"]) for row in rows]


async def resume_pending_vector_sync_tasks(limit: int = 500) -> int:
    """Restore durable vector jobs for the active tenant without blocking the event loop."""
    if not vector_store.enabled():
        return 0
    vector_status = await asyncio.to_thread(vector_store.status)
    if not vector_status.get("vector_store_available"):
        return 0
    item_ids = await asyncio.to_thread(_pending_vector_sync_item_ids, limit)
    queued = 0
    for item_id in item_ids:
        queued += int(_queue_item_vector_sync(item_id))
    return queued


def prepare_deep_extract(item_id: int) -> dict[str, Any]:
    """Mark all image assets for an item as deep candidates before queueing work.

    This also makes the endpoint useful for historical items created before the
    layered parser introduced the deep_candidate column.
    """
    ensure_schema()
    item = get_item(item_id, include_chunks=False)
    if item.get("status") in {"pending", "processing"}:
        raise KnowledgeModuleError(40901, "基础解析尚未完成，请稍后再执行深度识别", 409)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_asset
            SET deep_candidate = 1, deep_status = 'pending'
            WHERE item_id = ? AND COALESCE(ocr_status, 'not_started') NOT IN ('recognized','no_text')
              AND (asset_type IN ('图片', 'image') OR LOWER(resource_path) LIKE '%.png'
                   OR LOWER(resource_path) LIKE '%.jpg' OR LOWER(resource_path) LIKE '%.jpeg'
                   OR LOWER(resource_path) LIKE '%.webp')
            """,
            (item_id,),
        )
        count_row = conn.execute(
            "SELECT COUNT(1) AS count FROM knowledge_asset WHERE item_id = ? AND deep_candidate = 1",
            (item_id,),
        ).fetchone()
        conn.commit()
    total = int(count_row["count"] or 0) if count_row else 0
    if not total and item.get("scan_page_count"):
        total = int(item["scan_page_count"])
    _set_item_status(
        item_id,
        "completed",
        100,
        None,
        deep_status="processing" if total else "completed",
        deep_progress=0 if total else 100,
        deep_total=total,
        deep_error="",
    )
    return {"item_id": item_id, "deep_status": "processing" if total else "completed", "deep_total": total}


def _deep_extract_complete(item_id: int, state: dict[str, Any]) -> bool:
    """Completed OCR is a no-op unless an asset or canonical scan page is missing."""
    if state.get("status") != "completed" or state.get("deep_status") != "completed" or state.get("deep_error"):
        return False
    with storage.connect() as conn:
        assets = conn.execute(
            "SELECT page_num,resource_path,source_chapter,ocr_status,deep_status FROM knowledge_asset WHERE item_id=?",
            (item_id,),
        ).fetchall()
        item = conn.execute("SELECT file_type,deep_candidate_pages_json FROM knowledge_item WHERE item_id=?", (item_id,)).fetchone()
    if any(a["ocr_status"] not in {"recognized", "no_text"} or a["deep_status"] != "completed" for a in assets):
        return False
    if item and str(item["file_type"] or "").lower() == "pdf":
        candidates = _loads(item["deep_candidate_pages_json"], [])
        required = {int(p) for p in candidates if str(p).isdigit() and int(p) > 0} if isinstance(candidates, list) else set()
        existing = {int(a["page_num"]) for a in assets if a["page_num"] is not None and (
            str(a["source_chapter"] or "").startswith("扫描页全文")
            or re.search(r"(?:^|[/\\])page[0-9]+_scan[.]png$", str(a["resource_path"] or "")))}
        if required - existing:
            return False
    return True


def queue_deep_extract(item_id: int) -> dict[str, Any]:
    state = item_status(item_id)
    if _tenant_item_key(item_id) in _DEEP_ITEM_IDS:
        return {**state, "queued": False, "action": "already_running", "message": "深度识别正在处理中，无需重复提交"}
    if _deep_extract_complete(item_id, state):
        if state.get("vector_status") == "failed":
            queued = _queue_item_vector_sync(item_id)
            return {**item_status(item_id), "queued": queued, "action": "index_retry",
                    "message": "图片识别已完成，仅重试失败的检索索引"}
        message = "深度识别已完成，没有待补识别的图片，无需重复处理"
        if state.get("vector_status") in {"pending", "indexing"}:
            message = "深度识别已完成，检索索引正在处理中，无需重复提交"
        return {**state, "queued": False, "action": "already_complete", "message": message}
    state = prepare_deep_extract(item_id)
    if state["deep_total"] and not _queue_deep_extract(item_id):
        state["queued"] = False
        state["message"] = "deep extraction already running"
    else:
        state["queued"] = bool(state["deep_total"])
    return {**state, **item_status(item_id), "message": "深度识别已启动，将补全未完成或失败的图片" if state.get("queued") else "没有待处理的图片"}


def queue_deep_extract_batch(item_ids: list[int]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item_id in item_ids:
        try:
            results.append(queue_deep_extract(int(item_id)))
        except KnowledgeModuleError as exc:
            results.append({"item_id": int(item_id), "queued": False, "status": "failed", "error": exc.message})
        except Exception as exc:
            results.append({"item_id": int(item_id), "queued": False, "status": "failed", "error": str(exc)[:240]})
    return {
        "total": len(results),
        "queued": sum(1 for item in results if item.get("queued")),
        "failed": sum(1 for item in results if item.get("status") == "failed"),
        "items": results,
    }


def _materialize_scan_page_assets(item_id: int) -> int:
    """OCR candidate PDF pages in the background and keep only useful pages."""
    with storage.connect() as conn:
        item = conn.execute(
            "SELECT original_file_path, source_name, deep_candidate_pages_json, content_text FROM knowledge_item WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        existing_pages = {
            int(row["page_num"])
            for row in conn.execute(
                "SELECT page_num,resource_path,source_chapter FROM knowledge_asset WHERE item_id = ? AND page_num IS NOT NULL",
                (item_id,),
            ).fetchall()
            if str(row["source_chapter"] or "").startswith("扫描页全文")
               or re.search(r"(?:^|[/\\])page[0-9]+_scan[.]png$", str(row["resource_path"] or ""))
        }
    if not item or not str(item["original_file_path"] or "").lower().endswith(".pdf"):
        return 0
    pages = _loads(item["deep_candidate_pages_json"], [])
    if not isinstance(pages, list):
        return 0
    page_numbers = sorted({int(page) for page in pages if str(page).isdigit() and int(page) > 0})
    if not page_numbers:
        return 0
    if fitz is None:
        raise RuntimeError("扫描页渲染不可用，请安装 PyMuPDF")
    source_path = _resolve_stored_path(item["original_file_path"])
    if not source_path or not source_path.exists():
        raise RuntimeError("扫描页原文件不存在，无法补识别")
    asset_dir = source_path.parent / "assets"
    asset_dir.mkdir(exist_ok=True)
    source_name = str(item["source_name"] or source_path.name)
    document_text = str(item["content_text"] or "")
    created = 0
    try:
        doc = fitz.open(str(source_path))
        for page_number in page_numbers:
            if page_number in existing_pages or page_number > len(doc):
                continue
            temporary = asset_dir / f".page{page_number}_scan.png"
            target = asset_dir / f"page{page_number}_scan.png"
            try:
                pixmap = doc[page_number - 1].get_pixmap(
                    matrix=fitz.Matrix(PDF_OCR_DPI / 72, PDF_OCR_DPI / 72),
                    alpha=False,
                )
                pixmap.save(str(temporary))
                page_ocr = ""
                temporary.replace(target)
                scan_profile = _image_profile(target)
                scan_profile["decorative"] = False
                if _save_image_asset(
                    item_id,
                    target,
                    target.name,
                    page_number=page_number,
                    source_chapter=f"扫描页全文 第{page_number}页",
                    profile=scan_profile,
                    duplicate_candidates=[],
                    context_text=page_ocr,
                    image_ocr=page_ocr,
                    deep_candidate=True,
                    defer_deep=True,
                ):
                    created += 1
                    existing_pages.add(page_number)
            finally:
                temporary.unlink(missing_ok=True)
    except Exception as exc:
        raise RuntimeError(f"扫描页渲染失败：{exc}") from exc
    finally:
        if 'doc' in locals():
            doc.close()
    return created


async def deep_extract_item(item_id: int) -> dict[str, Any]:
    """Run expensive OCR/structured extraction after the base item is usable."""
    ensure_schema()
    materialized = 0
    materialize_error = ""
    try:
        from .ocr_sources import restore_pdf_sources, restore_docx_sources
        await asyncio.to_thread(restore_pdf_sources, item_id)
        await asyncio.to_thread(restore_docx_sources, item_id)
    except Exception as exc:
        # Never read a historical replacement image whose original is unverified.
        _set_item_status(item_id, "completed", 100, None, deep_status="failed", deep_error=f"原始图片恢复失败：{exc}"[:1000])
        return {"item_id": item_id, "updated": 0, "failed": 1, "materialized": 0}
    try:
        # DOCX media is deliberately materialized here, after XML text and
        # chunks are already available. Scanned PDF pages use the same phase.
        materialized += await asyncio.to_thread(_materialize_docx_images, item_id)
        materialized += await asyncio.to_thread(_materialize_scan_page_assets, item_id)
    except Exception as exc:
        materialize_error = str(exc)[:1000]
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT a.*, i.source_name
            FROM knowledge_asset a JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE a.item_id = ? AND a.deep_candidate = 1
              AND COALESCE(a.deep_status, 'not_started') <> 'completed'
            ORDER BY a.asset_id
            """,
            (item_id,),
        ).fetchall()
    remaining = len(rows)
    with storage.connect() as conn:
        finished = conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_asset WHERE item_id=? AND deep_candidate=1 AND deep_status='completed'",
            (item_id,),
        ).fetchone()["n"]
    total = remaining + int(finished)
    if not remaining:
        await asyncio.to_thread(_refresh_asset_annotation_chunks, item_id, False)
        _queue_item_vector_sync(item_id)
        _set_item_status(item_id, "completed", 100, None, deep_status="failed" if materialize_error else "completed", deep_progress=100, deep_total=total, deep_error=materialize_error)
        return {"item_id": item_id, "total": total, "updated": 0, "failed": int(bool(materialize_error)), "materialized": materialized}
    _set_item_status(item_id, "completed", 100, None, deep_status="processing", deep_progress=int(finished / total * 100), deep_total=total, deep_error="")
    updated = 0
    failed = int(bool(materialize_error))
    for index, row in enumerate(rows, start=1):
        try:
            detail = await asyncio.to_thread(_reextract_one_asset, row, only_missing=False)
            if detail.get("status") == "updated":
                updated += 1
                with storage.connect() as conn:
                    conn.execute("UPDATE knowledge_asset SET deep_status = 'completed' WHERE asset_id = ?", (int(row["asset_id"]),))
                    conn.commit()
            else:
                failed += 1
                with storage.connect() as conn:
                    conn.execute("UPDATE knowledge_asset SET deep_status = 'failed' WHERE asset_id = ?", (int(row["asset_id"]),))
                    conn.commit()
        except Exception:
            failed += 1
            with storage.connect() as conn:
                conn.execute("UPDATE knowledge_asset SET deep_status = 'failed' WHERE asset_id = ?", (int(row["asset_id"]),))
                conn.commit()
        _set_item_status(item_id, "completed", 100, None, deep_status="processing", deep_progress=int((finished + index) / total * 100), deep_total=total)
    try:
        await asyncio.to_thread(_refresh_asset_annotation_chunks, item_id, False)
        _queue_item_vector_sync(item_id)
    except Exception:
        failed += 1
    _set_item_status(
        item_id, "completed", 100, None,
        deep_status="failed" if failed else "completed",
        deep_progress=100, deep_total=total,
        deep_error=materialize_error or (f"{failed} 张图片识别或索引处理失败，请重试" if failed else ""),
        extracted_image_count=_asset_count(item_id),
        image_category_stats=_asset_category_stats(item_id),
    )
    return {"item_id": item_id, "total": total, "updated": updated, "failed": failed, "materialized": materialized}


async def _process_item_once(item_id: int) -> None:
    ensure_schema()
    try:
        # Text and chunks were committed atomically at 78%. Do not unzip the
        # original or discard existing images when continuing this checkpoint.
        with storage.connect() as conn:
            saved = conn.execute(
                "SELECT status,process_progress,content_text,category_id,file_type FROM knowledge_item WHERE item_id=?",
                (item_id,),
            ).fetchone()
        if saved and saved["status"] == "processing" and int(saved["process_progress"] or 0) >= 78:
            await asyncio.to_thread(_refresh_asset_annotation_chunks, item_id, False)
            await asyncio.to_thread(_build_graph_for_item, item_id, saved["content_text"] or "", saved["category_id"])
            deferred = str(saved["file_type"]).lower() in {"pdf", "docx", "png", "jpg", "jpeg", "webp"}
            _set_item_status(item_id, "completed", 100, None,
                             deep_status="processing" if deferred else "completed",
                             deep_progress=0 if deferred else 100)
            _queue_item_vector_sync(item_id)
            if deferred:
                _queue_deep_extract(item_id)
            await asyncio.to_thread(_maybe_finalize_project_for_item, item_id)
            return
        _set_item_status(item_id, "processing", 12, None, total_pages=0, current_page=0, extracted_image_count=0, text_char_count=0, image_category_stats={})
        item = await asyncio.to_thread(get_item, item_id, include_chunks=False)
        file_path = _resolve_stored_path(item.get("original_file_path"))
        if not file_path or not file_path.exists():
            raise KnowledgeModuleError(40402, "原始文件不存在", 404)
        await asyncio.to_thread(_clear_assets_for_item, item_id)
        _set_item_status(item_id, "processing", 12, None, deep_status="not_started", deep_progress=0, deep_total=0, deep_error="", text_page_count=0, scan_page_count=0)
        # PDF rasterization and PaddleOCR are blocking CPU-bound work. Run the
        # extraction pipeline off the event loop so status/diagnostic requests
        # remain responsive while large documents are being processed.
        extraction_options = {"evidence": True} if item.get("evidence_kind") else {}
        extracted = await asyncio.to_thread(_extract_document, file_path, item["source_name"], item["file_type"], item_id, **extraction_options)
        _set_item_status(
            item_id,
            "processing",
            48,
            None,
            total_pages=extracted.get("total_pages", 0),
            current_page=extracted.get("processed_pages", 0),
            extracted_image_count=_asset_count(item_id),
            text_page_count=extracted.get("text_page_count", 0),
            scan_page_count=extracted.get("scan_page_count", 0),
        )
        candidate_pages = extracted.get("deep_candidate_pages", [])
        # Keep the foreground pass fast for every image-bearing document. OCR,
        # scan-page rendering and structured extraction run in the deep queue.
        defer_deep = item["file_type"].lower() in {"pdf", "docx", "png", "jpg", "jpeg", "webp"}
        with storage.connect() as conn:
            conn.execute("UPDATE knowledge_item SET deep_candidate_pages_json = ?, update_time = ? WHERE item_id = ?", (_json(candidate_pages), _now(), item_id))
            conn.commit()
        def store_extracted_text():
            text = _clean_text(extracted["text"])
            if not text and item["file_type"].lower() in {"png", "jpg", "jpeg", "webp"}:
                text = f"图片资料：{item['title']}"
            category_id = item.get("category_id") or _guess_category_id(item["source_name"], text)
            chunks = _split_chunks(text, extracted.get("pages", []), sections=extracted.get("sections"))
            if len(chunks) >= 100000:
                raise ValueError("文本片段超过 99999 个，请拆分文档后上传；未截断或标记为解析成功")
            project_id, doc_role = _document_rag_metadata(item_id)
            _set_item_status(item_id, "processing", 68, None)
            _delete_item_vectors(item_id)
            with storage.connect() as conn:
                conn.execute("DELETE FROM knowledge_chunk WHERE item_id = ?", (item_id,))
                conn.execute("DELETE FROM knowledge_relation WHERE item_id = ?", (item_id,))
                conn.execute("DELETE FROM knowledge_entity WHERE item_id = ?", (item_id,))
                conn.execute(
                    "UPDATE knowledge_item SET vector_status = ?, vector_indexed_chunks = 0, vector_error = NULL, vector_updated_at = ? WHERE item_id = ?",
                    ("pending" if vector_store.enabled() else "not_configured", _now(), item_id),
                )
                for index, chunk in enumerate(chunks):
                    tokens = _tokens(chunk["content"])
                    conn.execute(
                        """
                        INSERT INTO knowledge_chunk (
                            item_id, chunk_index, section_title, page_number, content, tokens_json, embedding_json,
                            project_id, doc_role, chunk_kind, section_path, create_time
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id, index, chunk.get("section_title"), chunk.get("page_number"), chunk["content"],
                            _json(tokens), _json(_hash_embedding(tokens)), project_id, doc_role,
                            chunk.get("chunk_kind") or "document_text",
                            chunk.get("section_path") or chunk.get("section_title") or "", _now(),
                        ),
                    )
                conn.execute(
                    """
                    UPDATE knowledge_item
                    SET category_id = ?, content_text = ?, status = 'processing', process_progress = 78,
                        process_error = NULL, is_expired = ?, total_pages = ?, current_page = ?,
                        extracted_image_count = ?, text_char_count = ?, image_category_stats_json = ?, update_time = ?
                    WHERE item_id = ?
                    """,
                    (
                        category_id,
                        text,
                        _is_expired(item.get("valid_until")),
                        int(extracted.get("total_pages", 0) or 0),
                        int(extracted.get("processed_pages", extracted.get("total_pages", 0)) or 0),
                        _asset_count(item_id),
                        len(text),
                        _json(_asset_category_stats(item_id)),
                        _now(),
                        item_id,
                    ),
                )
                conn.execute("UPDATE knowledge_item_version SET content_text = ? WHERE item_id = ?", (text, item_id))
                conn.commit()
            return text, category_id

        text, category_id = await asyncio.to_thread(store_extracted_text)
        _set_item_status(item_id, "processing", 82, None, extracted_image_count=_asset_count(item_id), text_char_count=len(text), image_category_stats=_asset_category_stats(item_id))
        if not defer_deep:
            await _semantic_confirm_assets(item_id, text)
        _set_item_status(item_id, "processing", 94, None, extracted_image_count=_asset_count(item_id), text_char_count=len(text), image_category_stats=_asset_category_stats(item_id))
        # A scanned PDF may have no embedded image rows yet. Its candidate
        # pages still need to trigger the background OCR/materialization pass.
        deep_count = _deep_candidate_asset_count(item_id) + (
            len(candidate_pages) if item["file_type"].lower() == "pdf" else 0
        ) + int(extracted.get("deferred_image_count", 0) or 0)
        await asyncio.to_thread(_refresh_asset_annotation_chunks, item_id, False)
        await asyncio.to_thread(_build_graph_for_item, item_id, text, category_id)
        _set_item_status(
            item_id, "completed", 100, None,
            extracted_image_count=_asset_count(item_id), text_char_count=len(text),
            image_category_stats=_asset_category_stats(item_id),
            deep_status="processing" if defer_deep and deep_count else "completed",
            deep_progress=0 if defer_deep and deep_count else 100,
            deep_total=deep_count,
            deep_error="" if _local_ocr_available() or not defer_deep or not deep_count else "OCR unavailable; text-only parsing completed",
        )
        _queue_item_vector_sync(item_id)
        # MySQL chunks are immediately useful for lexical project matching.
        # Persist that first-pass map now; the vector worker will trigger a
        # second pass with semantic scores when Qdrant is ready.
        await asyncio.to_thread(_maybe_finalize_project_for_item, item_id)
        if defer_deep and deep_count:
            _queue_deep_extract(item_id)
        tags = _loads(item.get("tags_json") if "tags_json" in item else None, item.get("tags", []))
        if "批量导入" in tags and len(tags) > 1:
            _build_import_project_relation(item_id, item["title"], str(tags[1]), category_id)
    except Exception as exc:
        if _is_database_locked_error(exc):
            raise
        message = exc.message if isinstance(exc, KnowledgeModuleError) else str(exc)
        _set_item_status(item_id, "failed", 100, message or "处理失败")
        await asyncio.to_thread(_maybe_finalize_project_for_item, item_id)


# These scalar aggregates avoid loading image payloads or document versions
# during progress polling. All queries use the current tenant's connection.
_ITEM_ASSET_PROGRESS_SQL = """
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id = i.item_id) AS asset_count,
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id = i.item_id
        AND a.deep_candidate = 1 AND a.deep_status = 'completed') AS deep_completed_count,
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id = i.item_id
        AND a.deep_candidate = 1 AND a.deep_status = 'failed') AS deep_failed_count,
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id=i.item_id AND a.ocr_status='recognized') AS ocr_recognized_count,
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id=i.item_id AND a.ocr_status='no_text') AS ocr_no_text_count,
    (SELECT COUNT(1) FROM knowledge_asset a WHERE a.item_id=i.item_id AND a.ocr_status NOT IN ('recognized','no_text')) AS ocr_unprocessed_count
"""


def list_items(*, category_id: int | None = None, keyword: str = "", status: str = "", file_type: str = "", bid_type: str = "", bid_scenario: str = "", length_level: str = "", include_expired: bool = True, limit: int = 100) -> dict[str, Any]:
    ensure_schema()
    bid_type = _normalize_dimension_value(bid_type, "bid_type")
    bid_scenario = _normalize_dimension_value(bid_scenario, "bid_scenario")
    length_level = _normalize_dimension_value(length_level, "length_level")
    clauses: list[str] = []
    params: list[Any] = []
    if category_id:
        clauses.append("i.category_id = ?")
        params.append(category_id)
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if file_type:
        clauses.append("i.file_type = ?")
        params.append(file_type.lower())
    if bid_type:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset ad WHERE ad.item_id = i.item_id AND COALESCE(ad.bid_type, '') = ?)")
        params.append(bid_type)
    if bid_scenario:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset ads WHERE ads.item_id = i.item_id AND COALESCE(ads.bid_scenario, '') = ?)")
        params.append(bid_scenario)
    if length_level:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset al WHERE al.item_id = i.item_id AND COALESCE(al.length_level, '') = ?)")
        params.append(length_level)
    if not include_expired:
        clauses.append("i.is_expired = 0")
    if keyword:
        clauses.append("(i.title LIKE ? OR i.content_text LIKE ? OR i.tags_json LIKE ? OR EXISTS (SELECT 1 FROM knowledge_chunk ko WHERE ko.item_id=i.item_id AND ko.chunk_kind='ocr_text' AND ko.content LIKE ?))")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT i.*, c.name AS category_name,
                   (SELECT resource_path FROM knowledge_asset a WHERE a.item_id = i.item_id AND a.asset_type = '图片' LIMIT 1) AS thumbnail_path,
                   {_ITEM_ASSET_PROGRESS_SQL},
                   (SELECT COUNT(1) FROM knowledge_chunk k WHERE k.item_id = i.item_id) AS chunk_count
            FROM knowledge_item i
            LEFT JOIN knowledge_category c ON c.category_id = i.category_id
            {where}
            ORDER BY i.update_time DESC
            LIMIT ?
            """,
            (*params, max(1, min(limit, 500))),
        ).fetchall()
    return {"items": [_row_item(row, detail=False) for row in rows], "total": len(rows)}


def list_assets(*, category_id: int | None = None, keyword: str = "", image_category: str = "", employee_role: str = "", archive_year: str = "", bid_type: str = "", bid_scenario: str = "", length_level: str = "", asset_sub_type: str = "", limit: int = 200, asset_sub_types: list[str] | None = None) -> dict[str, Any]:
    ensure_schema()
    bid_type = _normalize_dimension_value(bid_type, "bid_type")
    bid_scenario = _normalize_dimension_value(bid_scenario, "bid_scenario")
    length_level = _normalize_dimension_value(length_level, "length_level")
    clauses = ["a.asset_type = '图片'"]
    params: list[Any] = []
    if category_id:
        clauses.append("i.category_id = ?")
        params.append(category_id)
    if image_category:
        clauses.append("COALESCE(a.category, '其他附件类') = ?")
        params.append(image_category)
    if bid_type:
        clauses.append("COALESCE(a.bid_type, '') = ?")
        params.append(bid_type)
    if bid_scenario:
        clauses.append("COALESCE(a.bid_scenario, '') = ?")
        params.append(bid_scenario)
    if length_level:
        clauses.append("COALESCE(a.length_level, '') = ?")
        params.append(length_level)
    if asset_sub_type:
        clauses.append("COALESCE(a.asset_sub_type, '') = ?")
        params.append(asset_sub_type.strip())
    if asset_sub_types:
        types = sorted(set(asset_sub_types))
        clauses.append("COALESCE(a.asset_sub_type, '') IN (" + ", ".join("?" for _ in types) + ")")
        params.extend(types)
    if employee_role:
        clauses.append("COALESCE(a.employee_role, '') = ?")
        params.append(employee_role)
    if archive_year:
        year = archive_year[:4]
        clauses.append("(substr(COALESCE(a.archive_date, ''), 1, 4) = ? OR COALESCE(a.contract_info, '') LIKE ?)")
        params.extend([year, f"%{year}%"])
    if keyword:
        clauses.append("(i.title LIKE ? OR i.source_name LIKE ? OR a.ai_description LIKE ? OR COALESCE(a.annotation, '') LIKE ? OR COALESCE(a.ocr_text, '') LIKE ? OR a.tags_json LIKE ? OR a.source_chapter LIKE ? OR COALESCE(a.person_info, '') LIKE ? OR COALESCE(a.employee_info, '') LIKE ? OR COALESCE(a.employee_role, '') LIKE ? OR COALESCE(a.contract_info, '') LIKE ? OR COALESCE(a.archive_date, '') LIKE ? OR COALESCE(a.structured_info, '') LIKE ? OR COALESCE(a.asset_sub_type, '') LIKE ? OR COALESCE(a.cert_no, '') LIKE ? OR COALESCE(a.holder_name, '') LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like, like, like, like, like, like, like, like, like, like, like, like, like])
    where = " AND ".join(clauses)
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*, i.title AS item_title, i.source_name, i.category_id, i.status AS item_status, i.evidence_kind, i.evidence_review_json, i.is_expired AS evidence_item_expired, c.name AS category_name
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            LEFT JOIN knowledge_category c ON c.category_id = i.category_id
            WHERE {where}
            ORDER BY COALESCE(a.archive_date, '') DESC, a.asset_id DESC
            LIMIT ?
            """,
            (*params, max(1, min(limit, 500))),
        ).fetchall()
    assets: list[dict[str, Any]] = []
    for row in rows:
        asset = _row_asset(row)
        asset.update(
            {
                "item_title": row["item_title"],
                "source_name": row["source_name"],
                "item_category_id": row["category_id"],
                "item_category_name": row["category_name"],
                "item_status": row["item_status"],
            }
        )
        assets.append(asset)
    return {"assets": assets, "total": len(assets)}


def _project_summary(row: Any, *, connection=None) -> dict[str, Any]:
    from .document_analysis import coverage
    from .recovery import workflow_state
    workflow = workflow_state(int(row["project_id"]), connection=connection)
    def document_summary(prefix: str, item_id_key: str, name_key: str, status_key: str) -> dict[str, Any]:
        ai_analysis_key = f"{prefix}_ai_analysis_json"
        ai_status_key = f"{prefix}_ai_status"
        return {
            "processing": item_status(int(row[item_id_key]), connection=connection) if row[item_id_key] else None,
            "item_id": row[item_id_key],
            "file_name": row[name_key] if name_key in row.keys() else None,
            "status": row[status_key] if status_key in row.keys() else None,
            "vector_status": row[f"{prefix}_vector_status"] if f"{prefix}_vector_status" in row.keys() else "unknown",
            "vector_indexed_chunks": int(row[f"{prefix}_vector_indexed_chunks"] or 0) if f"{prefix}_vector_indexed_chunks" in row.keys() else 0,
            "vector_error": row[f"{prefix}_vector_error"] if f"{prefix}_vector_error" in row.keys() else None,
            "vector_updated_at": row[f"{prefix}_vector_updated_at"] if f"{prefix}_vector_updated_at" in row.keys() else None,
            "content_preview": row[f"{prefix}_content_preview"] if f"{prefix}_content_preview" in row.keys() else "",
            "total_pages": int(row[f"{prefix}_total_pages"] or 0) if f"{prefix}_total_pages" in row.keys() else 0,
            "text_char_count": int(row[f"{prefix}_text_char_count"] or 0) if f"{prefix}_text_char_count" in row.keys() else 0,
            "process_error": row[f"{prefix}_process_error"] if f"{prefix}_process_error" in row.keys() else None,
            "chunk_count": int(row[f"{prefix}_chunk_count"] or 0) if f"{prefix}_chunk_count" in row.keys() else 0,
            "asset_count": int(row[f"{prefix}_asset_count"] or 0) if f"{prefix}_asset_count" in row.keys() else 0,
            "deep_status": row[f"{prefix}_deep_status"] if f"{prefix}_deep_status" in row.keys() else None,
            "update_time": row[f"{prefix}_update_time"] if f"{prefix}_update_time" in row.keys() else None,
            "ai_interpretation": {
                "coverage": coverage(int(row[item_id_key]), connection=connection) if row[item_id_key] else None,
                "status": row[ai_status_key] if ai_status_key in row.keys() else None,
                "provider": row[f"{prefix}_ai_provider"] if f"{prefix}_ai_provider" in row.keys() else None,
                "model": row[f"{prefix}_ai_model"] if f"{prefix}_ai_model" in row.keys() else None,
                "summary": row[f"{prefix}_ai_summary"] if f"{prefix}_ai_summary" in row.keys() else None,
                "analysis": _loads(row[ai_analysis_key] if ai_analysis_key in row.keys() else None, {}),
                "error": row[f"{prefix}_ai_error"] if f"{prefix}_ai_error" in row.keys() else None,
                "source_chunk_count": int(row[f"{prefix}_ai_source_chunks"] or 0) if f"{prefix}_ai_source_chunks" in row.keys() else 0,
                "result_count": int(row[f"{prefix}_ai_result_count"] or 0) if f"{prefix}_ai_result_count" in row.keys() else 0,
            },
        }
    documents = {
        "tender": document_summary("tender", "tender_doc_id", "tender_file_name", "tender_status"),
        "bid": document_summary("bid", "bid_doc_id", "bid_file_name", "bid_status"),
    }
    processing = [doc.get("processing") or {} for doc in documents.values()]
    active = any(d.get("deep_status") in {"pending", "processing"} or d.get("vector_status") in {"pending", "indexing"} for d in processing)
    issue = any(d.get("deep_status") == "failed" or d.get("ocr_unprocessed_count", 0) > 0 or d.get("vector_status") == "failed" for d in processing)
    return {
        "project_id": int(row["project_id"]),
        "workflow": workflow,
        "project_name": row["project_name"],
        "tender_doc_id": row["tender_doc_id"],
        "bid_doc_id": row["bid_doc_id"],
        "status": ("processing" if active or (workflow and workflow["status"] in {"pending", "processing"})
                   else "needs_attention" if issue or (workflow and workflow["status"] == "failed") else row["status"]),
        "format_template": {
            "id": row["format_template_id"] if "format_template_id" in row.keys() else None,
            "name": row["format_template_name"] if "format_template_name" in row.keys() else None,
            "status": (row["format_template_status"] if "format_template_status" in row.keys() else None) or "not_started",
            "error": row["format_template_error"] if "format_template_error" in row.keys() else None,
            "rules": _loads(row["format_template_rules"] if "format_template_rules" in row.keys() else None, {}),
            "source_file_name": row["format_template_source_file"] if "format_template_source_file" in row.keys() else None,
            "updated_at": row["format_template_updated_at"] if "format_template_updated_at" in row.keys() else None,
            "is_default": bool(row["format_template_default"]) if "format_template_default" in row.keys() else False,
        },
        "tender_file_name": row["tender_file_name"] if "tender_file_name" in row.keys() else None,
        "bid_file_name": row["bid_file_name"] if "bid_file_name" in row.keys() else None,
        "tender_status": row["tender_status"] if "tender_status" in row.keys() else None,
        "bid_status": row["bid_status"] if "bid_status" in row.keys() else None,
        "requirement_count": int(row["requirement_count"] or 0) if "requirement_count" in row.keys() else 0,
        "mapped_count": int(row["mapped_count"] or 0) if "mapped_count" in row.keys() else 0,
        "tender_asset_count": int(row["tender_asset_count"] or 0) if "tender_asset_count" in row.keys() else 0,
        "bid_asset_count": int(row["bid_asset_count"] or 0) if "bid_asset_count" in row.keys() else 0,
        "documents": documents,
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


async def upload_project(project_name: str, tender_file: UploadFile, bid_file: UploadFile) -> dict[str, Any]:
    ensure_schema()
    if not project_name.strip():
        raise KnowledgeModuleError(40051, "项目名称不能为空", 400)
    tender_result = await upload_items([tender_file], category_id=None, tags="招标资料", valid_until=None)
    if not tender_result["items"]:
        raise KnowledgeModuleError(40052, "招标文件上传失败", 400)
    bid_result = await upload_items([bid_file], category_id=None, tags="成品投标文件", valid_until=None)
    if not bid_result["items"]:
        raise KnowledgeModuleError(40053, "投标文件上传失败", 400)
    tender_id = int(tender_result["items"][0]["item_id"])
    bid_id = int(bid_result["items"][0]["item_id"])
    now = _now()
    with storage.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO bid_project (project_name, tender_doc_id, bid_doc_id, status, format_template_status, format_template_rules, create_time, update_time) VALUES (?, ?, ?, 'processing', 'pending', '{}', ?, ?)",
            (project_name.strip(), tender_id, bid_id, now, now),
        )
        project_id = int(cursor.lastrowid)
        from .recovery import prepare_project
        prepare_project(project_id, conn)
        conn.commit()
    return get_project(project_id)


async def process_project_upload_tasks(project_id: int, tender_id: int, bid_id: int) -> None:
    from .recovery import run_project
    await run_project(project_id, tender_id, bid_id)


def _validate_document_analysis(value: Any) -> dict[str, Any]:
    """Reject malformed model output before it can become a successful result."""
    list_fields = {
        "requirements", "scoring_points", "qualification_requirements",
        "invalid_bid_clauses", "chapters", "keywords", "solution_capabilities",
        "qualification_assets", "performance_cases", "service_metrics",
    }
    if not isinstance(value, dict) or not value:
        raise ValueError("AI 返回内容不是有效的解读 JSON 对象，可能已被截断")
    if not (set(value) & (list_fields | {"summary", "project_info"})):
        raise ValueError("AI 返回内容缺少文档解读字段")
    if "summary" in value and not isinstance(value["summary"], str):
        raise ValueError("AI 返回的 summary 必须为文本")
    if "project_info" in value and not isinstance(value["project_info"], dict):
        raise ValueError("AI 返回的 project_info 必须为对象")
    for field in list_fields & set(value):
        if not isinstance(value[field], list):
            raise ValueError(f"AI 返回的 {field} 必须为数组")
    return value


def _analysis_result_count(role: str, analysis: dict[str, Any]) -> int:
    fields = ("requirements", "scoring_points", "qualification_requirements", "invalid_bid_clauses", "chapters") if role == "tender" else ("chapters", "solution_capabilities", "qualification_assets", "performance_cases", "service_metrics")
    return sum(len(value) for key in fields if isinstance(value := analysis.get(key), list))


async def interpret_knowledge_item(item_id: int, *, role: str) -> dict[str, Any]:
    from .document_analysis import interpret
    return await interpret(item_id, role=role)


def extract_project_template(project_id: int) -> dict[str, Any]:
    """Run format extraction as a non-critical background task."""
    return word_template_service.extract_project_template(project_id)


def mark_project_template_pending(project_id: int) -> dict[str, Any]:
    return word_template_service.mark_project_template_pending(project_id)


def get_project_template(project_id: int) -> dict[str, Any]:
    try:
        return word_template_service.get_project_template(project_id)
    except FileNotFoundError as exc:
        raise KnowledgeModuleError(40452, "项目不存在", 404) from exc


def activate_project_template(project_id: int) -> dict[str, Any]:
    try:
        return word_template_service.activate_project_template(project_id)
    except FileNotFoundError as exc:
        raise KnowledgeModuleError(40452, "项目不存在", 404) from exc
    except ValueError as exc:
        raise KnowledgeModuleError(40952, str(exc), 409) from exc


def project_template_download_path(project_id: int) -> tuple[Path, str]:
    try:
        return word_template_service.template_download_path(project_id)
    except FileNotFoundError as exc:
        raise KnowledgeModuleError(40453, str(exc), 404) from exc


def active_format_template_path() -> Path | None:
    try:
        ensure_schema()
        return word_template_service.active_template_path()
    except Exception:
        return None


def list_projects(limit: int = 200) -> list[dict[str, Any]]:
    ensure_schema()
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, t.source_name AS tender_file_name, b.source_name AS bid_file_name,
                   t.status AS tender_status, b.status AS bid_status,
                   ti.status AS tender_ai_status, ti.provider AS tender_ai_provider,
                   ti.model AS tender_ai_model, ti.summary AS tender_ai_summary,
                   ti.analysis_json AS tender_ai_analysis_json, ti.error AS tender_ai_error,
                   ti.source_chunk_count AS tender_ai_source_chunks, ti.result_count AS tender_ai_result_count,
                   bi.status AS bid_ai_status, bi.provider AS bid_ai_provider,
                   bi.model AS bid_ai_model, bi.summary AS bid_ai_summary,
                   bi.analysis_json AS bid_ai_analysis_json, bi.error AS bid_ai_error,
                   bi.source_chunk_count AS bid_ai_source_chunks, bi.result_count AS bid_ai_result_count,
                   t.vector_status AS tender_vector_status, b.vector_status AS bid_vector_status,
                   t.vector_indexed_chunks AS tender_vector_indexed_chunks,
                   b.vector_indexed_chunks AS bid_vector_indexed_chunks,
                   t.vector_error AS tender_vector_error, b.vector_error AS bid_vector_error,
                   t.vector_updated_at AS tender_vector_updated_at,
                   b.vector_updated_at AS bid_vector_updated_at,
                   t.process_error AS tender_process_error, b.process_error AS bid_process_error,
                   substr(COALESCE(t.content_text, ''), 1, 280) AS tender_content_preview,
                   substr(COALESCE(b.content_text, ''), 1, 280) AS bid_content_preview,
                   t.total_pages AS tender_total_pages, b.total_pages AS bid_total_pages,
                   t.text_char_count AS tender_text_char_count, b.text_char_count AS bid_text_char_count,
                   (SELECT COUNT(*) FROM knowledge_chunk tk WHERE tk.item_id = p.tender_doc_id AND tk.chunk_kind IN ('document_text','ocr_text')) AS tender_chunk_count,
                   (SELECT COUNT(*) FROM knowledge_chunk bk WHERE bk.item_id = p.bid_doc_id AND bk.chunk_kind IN ('document_text','ocr_text')) AS bid_chunk_count,
                   (SELECT COUNT(*) FROM knowledge_chunk k WHERE k.item_id = p.tender_doc_id AND k.chunk_index < 100000) AS requirement_count,
                   (SELECT COUNT(*) FROM requirement_solution_map m WHERE m.project_id = p.project_id) AS mapped_count,
                   (SELECT COUNT(*) FROM knowledge_asset a WHERE a.item_id = p.tender_doc_id) AS tender_asset_count,
                   (SELECT COUNT(*) FROM knowledge_asset a WHERE a.item_id = p.bid_doc_id) AS bid_asset_count,
                   t.deep_status AS tender_deep_status, b.deep_status AS bid_deep_status,
                   t.update_time AS tender_update_time, b.update_time AS bid_update_time
            FROM bid_project p
            LEFT JOIN knowledge_item t ON t.item_id = p.tender_doc_id
            LEFT JOIN knowledge_item b ON b.item_id = p.bid_doc_id
            LEFT JOIN knowledge_item_interpretation ti ON ti.item_id = p.tender_doc_id
            LEFT JOIN knowledge_item_interpretation bi ON bi.item_id = p.bid_doc_id
            ORDER BY p.update_time DESC, p.project_id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [_project_summary(row, connection=conn) for row in rows]


def get_project(project_id: int) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT p.*, t.source_name AS tender_file_name, b.source_name AS bid_file_name,
                   t.status AS tender_status, b.status AS bid_status,
                   ti.status AS tender_ai_status, ti.provider AS tender_ai_provider,
                   ti.model AS tender_ai_model, ti.summary AS tender_ai_summary,
                   ti.analysis_json AS tender_ai_analysis_json, ti.error AS tender_ai_error,
                   ti.source_chunk_count AS tender_ai_source_chunks, ti.result_count AS tender_ai_result_count,
                   bi.status AS bid_ai_status, bi.provider AS bid_ai_provider,
                   bi.model AS bid_ai_model, bi.summary AS bid_ai_summary,
                   bi.analysis_json AS bid_ai_analysis_json, bi.error AS bid_ai_error,
                   bi.source_chunk_count AS bid_ai_source_chunks, bi.result_count AS bid_ai_result_count,
                   t.vector_status AS tender_vector_status, b.vector_status AS bid_vector_status,
                   t.vector_indexed_chunks AS tender_vector_indexed_chunks,
                   b.vector_indexed_chunks AS bid_vector_indexed_chunks,
                   t.vector_error AS tender_vector_error, b.vector_error AS bid_vector_error,
                   t.vector_updated_at AS tender_vector_updated_at,
                   b.vector_updated_at AS bid_vector_updated_at,
                   t.process_error AS tender_process_error, b.process_error AS bid_process_error,
                   substr(COALESCE(t.content_text, ''), 1, 280) AS tender_content_preview,
                   substr(COALESCE(b.content_text, ''), 1, 280) AS bid_content_preview,
                   t.total_pages AS tender_total_pages, b.total_pages AS bid_total_pages,
                   t.text_char_count AS tender_text_char_count, b.text_char_count AS bid_text_char_count,
                   (SELECT COUNT(*) FROM knowledge_chunk tk WHERE tk.item_id = p.tender_doc_id AND tk.chunk_kind IN ('document_text','ocr_text')) AS tender_chunk_count,
                   (SELECT COUNT(*) FROM knowledge_chunk bk WHERE bk.item_id = p.bid_doc_id AND bk.chunk_kind IN ('document_text','ocr_text')) AS bid_chunk_count,
                   (SELECT COUNT(*) FROM knowledge_chunk k WHERE k.item_id = p.tender_doc_id AND k.chunk_index < 100000) AS requirement_count,
                   (SELECT COUNT(*) FROM requirement_solution_map m WHERE m.project_id = p.project_id) AS mapped_count,
                   (SELECT COUNT(*) FROM knowledge_asset a WHERE a.item_id = p.tender_doc_id) AS tender_asset_count,
                   (SELECT COUNT(*) FROM knowledge_asset a WHERE a.item_id = p.bid_doc_id) AS bid_asset_count,
                   t.deep_status AS tender_deep_status, b.deep_status AS bid_deep_status,
                   t.update_time AS tender_update_time, b.update_time AS bid_update_time
            FROM bid_project p
            LEFT JOIN knowledge_item t ON t.item_id = p.tender_doc_id
            LEFT JOIN knowledge_item b ON b.item_id = p.bid_doc_id
            LEFT JOIN knowledge_item_interpretation ti ON ti.item_id = p.tender_doc_id
            LEFT JOIN knowledge_item_interpretation bi ON bi.item_id = p.bid_doc_id
            WHERE p.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeModuleError(40451, "项目不存在", 404)
        maps = conn.execute(
            "SELECT map_id, project_id, requirement_id, requirement_content, solution_id, solution_content, match_score, source_chapter, create_time FROM requirement_solution_map WHERE project_id = ? ORDER BY match_score DESC, map_id",
            (project_id,),
        ).fetchall()
    result = _project_summary(row)
    result["maps"] = [dict(item) for item in maps]
    return result


def rename_project(project_id: int, project_name: str) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        cursor = conn.execute("UPDATE bid_project SET project_name = ?, update_time = ? WHERE project_id = ?", (project_name.strip(), _now(), project_id))
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeModuleError(40451, "项目不存在", 404)
    return get_project(project_id)


def delete_project(project_id: int) -> None:
    ensure_schema()
    template_path = word_template_service.project_template_path(project_id)
    with storage.connect() as conn:
        row = conn.execute("SELECT project_id FROM bid_project WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise KnowledgeModuleError(40451, "项目不存在", 404)
        conn.execute("UPDATE knowledge_asset SET project_id = NULL WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM bid_project WHERE project_id = ?", (project_id,))
        conn.commit()
    word_template_service.remove_template_file(template_path)


def match_project(
    project_id: int,
    *,
    min_score: float = 0.08,
    limit: int = 500,
    use_dense: bool = True,
    completion_status: str = "completed",
) -> dict[str, Any]:
    project = get_project(project_id)
    tender_id = project.get("tender_doc_id")
    bid_id = project.get("bid_doc_id")
    if not tender_id or not bid_id:
        raise KnowledgeModuleError(40054, "项目缺少招标文件或投标文件", 400)
    with storage.connect() as conn:
        requirements = conn.execute(
            "SELECT * FROM knowledge_chunk WHERE item_id = ? AND chunk_index < 100000 ORDER BY chunk_index",
            (tender_id,),
        ).fetchall()
        solutions = conn.execute(
            "SELECT * FROM knowledge_chunk WHERE item_id = ? AND chunk_index < 100000 ORDER BY chunk_index",
            (bid_id,),
        ).fetchall()
    if not requirements or not solutions:
        raise KnowledgeModuleError(40951, "两个文件仍在解析中，请完成后再匹配", 409)
    solution_by_id = {int(row["chunk_id"]): row for row in solutions}
    solution_tokens = {chunk_id: _loads(row["tokens_json"], []) for chunk_id, row in solution_by_id.items()}
    token_to_solution_ids: dict[str, set[int]] = defaultdict(set)
    for solution_id, tokens in solution_tokens.items():
        for token in set(tokens):
            token_to_solution_ids[token].add(solution_id)

    requirement_queries = [
        f"{row['section_title'] or ''}\n{row['content'] or ''}".strip()
        for row in requirements
    ]
    dense_results = vector_store.search_knowledge_chunks_batch(
        requirement_queries,
        limit=24,
        filters={"item_id": int(bid_id)},
    ) if vector_store.qdrant_enabled() and use_dense else [[] for _ in requirements]
    matches: list[dict[str, Any]] = []
    for requirement, dense_hits in zip(requirements, dense_results, strict=False):
        requirement_text = str(requirement["content"] or "").strip()
        req_tokens = _tokens(requirement_text)
        lexical_candidate_ids: set[int] = set()
        for token in set(req_tokens):
            lexical_candidate_ids.update(token_to_solution_ids.get(token, ()))
        lexical_rank = sorted(
            (
                (
                    _keyword_score(req_tokens, solution_tokens[solution_id], str(solution_by_id[solution_id]["content"] or "")),
                    solution_id,
                )
                for solution_id in lexical_candidate_ids
            ),
            reverse=True,
        )[:40]
        lexical_scores = {solution_id: score for score, solution_id in lexical_rank}
        dense_scores = {
            int(hit.get("chunk_id") or 0): float(hit.get("score") or 0.0)
            for hit in dense_hits
            if int(hit.get("chunk_id") or 0) in solution_by_id
        }
        candidate_ids = set(lexical_scores) | set(dense_scores)
        if not candidate_ids:
            continue
        ranked = []
        for solution_id in candidate_ids:
            lexical_score = lexical_scores.get(solution_id, 0.0)
            dense_score = dense_scores.get(solution_id, 0.0)
            # Dense similarity enriches term matching, while lexical evidence
            # keeps unrelated nearest neighbours from becoming false maps.
            score = lexical_score if not dense_scores else lexical_score * 0.55 + dense_score * 0.45
            solution_title = str(solution_by_id[solution_id]["section_title"] or "")
            heading_overlap = _keyword_score(_tokens(str(requirement["section_title"] or "")), _tokens(solution_title), solution_title)
            if heading_overlap and lexical_score:
                score = min(1.0, score + min(0.06, heading_overlap * 0.06))
            if not lexical_score and dense_score < max(0.42, min_score):
                continue
            ranked.append((score, solution_id))
        if not ranked:
            continue
        score, solution_id = max(ranked)
        if score < min_score:
            continue
        solution = solution_by_id[solution_id]
        matches.append({
            "requirement_id": int(requirement["chunk_id"]),
            "requirement_content": requirement_text,
            "solution_id": solution_id,
            "solution_content": str(solution["content"] or ""),
            "match_score": score,
            "source_chapter": solution["section_title"] or requirement["section_title"] or "",
        })
    matches = sorted(matches, key=lambda item: item["match_score"], reverse=True)[: max(1, min(limit, 5000))]
    now = _now()
    with storage.connect() as conn:
        conn.execute("DELETE FROM requirement_solution_map WHERE project_id = ?", (project_id,))
        conn.executemany(
            "INSERT INTO requirement_solution_map (project_id, requirement_id, requirement_content, solution_id, solution_content, match_score, source_chapter, create_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(project_id, item["requirement_id"], item["requirement_content"], item["solution_id"], item["solution_content"], item["match_score"], item["source_chapter"], now) for item in matches],
        )
        conn.execute("UPDATE bid_project SET status = ?, update_time = ? WHERE project_id = ?", (completion_status, now, project_id))
        conn.commit()
    return {**get_project(project_id), "matched_count": len(matches), "matches": matches}


def _maybe_finalize_project_for_item(item_id: int) -> None:
    """Publish a lexical map immediately, then refine it after vector writes."""
    ensure_schema()
    with storage.connect() as conn:
        project = conn.execute(
            """
            SELECT p.project_id, p.tender_doc_id, p.bid_doc_id,
                   t.status AS tender_status, b.status AS bid_status
            FROM bid_project p
            LEFT JOIN knowledge_item t ON t.item_id = p.tender_doc_id
            LEFT JOIN knowledge_item b ON b.item_id = p.bid_doc_id
            WHERE p.tender_doc_id = ? OR p.bid_doc_id = ?
            ORDER BY p.project_id DESC LIMIT 1
            """,
            (item_id, item_id),
        ).fetchone()
        if project is None:
            return
        project_id = int(project["project_id"])
        has_failed_document = project["tender_status"] == "failed" or project["bid_status"] == "failed"
        both_completed = project["tender_status"] == "completed" and project["bid_status"] == "completed"
        vector_pending = any(
            _tenant_item_key(int(document_id or 0)) in _VECTOR_ITEM_IDS
            for document_id in (project["tender_doc_id"], project["bid_doc_id"])
        )
        if has_failed_document:
            next_status = "failed"
        elif both_completed and vector_pending:
            next_status = "indexing"
        elif both_completed:
            next_status = "matching"
        else:
            next_status = "processing"
        conn.execute("UPDATE bid_project SET status = ?, update_time = ? WHERE project_id = ?", (next_status, _now(), project_id))
        conn.commit()
    if not both_completed or has_failed_document:
        return
    try:
        match_project(
            project_id,
            use_dense=not vector_pending,
            completion_status="indexing" if vector_pending else "completed",
        )
    except Exception as exc:
        # Parsing remains successful even if semantic mapping needs a manual retry.
        with storage.connect() as conn:
            conn.execute("UPDATE bid_project SET status = 'needs_attention', update_time = ? WHERE project_id = ?", (_now(), project_id))
            conn.commit()
        print(f"[KnowledgeProject] auto_match_deferred project_id={project_id} error={exc}", flush=True)


def reuse_project_solutions(project_id: int, *, target_bid_id: int | None, target_project_id: int | None, map_ids: list[int]) -> dict[str, Any]:
    source = get_project(project_id)
    selected = [item for item in source.get("maps", []) if not map_ids or int(item["map_id"]) in map_ids]
    if target_bid_id:
        from app.schemas.bid_graphic import ChapterMaterialSelectionRequest
        from app.services import bid_graphic_service

        with storage.connect() as conn:
            chapter_rows = conn.execute(
                "SELECT chapter_id, chapter_title FROM bid_catalog_chapters WHERE bid_id = ? ORDER BY level, sort_order, chapter_id",
                (target_bid_id,),
            ).fetchall()
        applied = 0
        applied_chapters: list[int] = []
        for item in selected:
            source_chapter = str(item.get("source_chapter") or "").strip()
            if not source_chapter or not chapter_rows:
                continue
            ranked = [
                (_keyword_score(_tokens(source_chapter), _tokens(str(row["chapter_title"] or "")), str(row["chapter_title"] or "")), row)
                for row in chapter_rows
            ]
            score, chapter = max(ranked, key=lambda value: value[0])
            if score <= 0:
                continue
            current = bid_graphic_service._chapter_material_selection(target_bid_id, int(chapter["chapter_id"])) or {}
            selected_chunks = list(current.get("selected_chunk_ids", []))
            solution_id = int(item.get("solution_id") or 0)
            if solution_id and solution_id not in selected_chunks:
                selected_chunks.append(solution_id)
            bid_graphic_service.save_chapter_materials(
                target_bid_id,
                int(chapter["chapter_id"]),
                ChapterMaterialSelectionRequest(
                    selected_asset_ids=list(current.get("selected_asset_ids", [])),
                    selected_chunk_ids=selected_chunks,
                ),
            )
            applied += 1
            if int(chapter["chapter_id"]) not in applied_chapters:
                applied_chapters.append(int(chapter["chapter_id"]))
        return {"source_project_id": project_id, "target_bid_id": target_bid_id, "selected_count": len(selected), "applied_count": applied, "applied_chapters": applied_chapters}
    if target_project_id:
        target = get_project(target_project_id)
        now = _now()
        with storage.connect() as conn:
            conn.executemany(
                "INSERT INTO requirement_solution_map (project_id, requirement_id, requirement_content, solution_id, solution_content, match_score, source_chapter, create_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(target_project_id, item["requirement_id"], item["requirement_content"], item["solution_id"], item["solution_content"], item["match_score"], item["source_chapter"], now) for item in selected],
            )
            conn.execute("UPDATE bid_project SET update_time = ? WHERE project_id = ?", (now, target_project_id))
            conn.commit()
        return {"source_project_id": project_id, "target_project_id": target_project_id, "selected_count": len(selected), "target": target}
    return {"source_project_id": project_id, "selected_count": len(selected), "solutions": selected}


def get_asset(asset_id: int) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT a.*, i.title AS item_title, i.source_name, i.category_id, i.status AS item_status, i.evidence_kind, i.evidence_review_json, i.is_expired AS evidence_item_expired, c.name AS category_name
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            LEFT JOIN knowledge_category c ON c.category_id = i.category_id
            WHERE a.asset_id = ?
            """,
            (asset_id,),
        ).fetchone()
    if row is None:
        raise KnowledgeModuleError(40421, "图片资源不存在", 404)
    asset = _row_asset(row)
    asset.update(
        {
            "item_title": row["item_title"],
            "source_name": row["source_name"],
            "item_category_id": row["category_id"],
            "item_category_name": row["category_name"],
            "item_status": row["item_status"],
        }
    )
    return asset


def update_asset_annotation(asset_id: int, annotation: str) -> dict[str, Any]:
    ensure_schema()
    annotation = (annotation or "").strip()[:2400]
    now = _now()
    with storage.connect() as conn:
        row = conn.execute("SELECT item_id FROM knowledge_asset WHERE asset_id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KnowledgeModuleError(40421, "图片资源不存在", 404)
        item_id = int(row["item_id"])
        conn.execute("UPDATE knowledge_asset SET annotation = ? WHERE asset_id = ?", (annotation, asset_id))
        conn.execute("UPDATE knowledge_item SET update_time = ? WHERE item_id = ?", (now, item_id))
        conn.commit()
    _refresh_asset_annotation_chunks(item_id)
    return get_asset(asset_id)


def update_asset_metadata(asset_id: int, *, bid_type: str | None = None, length_level: str | None = None, extend_tags: list[str] | None = None) -> dict[str, Any]:
    ensure_schema()
    now = _now()
    tags = list(dict.fromkeys(str(tag).strip() for tag in (extend_tags or []) if str(tag).strip()))[:30]
    with storage.connect() as conn:
        row = conn.execute("SELECT item_id FROM knowledge_asset WHERE asset_id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KnowledgeModuleError(40421, "图片资源不存在", 404)
        conn.execute(
            "UPDATE knowledge_asset SET bid_type = ?, length_level = ?, extend_tags = ? WHERE asset_id = ?",
            ((bid_type or "").strip() or None, (length_level or "").strip() or None, _json(tags), asset_id),
        )
        conn.execute("UPDATE knowledge_item SET update_time = ? WHERE item_id = ?", (now, int(row["item_id"])))
        conn.commit()
    _refresh_asset_annotation_chunks(int(row["item_id"]))
    return get_asset(asset_id)


def _validate_dimension_value(value: str | None, kind: str) -> str:
    normalized = _normalize_dimension_value(value, kind)
    if not normalized:
        return ""
    options = BID_SCENARIO_OPTIONS if kind == "bid_scenario" else LENGTH_LEVEL_OPTIONS
    if normalized not in options:
        raise KnowledgeModuleError(40032, f"unsupported {kind} value", 400)
    return normalized


def _dimension_tags_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = _loads(value, {})
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def update_asset_dimensions(asset_id: int, payload: KnowledgeAssetDimensionsUpdate) -> dict[str, Any]:
    ensure_schema()
    provided = set(getattr(payload, "model_fields_set", set()))
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM knowledge_asset WHERE asset_id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KnowledgeModuleError(40421, "\u56fe\u7247\u8d44\u6e90\u4e0d\u5b58\u5728", 404)
        current_scenario = str(_row_value(row, "bid_scenario", "") or "")
        current_length = str(_row_value(row, "length_level", "") or "")
        next_scenario = current_scenario
        next_length = current_length
        if "bid_scenario" in provided:
            next_scenario = _validate_dimension_value(payload.bid_scenario, "bid_scenario")
        if "length_level" in provided:
            next_length = _validate_dimension_value(payload.length_level, "length_level")
        tags = _dimension_tags_dict(_row_value(row, "dimension_tags", "{}"))
        if "dimension_tags" in provided and payload.dimension_tags is not None:
            tags.update(payload.dimension_tags)
        manual = tags.get("manual") if isinstance(tags.get("manual"), dict) else {}
        confidence = tags.get("confidence") if isinstance(tags.get("confidence"), dict) else {}
        matches = tags.get("matches") if isinstance(tags.get("matches"), dict) else {}
        for field in ("bid_scenario", "length_level"):
            if field in provided:
                manual[field] = True
                confidence[field] = 1.0
                matches[field] = ["manual"]
        if manual:
            tags["manual"] = manual
        if confidence:
            tags["confidence"] = confidence
        if matches:
            tags["matches"] = matches
        tags["source"] = "manual" if any(field in provided for field in ("bid_scenario", "length_level")) else tags.get("source", "manual")
        conn.execute(
            "UPDATE knowledge_asset SET bid_scenario = ?, length_level = ?, dimension_tags = ? WHERE asset_id = ?",
            (next_scenario, next_length, _json(tags), asset_id),
        )
        conn.execute("UPDATE knowledge_item SET update_time = ? WHERE item_id = ?", (_now(), int(row["item_id"])))
        conn.commit()
    return get_asset(asset_id)


def get_dimensions() -> dict[str, Any]:
    return {
        "dimensions": [
            {
                "key": "bid_scenario",
                "name": "\u6807\u4e66\u573a\u666f",
                "options": [{"value": value, "label": value} for value in BID_SCENARIO_OPTIONS],
            },
            {
                "key": "length_level",
                "name": "\u7bc7\u5e45\u7b49\u7ea7",
                "options": [
                    {"value": value, "label": value, "aliases": [alias]}
                    for value, alias in zip(LENGTH_LEVEL_OPTIONS, ("short", "standard", "detailed"))
                ],
            },
        ],
        "legacy_dimensions": [
            {"key": "bid_type", "alias_of": "bid_scenario", "options": [{"value": value, "label": value} for value in BID_SCENARIO_OPTIONS]}
        ],
        "extensible": True,
    }


def batch_tag_assets(payload: KnowledgeAssetBatchTagRequest) -> dict[str, Any]:
    ensure_schema()
    requested_scenario = None if payload.bid_scenario is None else _validate_dimension_value(payload.bid_scenario, "bid_scenario")
    requested_length = None if payload.length_level is None else _validate_dimension_value(payload.length_level, "length_level")
    clauses = ["a.asset_type = '\u56fe\u7247'"]
    params: list[Any] = []
    if payload.category:
        clauses.append("COALESCE(a.category, '') = ?")
        params.append(payload.category.strip())
    if payload.asset_sub_type:
        clauses.append("COALESCE(a.asset_sub_type, '') = ?")
        params.append(payload.asset_sub_type.strip())
    if payload.item_ids:
        placeholders = ",".join("?" for _ in payload.item_ids)
        clauses.append(f"a.item_id IN ({placeholders})")
        params.extend(payload.item_ids)
    if payload.asset_ids:
        placeholders = ",".join("?" for _ in payload.asset_ids)
        clauses.append(f"a.asset_id IN ({placeholders})")
        params.extend(payload.asset_ids)
    if payload.source:
        source_like = f"%{payload.source.strip()}%"
        clauses.append("(i.source_name LIKE ? OR COALESCE(a.source_chapter, '') LIKE ? OR COALESCE(a.storage_location, '') LIKE ?)")
        params.extend([source_like, source_like, source_like])
    if payload.source_from:
        clauses.append("substr(COALESCE(NULLIF(a.archive_date, ''), a.create_time), 1, 10) >= ?")
        params.append(payload.source_from[:10])
    if payload.source_to:
        clauses.append("substr(COALESCE(NULLIF(a.archive_date, ''), a.create_time), 1, 10) <= ?")
        params.append(payload.source_to[:10])
    params.append(max(1, min(int(payload.limit), 10000)))
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*, i.title AS item_title, i.source_name, i.content_text
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.asset_id
            LIMIT ?
            """,
            params,
        ).fetchall()
    updated = 0
    skipped = 0
    failed = 0
    details: list[dict[str, Any]] = []
    for row in rows:
        asset_id = int(row["asset_id"])
        try:
            inferred = _infer_asset_dimensions(row)
            tags = _dimension_tags_dict(_row_value(row, "dimension_tags", "{}"))
            manual = tags.get("manual") if isinstance(tags.get("manual"), dict) else {}
            confidence = tags.get("confidence") if isinstance(tags.get("confidence"), dict) else {}
            matches = tags.get("matches") if isinstance(tags.get("matches"), dict) else {}
            current_scenario = str(_row_value(row, "bid_scenario", "") or "")
            current_length = str(_row_value(row, "length_level", "") or "")
            next_scenario = current_scenario
            next_length = current_length
            asset_changes: list[str] = []
            blocked_manual: list[str] = []
            for field, current, requested, threshold in (
                ("bid_scenario", current_scenario, requested_scenario, "bid_scenario"),
                ("length_level", current_length, requested_length, "length_level"),
            ):
                if manual.get(field) and not payload.overwrite_manual:
                    blocked_manual.append(field)
                    continue
                if payload.only_missing and current:
                    continue
                value = requested if requested is not None else str(inferred.get(field) or "")
                value_confidence = 1.0 if requested is not None else float((inferred.get("confidence") or {}).get(field) or 0.0)
                if not value or value_confidence < DIMENSION_CONFIDENCE_THRESHOLD:
                    continue
                if field == "bid_scenario":
                    next_scenario = value
                else:
                    next_length = value
                confidence[field] = round(value_confidence, 3)
                matches[field] = ["requested_value"] if requested is not None else (inferred.get("matches") or {}).get(field, [])
                asset_changes.append(field)
            if not asset_changes:
                skipped += 1
                details.append({"asset_id": asset_id, "status": "skipped", "reason": "manual_or_low_confidence_or_existing", "blocked_manual": blocked_manual})
                continue
            tags["source"] = "batch" if requested_scenario is not None or requested_length is not None else "auto"
            tags["confidence"] = confidence
            tags["matches"] = matches
            tags["manual"] = manual
            with storage.connect() as conn:
                conn.execute(
                    "UPDATE knowledge_asset SET bid_scenario = ?, length_level = ?, dimension_tags = ? WHERE asset_id = ?",
                    (next_scenario, next_length, _json(tags), asset_id),
                )
                conn.execute("UPDATE knowledge_item SET update_time = ? WHERE item_id = ?", (_now(), int(row["item_id"])))
                conn.commit()
            updated += 1
            details.append({"asset_id": asset_id, "status": "updated", "dimensions": asset_changes, "values": {"bid_scenario": next_scenario, "length_level": next_length}})
        except Exception as exc:
            failed += 1
            details.append({"asset_id": asset_id, "status": "failed", "reason": str(exc)[:240]})
    return {
        "scanned": len(rows),
        "updated": updated,
        "success": updated,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }


async def regenerate_asset_annotations(asset_ids: list[int] | None = None, *, only_missing: bool = True, limit: int = 120) -> dict[str, Any]:
    ensure_schema()
    clauses = ["a.asset_type = '图片'"]
    params: list[Any] = []
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        clauses.append(f"a.asset_id IN ({placeholders})")
        params.extend(asset_ids)
    if only_missing:
        clauses.append("COALESCE(a.annotation, '') = ''")
    params.append(max(1, min(limit, 1000)))
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*, i.title AS item_title, i.content_text
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.asset_id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    updated = 0
    touched_items: set[int] = set()
    print(f"[KnowledgeAsset] regenerate_annotations_start scanned={len(rows)} only_missing={only_missing}", flush=True)
    for row in rows:
        asset = _row_asset(row)
        annotation = await _generate_asset_annotation_with_ai(asset, row["item_title"], row["content_text"] or "")
        if not annotation:
            continue
        with storage.connect() as conn:
            conn.execute("UPDATE knowledge_asset SET annotation = ? WHERE asset_id = ?", (annotation[:2400], asset["asset_id"]))
            conn.commit()
        updated += 1
        touched_items.add(int(asset["item_id"]))
    for item_id in touched_items:
        _refresh_asset_annotation_chunks(item_id)
    print(f"[KnowledgeAsset] regenerate_annotations_done updated={updated} scanned={len(rows)}", flush=True)
    return {"updated": updated, "scanned": len(rows)}


def cleanup_invalid_assets(limit: int = 5000) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        rows = conn.execute("SELECT a.* FROM knowledge_asset a JOIN knowledge_item i ON i.item_id = a.item_id WHERE a.asset_type = '图片' AND i.evidence_kind IS NULL ORDER BY a.asset_id LIMIT ?", (max(1, min(limit, 10000)),)).fetchall()
    removed = 0
    linked_duplicates = 0
    updated_meta = 0
    cleared_bad_info = 0
    touched_items: set[int] = set()
    keepers: list[dict[str, Any]] = []
    for row in rows:
        asset = _row_asset(row)
        path = _resolve_stored_path(asset.get("resource_path"))
        touched_items.add(int(asset["item_id"]))
        if not path or not path.exists():
            with storage.connect() as conn:
                conn.execute("DELETE FROM knowledge_asset WHERE asset_id = ?", (asset["asset_id"],))
                conn.commit()
            removed += 1
            continue
        profile = _image_profile(path)
        bad_info_updates: dict[str, str] = {}
        if _looks_like_raw_asset_payload(asset.get("annotation")):
            bad_info_updates["annotation"] = ""
        if _looks_like_raw_asset_payload(asset.get("ai_description")):
            bad_info_updates["ai_description"] = ""
        if bad_info_updates:
            with storage.connect() as conn:
                assignments = ", ".join(f"{name} = ?" for name in bad_info_updates)
                conn.execute(f"UPDATE knowledge_asset SET {assignments} WHERE asset_id = ?", (*bad_info_updates.values(), asset["asset_id"]))
                conn.commit()
            asset.update(bad_info_updates)
            cleared_bad_info += 1
        if profile.get("decorative"):
            with storage.connect() as conn:
                conn.execute("DELETE FROM knowledge_asset WHERE asset_id = ?", (asset["asset_id"],))
                conn.commit()
            _safe_unlink_asset_file(path)
            removed += 1
            continue
        duplicate = _find_similar_profile(profile.get("hash"), keepers)
        if duplicate:
            with storage.connect() as conn:
                conn.execute(
                    "UPDATE knowledge_asset SET resource_path = ?, duplicate_of_asset_id = ?, image_hash = ?, image_width = ?, image_height = ? WHERE asset_id = ?",
                    (duplicate["resource_path"], duplicate["asset_id"], profile.get("hash"), profile.get("width"), profile.get("height"), asset["asset_id"]),
                )
                conn.commit()
            _safe_unlink_asset_file(path, keep_path=_resolve_stored_path(duplicate.get("resource_path")))
            linked_duplicates += 1
            continue
        keepers.append({**asset, "image_hash": profile.get("hash"), "image_width": profile.get("width"), "image_height": profile.get("height")})
        if profile.get("hash") and (asset.get("image_hash") != profile.get("hash") or asset.get("image_width") != profile.get("width") or asset.get("image_height") != profile.get("height")):
            with storage.connect() as conn:
                conn.execute(
                    "UPDATE knowledge_asset SET image_hash = ?, image_width = ?, image_height = ? WHERE asset_id = ?",
                    (profile.get("hash"), profile.get("width"), profile.get("height"), asset["asset_id"]),
                )
                conn.commit()
            updated_meta += 1
    for item_id in touched_items:
        with storage.connect() as conn:
            conn.execute(
                "UPDATE knowledge_item SET extracted_image_count = ?, image_category_stats_json = ?, update_time = ? WHERE item_id = ?",
                (_asset_count(item_id), _json(_asset_category_stats(item_id)), _now(), item_id),
            )
            conn.commit()
        _refresh_asset_annotation_chunks(item_id)
    return {"removed": removed, "linked_duplicates": linked_duplicates, "updated_meta": updated_meta, "cleared_bad_info": cleared_bad_info, "scanned": len(rows)}



def _evidence_asset_fields(row: Any) -> dict[str, Any]:
    kind = _row_value(row, "evidence_kind", None)
    if not kind:
        return {}
    review = _loads(_row_value(row, "evidence_review_json", None), {}) or {}
    return {
        "evidence_kind": kind,
        "evidence_review": review,
        "evidence_expired": bool(_row_value(row, "evidence_item_expired", False)) or bool(_is_expired(review.get("valid_until"))),
    }


def is_verified_evidence_asset(asset: dict[str, Any], bidder_name: str = "") -> bool:
    review = asset.get("evidence_review") or {}
    bidder = re.sub(r"\s+", "", bidder_name).casefold()
    subject = re.sub(r"\s+", "", str(review.get("subject") or "")).casefold()
    return bool(
        (not bidder or bidder == subject)
        and asset.get("evidence_kind") in EVIDENCE_CATEGORIES
        and review.get("status") == "verified"
        and review.get("identifier")
        and asset.get("item_status") == "completed"
        and not asset.get("evidence_expired")
    )



def _evidence_suggestions(item: dict[str, Any]) -> dict[str, str]:
    kind = item["evidence_kind"]
    values: dict[str, set[str]] = defaultdict(set)

    def add(field: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values[field].add(value.strip())

    for asset in item["assets"]:
        structured = asset.get("structured_info") or {}
        fields = structured.get("fields") or {}
        add("subject", asset.get("holder_name") or fields.get("holder_name") or fields.get("subject_name"))
        if kind.startswith("业绩"):
            contract = asset.get("contract_info") or {}
            add("identifier", fields.get("contract_no") or contract.get("contract_no") or contract.get("project_name"))
        elif kind.startswith("车辆"):
            add("identifier", fields.get("plate_no") or fields.get("vehicle_plate") or fields.get("vin"))
        else:
            add("identifier", asset.get("cert_no") or fields.get("cert_no") or fields.get("certificate_no"))
        date_text = asset.get("expire_date") or fields.get("valid_until") or ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_text)):
            add("valid_until", date_text)

    text = "\n".join([item.get("content_text") or "", *(a.get("ocr_text") or "" for a in item["assets"])])
    if kind == "营业执照":
        if not values["identifier"] and "统一社会信用代码" in text:
            for identifier in re.findall(r"(?<![A-Z0-9])[A-Z0-9]{18}(?![A-Z0-9])", text.upper()):
                add("identifier", identifier)
        if not values["subject"]:
            for name in re.findall(r"名\s*称\s*[：:]?\s*([^\r\n]{2,100}?(?:股份有限公司|有限责任公司|有限公司))", text):
                add("subject", name)
    elif kind.startswith("车辆") and not values["identifier"]:
        for plate in re.findall(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-Z0-9]{5,6}(?![A-Z0-9])", text.upper()):
            add("identifier", plate)
    elif kind.startswith("业绩") and not values["identifier"]:
        for name in re.findall(r"(?:合同编号|项目编号|项目名称)\s*[：:]\s*([^\r\n]{2,100})", text):
            add("identifier", name)
    return {key: next(iter(found)) for key, found in values.items() if len(found) == 1}


def review_evidence_item(item_id: int, payload: KnowledgeEvidenceReview) -> dict[str, Any]:
    tenant = get_current_tenant()
    if tenant is None:
        raise KnowledgeModuleError(40101, "请登录后核验企业证明材料", 401)
    item = get_item(item_id, include_chunks=False)
    kind = item.get("evidence_kind")
    if not kind:
        raise KnowledgeModuleError(40001, "请从企业证明材料入口上传原件后核验")
    subject = payload.subject.strip()
    identifier = re.sub(r"\s+", "", payload.identifier).upper() if kind.startswith("车辆") else payload.identifier.strip()
    valid_until = payload.valid_until.isoformat() if payload.valid_until else None
    if payload.verified:
        from app.services.bid_case_reference_service import is_current_company_evidence

        if not is_current_company_evidence(item):
            raise KnowledgeModuleError(40001, "历史标书只能作参考，请上传独立的原始证明材料")
        if item["status"] != "completed" or item["deep_status"] in {"pending", "processing"}:
            raise KnowledgeModuleError(40901, "材料仍在处理中，请等待识别结束", 409)
        pages = {asset.get("page_num") for asset in item["assets"] if asset.get("resource_path")}
        if not set(range(1, max(1, item["total_pages"]) + 1)).issubset(pages):
            raise KnowledgeModuleError(40901, "原件页未提取完整，请重试深度识别", 409)
        if not subject or not identifier:
            raise KnowledgeModuleError(40001, "请核对并填写证明主体和编号")
        if kind.startswith("车辆") and not re.fullmatch(r"[\u4e00-\u9fff][A-Z][A-Z0-9]{5,6}|[A-HJ-NPR-Z0-9]{17}", identifier):
            raise KnowledgeModuleError(40001, "请填写有效格式的车牌号或车辆识别代号")
        if (kind == "食品经营许可证" or kind.endswith("体系认证证书")) and not valid_until:
            raise KnowledgeModuleError(40001, "请填写证件的有效期截止日")
        if _is_expired(valid_until):
            raise KnowledgeModuleError(40001, "该材料已到期，请上传新的有效证明")
    now = _now()
    record = {"status": "verified" if payload.verified else "withdrawn", "subject": subject,
              "identifier": identifier, "valid_until": valid_until,
              "reviewed_by": tenant.username, "reviewed_at": now}
    with storage.connect() as conn:
        cursor = conn.execute(
            """UPDATE knowledge_item SET evidence_review_json = ?, valid_until = ?, is_expired = ?, update_time = ?
               WHERE item_id = ? AND evidence_kind = ? AND update_time = ?""",
            (_json(record), valid_until, _is_expired(valid_until), now, item_id, kind, item["update_time"]),
        )
        if not cursor.rowcount:
            raise KnowledgeModuleError(40901, "材料状态已变化，请重新打开资料后核验", 409)
        conn.commit()
    return get_item(item_id)


def verified_evidence_assets(kinds: set[str]) -> list[dict[str, Any]]:
    if not kinds:
        return []
    placeholders = ", ".join("?" for _ in kinds)
    with storage.connect() as conn:
        rows = conn.execute(
            f"""SELECT a.*, i.title AS item_title, i.source_name, i.status AS item_status,
                       i.evidence_kind, i.evidence_review_json, i.is_expired AS evidence_item_expired
                FROM knowledge_asset a JOIN knowledge_item i ON i.item_id = a.item_id
                WHERE i.evidence_kind IN ({placeholders}) AND i.evidence_review_json IS NOT NULL
                  AND i.status = 'completed' AND a.page_num IS NOT NULL
                ORDER BY i.item_id DESC, a.page_num, a.asset_id""", tuple(sorted(kinds)),
        ).fetchall()
    assets = [{**_row_asset(row), "source_name": row["source_name"], "item_title": row["item_title"],
               "item_status": row["item_status"]} for row in rows]
    return [asset for asset in assets if is_verified_evidence_asset(asset)]


def get_item(item_id: int, *, include_chunks: bool = True) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        row = conn.execute(
            f"""
            SELECT i.*, c.name AS category_name,
                   {_ITEM_ASSET_PROGRESS_SQL},
                   (SELECT COUNT(1) FROM knowledge_chunk k WHERE k.item_id = i.item_id) AS chunk_count
            FROM knowledge_item i
            LEFT JOIN knowledge_category c ON c.category_id = i.category_id
            WHERE i.item_id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeModuleError(40401, "知识条目不存在", 404)
        item = _row_item(row, detail=True)
        item["assets"] = [_row_asset(asset) for asset in conn.execute("SELECT * FROM knowledge_asset WHERE item_id = ? ORDER BY asset_id", (item_id,)).fetchall()]
        if item.get("evidence_kind"):
            item["evidence_suggestions"] = _evidence_suggestions(item)
        item["versions"] = [_row_version(v) for v in conn.execute("SELECT * FROM knowledge_item_version WHERE item_id = ? ORDER BY version_no DESC", (item_id,)).fetchall()]
        if include_chunks:
            item["chunks"] = [_row_chunk(chunk) for chunk in conn.execute("SELECT * FROM knowledge_chunk WHERE item_id = ? ORDER BY chunk_index LIMIT 50", (item_id,)).fetchall()]
    return item


def update_item(item_id: int, payload: KnowledgeItemUpdate) -> dict[str, Any]:
    ensure_schema()
    values = payload.model_dump(exclude_unset=True)
    assignments: list[str] = []
    params: list[Any] = []
    if "title" in values and values["title"] is not None:
        assignments.append("title = ?")
        params.append(values["title"].strip())
    if "category_id" in values:
        assignments.append("category_id = ?")
        params.append(values["category_id"])
    if "tags" in values and values["tags"] is not None:
        assignments.append("tags_json = ?")
        params.append(_json(_parse_tags(values["tags"])))
    if "valid_until" in values:
        assignments.append("valid_until = ?")
        params.append(values["valid_until"])
        assignments.append("is_expired = ?")
        params.append(_is_expired(values["valid_until"]))
    if "status" in values and values["status"] is not None:
        assignments.append("status = ?")
        params.append(values["status"])
    if not assignments:
        return get_item(item_id)
    assignments.append("update_time = ?")
    params.extend([_now(), item_id])
    with storage.connect() as conn:
        cursor = conn.execute(f"UPDATE knowledge_item SET {', '.join(assignments)} WHERE item_id = ?", params)
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeModuleError(40401, "知识条目不存在", 404)
    return get_item(item_id)


def retry_item(item_id: int) -> dict[str, Any]:
    ensure_schema()
    item = get_item(item_id, include_chunks=False)
    if item["status"] == "processing":
        return item
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_item
            SET status = 'pending', process_progress = 5, process_error = NULL,
                processing_lock = NULL, deep_status = 'not_started', deep_progress = 0,
                deep_total = 0, deep_error = NULL, deep_candidate_pages_json = '[]', update_time = ?
            WHERE item_id = ?
            """,
            (_now(), item_id),
        )
        conn.commit()
    return get_item(item_id, include_chunks=False)


def delete_item(item_id: int) -> None:
    ensure_schema()
    item = get_item(item_id, include_chunks=False)
    file_path = _resolve_stored_path(item.get("original_file_path"))
    _delete_item_vectors(item_id)
    with storage.connect() as conn:
        cursor = conn.execute("DELETE FROM knowledge_item WHERE item_id = ?", (item_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeModuleError(40401, "知识条目不存在", 404)
    if file_path and file_path.exists():
        root = _knowledge_root().resolve()
        try:
            parent = file_path.parent.resolve()
            if root in parent.parents or parent == root:
                shutil.rmtree(parent, ignore_errors=True)
        except Exception:
            pass


def item_status(item_id: int, *, connection=None) -> dict[str, Any]:
    """Return live progress without reading full text, assets, or version payloads."""
    from contextlib import nullcontext
    ensure_schema()
    with (nullcontext(connection) if connection is not None else storage.connect()) as conn:
        row = conn.execute(
            f"""
            SELECT i.item_id, i.status, i.process_progress, i.process_error, i.is_expired,
                   i.total_pages, i.current_page, i.text_page_count, i.scan_page_count,
                   i.deep_status, i.deep_progress, i.deep_total, i.deep_error,
                   i.text_char_count, i.image_category_stats_json, i.update_time,
                   i.vector_status, i.vector_indexed_chunks, i.vector_error, i.vector_updated_at,
                   {_ITEM_ASSET_PROGRESS_SQL},
                   (SELECT COUNT(1) FROM knowledge_chunk k WHERE k.item_id = i.item_id) AS chunk_count
            FROM knowledge_item i
            WHERE i.item_id = ?
            """,
            (item_id,),
        ).fetchone()
    if row is None:
        raise KnowledgeModuleError(40401, "知识条目不存在", 404)
    result = {
        "item_id": int(row["item_id"]),
        "status": row["status"],
        "process_progress": int(row["process_progress"] or 0),
        "process_error": row["process_error"],
        "is_expired": bool(row["is_expired"]),
        "total_pages": int(row["total_pages"] or 0),
        "current_page": int(row["current_page"] or 0),
        "text_page_count": int(row["text_page_count"] or 0),
        "scan_page_count": int(row["scan_page_count"] or 0),
        "deep_status": row["deep_status"] or "not_started",
        "deep_progress": int(row["deep_progress"] or 0),
        "deep_total": int(row["deep_total"] or 0),
        "deep_error": row["deep_error"],
        "text_char_count": int(row["text_char_count"] or 0),
        "image_category_stats": _loads(row["image_category_stats_json"], {}),
        "vector_status": row["vector_status"] or "unknown",
        "vector_indexed_chunks": int(row["vector_indexed_chunks"] or 0),
        "vector_error": row["vector_error"],
        "vector_updated_at": row["vector_updated_at"],
        "chunk_count": int(row["chunk_count"] or 0),
        "update_time": row["update_time"],
    }
    result.update(_item_live_asset_progress(row))
    return result



def version_history(item_id: int) -> dict[str, Any]:
    item = get_item(item_id, include_chunks=False)
    with storage.connect() as conn:
        same_title_rows = conn.execute(
            "SELECT item_id, title, source_name, version_no, status, create_time, update_time FROM knowledge_item WHERE title = ? ORDER BY version_no DESC, item_id DESC",
            (item["title"],),
        ).fetchall()
    return {"item_id": item_id, "versions": [dict(row) for row in same_title_rows]}


def _rank_dimension_fallback(results: list[dict[str, Any]], payload: KnowledgeSearchRequest) -> list[dict[str, Any]]:
    requested = {
        "bid_type": payload.bid_type,
        "bid_scenario": payload.bid_scenario,
        "length_level": payload.length_level,
    }
    requested = {key: value for key, value in requested.items() if value}
    if not requested or not results:
        return results
    item_ids = list(dict.fromkeys(int(result.get("item_id") or 0) for result in results if int(result.get("item_id") or 0) > 0))
    if not item_ids:
        return results
    placeholders = ",".join("?" for _ in item_ids)
    with storage.connect() as conn:
        rows = conn.execute(
            f"SELECT item_id, bid_type, bid_scenario, length_level FROM knowledge_asset WHERE item_id IN ({placeholders})",
            item_ids,
        ).fetchall()
    scores: dict[int, int] = defaultdict(int)
    for row in rows:
        item_id = int(row["item_id"])
        for field, value in requested.items():
            if str(row[field] or "") == str(value):
                scores[item_id] = scores.get(item_id, 0) + 1
    ranked: list[tuple[int, dict[str, Any]]] = []
    for index, result in enumerate(results):
        item_id = int(result.get("item_id") or 0)
        enriched = dict(result)
        enriched["dimension_match_score"] = scores.get(item_id, 0)
        enriched["dimension_match_total"] = len(requested)
        ranked.append((index, enriched))
    ranked.sort(key=lambda item: (int(item[1].get("dimension_match_score") or 0), -item[0]), reverse=True)
    return [item[1] for item in ranked]


def _ensure_mysql_knowledge_fulltext_index(conn: Any) -> None:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS index_count FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_chunk' "
            "AND INDEX_NAME = 'idx_knowledge_chunk_content_ft'"
        ).fetchone()
        if row is None or int(row["index_count"] or 0) == 0:
            conn.execute(
                "CREATE FULLTEXT INDEX idx_knowledge_chunk_content_ft "
                "ON knowledge_chunk (content) WITH PARSER ngram"
            )
            conn.commit()
            print("[KnowledgeSearch] MySQL ngram fulltext index is ready", flush=True)
    except Exception as exc:
        # Vector retrieval remains available if this MySQL build lacks the
        # ngram parser or the account cannot add an index.
        print(f"[KnowledgeSearch] MySQL fulltext index unavailable: {str(exc)[:300]}", flush=True)

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS index_count FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_chunk' "
            "AND INDEX_NAME = 'idx_knowledge_chunk_content_section_ft'"
        ).fetchone()
        if row is not None and int(row["index_count"] or 0) > 0:
            return
        conn.execute(
            "CREATE FULLTEXT INDEX idx_knowledge_chunk_content_section_ft "
            "ON knowledge_chunk (content, section_title) WITH PARSER ngram"
        )
        conn.commit()
        print("[KnowledgeSearch] MySQL ngram content+heading index is ready", flush=True)
    except Exception as exc:
        # Keep content-only retrieval usable when adding the wider index is
        # blocked by a MySQL build or database account permission.
        print(f"[KnowledgeSearch] MySQL content+heading index unavailable: {str(exc)[:300]}", flush=True)


def _retrieval_terms(query: str, limit: int = 24) -> list[str]:
    stop_words = {
        "以及", "或者", "相关", "内容", "要求", "方案", "项目", "文件", "提供", "进行",
        "按照", "根据", "其中", "包括", "用于", "可以", "需要", "资料", "情况", "工作",
    }
    terms = [token for token in _tokens(query) if len(token) >= 2 and token not in stop_words]
    terms = list(dict.fromkeys(terms))
    terms.sort(key=lambda token: (len(token), token), reverse=True)
    return terms[: max(1, min(limit, 32))]


def _mysql_keyword_candidates(query: str, where: str, params: list[Any], limit: int) -> list[tuple[int, float]] | None:
    if (settings.db_backend or "sqlite").strip().lower() != "mysql":
        return None
    search_text = re.sub(r"\s+", " ", query or "").strip()[:1000]
    if not search_text:
        return []
    terms = _retrieval_terms(query, 16)
    title_conditions = " OR ".join("k.section_title LIKE ?" for _ in terms) or "0"
    heading_score = " + ".join("CASE WHEN k.section_title LIKE ? THEN 0.15 ELSE 0 END" for _ in terms) or "0"
    heading_like = [f"%{term}%" for term in terms]
    try:
        with storage.connect() as conn:
            # The compound ngram index covers both正文 and heading path; if an
            # older MySQL build cannot create it, the content-only index is a
            # compatible fallback.
            try:
                rows = conn.execute(
                    f"""
                    SELECT k.chunk_id,
                           MATCH(k.content, k.section_title) AGAINST (? IN NATURAL LANGUAGE MODE) AS lexical_score,
                           ({heading_score}) AS heading_score
                    FROM knowledge_chunk k
                    JOIN knowledge_item i ON i.item_id = k.item_id
                    LEFT JOIN knowledge_category c ON c.category_id = i.category_id
                    WHERE {where}
                      AND (MATCH(k.content, k.section_title) AGAINST (? IN NATURAL LANGUAGE MODE) > 0
                           OR ({title_conditions}))
                    ORDER BY lexical_score DESC, heading_score DESC, k.chunk_id DESC
                    LIMIT ?
                    """,
                    (search_text, *heading_like, *params, search_text, *heading_like, max(1, min(limit, 1000))),
                ).fetchall()
            except Exception:
                rows = conn.execute(
                    f"""
                    SELECT k.chunk_id,
                           MATCH(k.content) AGAINST (? IN NATURAL LANGUAGE MODE) AS lexical_score,
                           ({heading_score}) AS heading_score
                    FROM knowledge_chunk k
                    JOIN knowledge_item i ON i.item_id = k.item_id
                    LEFT JOIN knowledge_category c ON c.category_id = i.category_id
                    WHERE {where}
                      AND (MATCH(k.content) AGAINST (? IN NATURAL LANGUAGE MODE) > 0
                           OR ({title_conditions}))
                    ORDER BY lexical_score DESC, heading_score DESC, k.chunk_id DESC
                    LIMIT ?
                    """,
                    (search_text, *heading_like, *params, search_text, *heading_like, max(1, min(limit, 1000))),
                ).fetchall()
        return [
            (int(row["chunk_id"]), float(row["lexical_score"] or 0.0) + float(row["heading_score"] or 0.0))
            for row in rows
        ]
    except Exception as exc:
        print(f"[KnowledgeSearch] MySQL fulltext search unavailable: {str(exc)[:300]}", flush=True)
        return None


def _sql_keyword_candidates(query: str, where: str, params: list[Any], limit: int) -> list[tuple[int, float]]:
    terms = _retrieval_terms(query, 20)
    if not terms:
        return []
    conditions = " OR ".join("(k.content LIKE ? OR k.section_title LIKE ?)" for _ in terms)
    score_terms = " + ".join(
        "CASE WHEN k.section_title LIKE ? THEN 2.0 ELSE 0 END + CASE WHEN k.content LIKE ? THEN 1.0 ELSE 0 END"
        for _ in terms
    )
    likes = [f"%{term}%" for term in terms]
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT k.chunk_id, ({score_terms}) AS lexical_score
            FROM knowledge_chunk k
            JOIN knowledge_item i ON i.item_id = k.item_id
            LEFT JOIN knowledge_category c ON c.category_id = i.category_id
            WHERE {where} AND ({conditions})
            ORDER BY lexical_score DESC, k.chunk_id DESC
            LIMIT ?
            """,
            (*[value for term in likes for value in (term, term)], *params,
             *[value for term in likes for value in (term, term)], max(1, min(limit, 1000))),
        ).fetchall()
    return [(int(row["chunk_id"]), float(row["lexical_score"] or 0.0)) for row in rows]


def search(payload: KnowledgeSearchRequest) -> dict[str, Any]:
    ensure_schema()
    payload = payload.model_copy(update={"bid_type": _normalize_dimension_value(payload.bid_type, "bid_type") or None, "bid_scenario": _normalize_dimension_value(payload.bid_scenario, "bid_scenario") or None, "length_level": _normalize_dimension_value(payload.length_level, "length_level") or None})
    _refresh_expired_flags()
    clauses = ["i.status = 'completed'"]
    params: list[Any] = []
    if payload.category_id:
        clauses.append("i.category_id = ?")
        params.append(payload.category_id)
    if payload.project_id:
        clauses.append("k.project_id = ?")
        params.append(payload.project_id)
    if payload.item_id:
        clauses.append("k.item_id = ?")
        params.append(payload.item_id)
    if payload.doc_role:
        clauses.append("k.doc_role = ?")
        params.append(payload.doc_role)
    if payload.chunk_kind:
        chunk_kinds = list(dict.fromkeys(value.strip() for value in payload.chunk_kind if value.strip()))
        if chunk_kinds:
            placeholders = ",".join("?" for _ in chunk_kinds)
            clauses.append(f"k.chunk_kind IN ({placeholders})")
            params.extend(chunk_kinds)
    if payload.image_category:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset a WHERE a.item_id = i.item_id AND a.category = ?)")
        params.append(payload.image_category)
    if payload.asset_sub_type:
        subtype_values = [item.strip() for item in payload.asset_sub_type if item and item.strip()]
        if subtype_values:
            subtype_placeholders = ",".join("?" for _ in subtype_values)
            clauses.append(f"EXISTS (SELECT 1 FROM knowledge_asset a WHERE a.item_id = i.item_id AND COALESCE(a.asset_sub_type, '') IN ({subtype_placeholders}))")
            params.extend(subtype_values)
    if payload.bid_type:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset a WHERE a.item_id = i.item_id AND COALESCE(a.bid_type, '') = ?)")
        params.append(payload.bid_type)
    if payload.bid_scenario:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset a WHERE a.item_id = i.item_id AND COALESCE(a.bid_scenario, '') = ?)")
        params.append(payload.bid_scenario)
    if payload.length_level:
        clauses.append("EXISTS (SELECT 1 FROM knowledge_asset a WHERE a.item_id = i.item_id AND COALESCE(a.length_level, '') = ?)")
        params.append(payload.length_level)
    if not payload.include_expired:
        clauses.append("i.is_expired = 0")
    where = " AND ".join(clauses)
    candidate_limit = max(
        payload.limit,
        min(1000, payload.limit * max(1, int(settings.rag_search_overfetch or 10))),
    )
    keyword_rank = _mysql_keyword_candidates(payload.query, where, params, candidate_limit)
    keyword_backend = "mysql_ngram"
    if keyword_rank is None:
        keyword_rank = _sql_keyword_candidates(payload.query, where, params, candidate_limit)
        keyword_backend = "sql_like"

    vector_filters: dict[str, Any] = {}
    for key in ("category_id", "project_id", "item_id", "doc_role"):
        value = getattr(payload, key)
        if value is not None and value != "":
            vector_filters[key] = value
    if payload.chunk_kind:
        vector_filters["chunk_kind"] = list(dict.fromkeys(payload.chunk_kind))
    vector_rank: list[tuple[int, float]] = []
    if vector_store.enabled():
        vector_hits = vector_store.search_knowledge_chunks(
            payload.query,
            limit=candidate_limit,
            filters=vector_filters,
        )
        vector_rank = [
            (int(hit.get("chunk_id") or 0), float(hit.get("score") or 0.0))
            for hit in vector_hits
            if int(hit.get("chunk_id") or 0) > 0
        ]

    ranked = _rrf_merge(keyword_rank, vector_rank, candidate_limit)
    candidate_ids = list(dict.fromkeys(chunk_id for chunk_id, _ in ranked))
    results: list[dict[str, Any]] = []
    if candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        with storage.connect() as conn:
            candidate_rows = conn.execute(
                f"""
                SELECT k.*, i.title, i.source_name, i.category_id, i.tags_json, i.valid_until, i.is_expired,
                       c.name AS category_name
                FROM knowledge_chunk k
                JOIN knowledge_item i ON i.item_id = k.item_id
                LEFT JOIN knowledge_category c ON c.category_id = i.category_id
                WHERE {where} AND k.chunk_id IN ({placeholders})
                """,
                (*params, *candidate_ids),
            ).fetchall()
        row_map = {int(row["chunk_id"]): row for row in candidate_rows}
        fused_scores = dict(ranked)
        for chunk_id in candidate_ids:
            row = row_map.get(chunk_id)
            if row is None:
                continue
            heading_score = _keyword_score(
                _retrieval_terms(payload.chapter_theme or "", 16),
                _tokens(str(row["section_path"] or row["section_title"] or "")),
                str(row["section_path"] or row["section_title"] or ""),
            ) if payload.chapter_theme else 0.0
            similarity = min(1.0, fused_scores.get(chunk_id, 0.0) + heading_score * 0.12)
            results.append(
                {
                    "chunk_id": chunk_id,
                    "item_id": int(row["item_id"]),
                    "project_id": int(row["project_id"] or 0) or None,
                    "doc_role": row["doc_role"] or "knowledge",
                    "chunk_kind": row["chunk_kind"] or "document_text",
                    "source_asset_id": row["source_asset_id"],
                    "title": row["title"],
                    "category_id": row["category_id"],
                    "category_name": row["category_name"],
                    "content": row["content"],
                    "section_title": row["section_title"],
                    "section_path": row["section_path"] or row["section_title"],
                    "page_number": row["page_number"],
                    "similarity": round(similarity, 4),
                    "heading_match_score": round(heading_score, 4),
                    "source_document": row["source_name"],
                    "tags": _loads(row["tags_json"], []),
                    "valid_until": row["valid_until"],
                    "assets": [],
                }
            )
        results.sort(key=lambda item: (item["similarity"], item["chunk_id"]), reverse=True)
        results = results[: payload.limit]
        if payload.include_images:
            for result in results:
                asset_ids: list[int] | None = None
                if result.get("chunk_kind") == "ocr_text":
                    asset_ids = [int(result["source_asset_id"])] if result.get("source_asset_id") else []
                elif result.get("chunk_kind") == "image_annotation":
                    match = re.search(r"图片语义注释#(\d+)$", str(result.get("section_title") or ""))
                    asset_ids = [int(match.group(1))] if match else []
                result["assets"] = _assets_for_item(
                    int(result["item_id"]),
                    image_category=payload.image_category,
                    bid_type=payload.bid_type,
                    bid_scenario=payload.bid_scenario,
                    length_level=payload.length_level,
                    asset_sub_type=payload.asset_sub_type,
                    priority_expire=payload.priority_expire,
                    exclude_ids=set(payload.exclude_ids),
                    chapter_theme=payload.chapter_theme,
                    asset_ids=asset_ids,
                )

    if results:
        vector_status = vector_store.status()
        strategy = f"{keyword_backend}+{vector_status['vector_backend']}_rrf" if vector_rank else keyword_backend
        return {
            "query": payload.query,
            "total": len(results),
            "results": results,
            "strategy": f"{strategy}+chapter_boost+mysql_metadata_filter",
            "chromadb_enabled": _chromadb_available(),
            "vector_backend": vector_status["vector_backend"],
            "vector_store_enabled": vector_status["vector_store_enabled"],
            "embedding_provider": vector_status["embedding_provider"],
            "retrieval": {
                "keyword_candidates": len(keyword_rank),
                "vector_candidates": len(vector_rank),
                "fusion": "reciprocal_rank_fusion" if vector_rank else "keyword_only",
                "metadata_filters": {key: value for key, value in {
                    "project_id": payload.project_id,
                    "item_id": payload.item_id,
                    "doc_role": payload.doc_role,
                    "category_id": payload.category_id,
                }.items() if value is not None},
            },
        }
    if not results and (payload.bid_type or payload.bid_scenario or payload.length_level):
        fallback_payload = payload.model_copy(update={"bid_type": None, "bid_scenario": None, "length_level": None})
        fallback = search(fallback_payload)
        fallback["results"] = _rank_dimension_fallback(fallback.get("results", []), payload)
        fallback["dimension_fallback"] = {
            "requested": {key: value for key, value in {"bid_type": payload.bid_type, "bid_scenario": payload.bid_scenario, "length_level": payload.length_level}.items() if value},
            "matched_results": sum(1 for result in fallback.get("results", []) if int(result.get("dimension_match_score") or 0) > 0),
        }
        fallback["strategy"] = f"{fallback.get('strategy', 'fallback')}+dimension_fallback"
        return fallback
    vector_status = vector_store.status()
    return {
        "query": payload.query,
        "total": 0,
        "results": [],
        "strategy": f"{keyword_backend}+qdrant_rrf" if vector_rank else keyword_backend,
        "chromadb_enabled": _chromadb_available(),
        "vector_backend": vector_status["vector_backend"],
        "vector_store_enabled": vector_status["vector_store_enabled"],
        "embedding_provider": vector_status["embedding_provider"],
        "retrieval": {"keyword_candidates": len(keyword_rank), "vector_candidates": len(vector_rank), "fusion": "reciprocal_rank_fusion" if vector_rank else "keyword_only"},
    }


def graph(category_id: int | None = None) -> dict[str, Any]:
    ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if category_id:
        clauses.append("e.category_id = ?")
        params.append(category_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with storage.connect() as conn:
        entity_rows = conn.execute(
            f"""
            SELECT e.*, c.name AS category_name
            FROM knowledge_entity e
            LEFT JOIN knowledge_category c ON c.category_id = e.category_id
            {where}
            ORDER BY e.entity_id DESC
            LIMIT 300
            """,
            params,
        ).fetchall()
        ids = [int(row["entity_id"]) for row in entity_rows]
        relation_rows = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            relation_rows = conn.execute(
                f"SELECT * FROM knowledge_relation WHERE source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}) LIMIT 500",
                (*ids, *ids),
            ).fetchall()
    nodes = [
        {
            "id": str(row["entity_id"]),
            "name": row["name"],
            "type": row["entity_type"],
            "category_id": row["category_id"],
            "category_name": row["category_name"],
            "item_id": row["item_id"],
        }
        for row in entity_rows
    ]
    links = [
        {
            "id": str(row["relation_id"]),
            "source": str(row["source_entity_id"]),
            "target": str(row["target_entity_id"]),
            "label": row["relation_type"],
            "item_id": row["item_id"],
        }
        for row in relation_rows
    ]
    category_nodes: dict[int, dict[str, Any]] = {}
    for node in nodes:
        cid = node.get("category_id")
        cname = node.get("category_name")
        if not cid or not cname:
            continue
        category_nodes[int(cid)] = {"id": f"cat:{cid}", "name": str(cname), "type": "知识分类", "category_id": cid, "category_name": cname, "item_id": None}
        links.append({"id": f"cat-rel-{cid}-{node['id']}", "source": f"cat:{cid}", "target": node["id"], "label": "包含", "item_id": node.get("item_id")})
    return {"nodes": [*category_nodes.values(), *nodes], "links": links}


def batch(payload: KnowledgeBatchRequest) -> dict[str, Any]:
    ensure_schema()
    affected = 0
    if payload.action == "delete":
        for item_id in payload.item_ids:
            try:
                delete_item(item_id)
                affected += 1
            except KnowledgeModuleError:
                continue
        return {"affected": affected}
    with storage.connect() as conn:
        if payload.action == "move":
            if not payload.category_id:
                raise KnowledgeModuleError(40031, "批量移动需要目标分类")
            cursor = conn.executemany("UPDATE knowledge_item SET category_id = ?, update_time = ? WHERE item_id = ?", [(payload.category_id, _now(), item_id) for item_id in payload.item_ids])
            affected = cursor.rowcount or len(payload.item_ids)
        elif payload.action == "expire":
            cursor = conn.executemany("UPDATE knowledge_item SET is_expired = 1, update_time = ? WHERE item_id = ?", [(_now(), item_id) for item_id in payload.item_ids])
            affected = cursor.rowcount or len(payload.item_ids)
        elif payload.action == "restore":
            cursor = conn.executemany("UPDATE knowledge_item SET is_expired = 0, status = CASE WHEN status = 'archived' THEN 'completed' ELSE status END, update_time = ? WHERE item_id = ?", [(_now(), item_id) for item_id in payload.item_ids])
            affected = cursor.rowcount or len(payload.item_ids)
        conn.commit()
    return {"affected": affected}


def diagnostics() -> dict[str, Any]:
    ensure_schema()
    paddle_available = _local_ocr_available()
    vector_status = vector_store.status()
    with storage.connect() as conn:
        task_rows = conn.execute(
            "SELECT status, COUNT(1) AS count FROM knowledge_rag_task GROUP BY status"
        ).fetchall()
        item_rows = conn.execute(
            "SELECT vector_status AS status, COUNT(1) AS count FROM knowledge_item GROUP BY vector_status"
        ).fetchall()
        fulltext_rows = []
        if (settings.db_backend or "sqlite").strip().lower() == "mysql":
            try:
                fulltext_rows = conn.execute(
                    "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_chunk' "
                    "AND INDEX_NAME IN ('idx_knowledge_chunk_content_ft', 'idx_knowledge_chunk_content_section_ft')"
                ).fetchall()
            except Exception as exc:
                print(f"[KnowledgeSearch] fulltext index diagnostics unavailable: {str(exc)[:300]}", flush=True)
    task_counts = {str(row["status"]): int(row["count"] or 0) for row in task_rows}
    item_counts = {str(row["status"] or "unknown"): int(row["count"] or 0) for row in item_rows}
    fulltext_indexes = sorted({str(row["INDEX_NAME"]) for row in fulltext_rows})
    return {
        "database_backend": (settings.db_backend or "sqlite").strip().lower(),
        "keyword_retrieval_backend": "mysql_ngram_fulltext_with_like_fallback" if (settings.db_backend or "sqlite").strip().lower() == "mysql" else "sql_like",
        "mysql_ngram_fulltext_available": bool(fulltext_indexes),
        "mysql_ngram_fulltext_indexes": fulltext_indexes,
        "local_ocr_available": paddle_available,
        "local_ocr_engine": "rapidocr-onnx",
        "paddleocr_available": False,
        "chromadb_available": _chromadb_available(),
        "local_vector_fallback": True,
        "vector_backend": vector_status["vector_backend"],
        "vector_store_enabled": vector_status["vector_store_enabled"],
        "vector_store_available": vector_status["vector_store_available"],
        "vector_store_error": vector_status["vector_store_error"],
        "embedding_provider": vector_status["embedding_provider"],
        "embedding_model": vector_status["embedding_model"],
        "embedding_dimension": vector_status["embedding_dimension"],
        "milvus_uri": vector_status["milvus_uri"],
        "milvus_collection_knowledge": vector_status["milvus_collection_knowledge"],
        "qdrant_url": vector_status.get("qdrant_url", ""),
        "qdrant_collection_knowledge": vector_status.get("qdrant_collection_knowledge", ""),
        "qdrant_collection_rag_cases": vector_status.get("qdrant_collection_rag_cases", ""),
        "rag_index_tasks": task_counts,
        "rag_index_items": item_counts,
        "ocr_status": "available" if paddle_available else "missing",
        "ocr_install_command": OCR_INSTALL_COMMAND,
        "ocr_install_note": "安装后重启后端服务，扫描版 PDF 和图片将自动启用逐页 OCR。",
    }


def rebuild_vector_index() -> dict[str, Any]:
    ensure_schema()
    vector_status = vector_store.status()
    if not vector_store.enabled() or not vector_status.get("vector_store_available"):
        return {
            **vector_status,
            "knowledge_items_indexed": 0,
            "knowledge_items_failed": 0,
            "knowledge_items_skipped": 0,
            "knowledge_chunks_attempted": 0,
            "knowledge_chunks_indexed": 0,
            "knowledge_chunks_failed": 0,
            "rag_case_chunks_attempted": 0,
            "rag_case_chunks_indexed": 0,
            "rag_case_chunks_failed": 0,
            "complete": False,
            "message": "External vector store is not enabled or unavailable; local fallback remains active.",
        }

    with storage.connect() as conn:
        item_rows = conn.execute("SELECT item_id FROM knowledge_item WHERE status = 'completed' ORDER BY item_id").fetchall()

    knowledge_items_indexed = 0
    knowledge_items_failed = 0
    knowledge_items_skipped = 0
    knowledge_chunks_attempted = 0
    knowledge_chunks_indexed = 0
    for row in item_rows:
        item_id = int(row["item_id"])
        result = _run_vector_sync_task(item_id)
        knowledge_chunks_attempted += int(result["attempted"])
        knowledge_chunks_indexed += int(result["indexed"])
        if int(result["attempted"]) == 0:
            knowledge_items_skipped += 1
        elif result["complete"]:
            knowledge_items_indexed += 1
        else:
            knowledge_items_failed += 1

    rag_case_chunks_attempted = 0
    rag_case_chunks_indexed = 0
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT ch.*, c.case_name
            FROM rag_case_chunks ch
            JOIN rag_cases c ON c.case_id = ch.case_id
            WHERE c.ingest_status = 'completed'
            ORDER BY ch.chunk_id
            """
        ).fetchall()
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "case_id": int(row["case_id"]),
                "document_id": int(row["document_id"]),
                "doc_role": row["doc_role"] or "",
                "chunk_kind": row["chunk_kind"] or "",
                "chunk_title": row["chunk_title"] or "",
                "case_name": row["case_name"] or "",
                "content": row["content"] or "",
            }
        )
        if len(batch) >= 200:
            result = vector_store.upsert_rag_case_chunks_with_stats(batch)
            rag_case_chunks_attempted += int(result["attempted"])
            rag_case_chunks_indexed += int(result["indexed"])
            batch = []
    if batch:
        result = vector_store.upsert_rag_case_chunks_with_stats(batch)
        rag_case_chunks_attempted += int(result["attempted"])
        rag_case_chunks_indexed += int(result["indexed"])

    knowledge_chunks_failed = knowledge_chunks_attempted - knowledge_chunks_indexed
    rag_case_chunks_failed = rag_case_chunks_attempted - rag_case_chunks_indexed
    complete = knowledge_chunks_failed == 0 and rag_case_chunks_failed == 0

    return {
        **vector_store.status(),
        "knowledge_items_indexed": knowledge_items_indexed,
        "knowledge_items_failed": knowledge_items_failed,
        "knowledge_items_skipped": knowledge_items_skipped,
        "knowledge_chunks_attempted": knowledge_chunks_attempted,
        "knowledge_chunks_indexed": knowledge_chunks_indexed,
        "knowledge_chunks_failed": knowledge_chunks_failed,
        "rag_case_chunks_attempted": rag_case_chunks_attempted,
        "rag_case_chunks_indexed": rag_case_chunks_indexed,
        "rag_case_chunks_failed": rag_case_chunks_failed,
        "complete": complete,
        "message": (
            f"{vector_status['vector_backend']} vector index rebuild finished."
            if complete
            else f"{vector_status['vector_backend']} vector index rebuild finished with write failures."
        ),
    }


def _row_category(row: Any) -> dict[str, Any]:
    return {
        "category_id": int(row["category_id"]),
        "id": int(row["category_id"]),
        "name": row["name"],
        "parent_id": row["parent_id"],
        "sort_order": int(row["sort_order"] or 0),
        "item_count": int(row["item_count"] or 0) if "item_count" in row.keys() else 0,
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


def _item_live_asset_progress(row: Any) -> dict[str, int]:
    # The cached extracted_image_count is only finalized at the end of deep
    # recognition. Count existing rows so an in-flight job no longer shows 0
    # images after materialization. Failed attempts stay separate from successes.
    count = int(_row_value(row, "asset_count", _row_value(row, "extracted_image_count", 0)) or 0)
    completed = int(_row_value(row, "deep_completed_count", 0) or 0)
    failed = int(_row_value(row, "deep_failed_count", 0) or 0)
    return {
        "asset_count": count,
        "extracted_image_count": count,
        "deep_completed_count": completed,
        "deep_failed_count": failed,
        "deep_processed_count": completed + failed,
        **{name: int(_row_value(row, name, 0) or 0) for name in ("ocr_recognized_count", "ocr_no_text_count", "ocr_unprocessed_count")},
    }


def _row_item(row: Any, *, detail: bool) -> dict[str, Any]:
    content = row["content_text"] or ""
    result = {
        "item_id": int(row["item_id"]),
        "id": int(row["item_id"]),
        "category_id": row["category_id"],
        "category_name": row["category_name"] if "category_name" in row.keys() else None,
        "title": row["title"],
        "file_type": row["file_type"],
        "source_name": row["source_name"],
        "original_file_path": row["original_file_path"],
        "source_local_path": row["source_local_path"] if "source_local_path" in row.keys() else None,
        "file_size": int(row["file_size"] or 0) if "file_size" in row.keys() else 0,
        "status": row["status"],
        "process_progress": int(row["process_progress"] or 0),
        "process_error": row["process_error"],
        "vector_status": row["vector_status"] if "vector_status" in row.keys() else "unknown",
        "vector_indexed_chunks": int(row["vector_indexed_chunks"] or 0) if "vector_indexed_chunks" in row.keys() else 0,
        "vector_error": row["vector_error"] if "vector_error" in row.keys() else None,
        "vector_updated_at": row["vector_updated_at"] if "vector_updated_at" in row.keys() else None,
        "version_no": int(row["version_no"] or 1),
        "valid_until": row["valid_until"],
        "is_expired": bool(row["is_expired"]),
        "evidence_kind": _row_value(row, "evidence_kind", None),
        "evidence_review": _loads(_row_value(row, "evidence_review_json", None), None),
        "tags": _loads(row["tags_json"], []),
        "content_preview": content[:420],
        "asset_count": int(row["asset_count"] or 0) if "asset_count" in row.keys() else 0,
        "chunk_count": int(row["chunk_count"] or 0) if "chunk_count" in row.keys() else 0,
        "total_pages": int(row["total_pages"] or 0) if "total_pages" in row.keys() else 0,
        "current_page": int(row["current_page"] or 0) if "current_page" in row.keys() else 0,
        "text_page_count": int(row["text_page_count"] or 0) if "text_page_count" in row.keys() else 0,
        "scan_page_count": int(row["scan_page_count"] or 0) if "scan_page_count" in row.keys() else 0,
        "deep_status": row["deep_status"] if "deep_status" in row.keys() else "not_started",
        "deep_progress": int(row["deep_progress"] or 0) if "deep_progress" in row.keys() else 0,
        "deep_total": int(row["deep_total"] or 0) if "deep_total" in row.keys() else 0,
        "deep_error": row["deep_error"] if "deep_error" in row.keys() else None,
        "extracted_image_count": int(row["extracted_image_count"] or 0) if "extracted_image_count" in row.keys() else 0,
        "text_char_count": int(row["text_char_count"] or 0) if "text_char_count" in row.keys() else len(content),
        "image_category_stats": _loads(row["image_category_stats_json"] if "image_category_stats_json" in row.keys() else None, {}),
        "thumbnail_path": row["thumbnail_path"] if "thumbnail_path" in row.keys() else None,
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }
    result.update(_item_live_asset_progress(row))
    if detail:
        result["content_text"] = content
    return result


def _row_asset(row: Any) -> dict[str, Any]:
    return {
        **_evidence_asset_fields(row),
        "asset_id": int(row["asset_id"]),
        "item_id": int(row["item_id"]),
        "asset_type": row["asset_type"],
        "resource_path": row["resource_path"],
        "category": row["category"] if "category" in row.keys() else None,
        "tags": _loads(row["tags_json"], []),
        "bid_type": row["bid_type"] if "bid_type" in row.keys() else None,
        "bid_scenario": row["bid_scenario"] if "bid_scenario" in row.keys() else None,
        "length_level": row["length_level"] if "length_level" in row.keys() else None,
        "dimension_tags": _loads(row["dimension_tags"] if "dimension_tags" in row.keys() else None, {}),
        "extend_tags": _loads(row["extend_tags"] if "extend_tags" in row.keys() else None, []),
        "ai_description": row["ai_description"],
        "annotation": row["annotation"] if "annotation" in row.keys() else None,
        "ocr_text": row["ocr_text"] if "ocr_text" in row.keys() else None,
        "employee_info": _loads(row["employee_info"] if "employee_info" in row.keys() else None, None),
        "employee_role": row["employee_role"] if "employee_role" in row.keys() else None,
        "contract_info": _loads(row["contract_info"] if "contract_info" in row.keys() else None, None),
        "archive_date": row["archive_date"] if "archive_date" in row.keys() else None,
        "storage_location": row["storage_location"],
        "page_num": row["page_num"] if "page_num" in row.keys() else None,
        "source_chapter": row["source_chapter"] if "source_chapter" in row.keys() else None,
        "image_hash": row["image_hash"] if "image_hash" in row.keys() else None,
        "duplicate_of_asset_id": row["duplicate_of_asset_id"] if "duplicate_of_asset_id" in row.keys() else None,
        "image_width": row["image_width"] if "image_width" in row.keys() else None,
        "image_height": row["image_height"] if "image_height" in row.keys() else None,
        "person_info": _loads(row["person_info"] if "person_info" in row.keys() else None, None),
        "structured_info": _loads(row["structured_info"] if "structured_info" in row.keys() else None, {}),
        "asset_sub_type": row["asset_sub_type"] if "asset_sub_type" in row.keys() else None,
        "expire_date": row["expire_date"] if "expire_date" in row.keys() else None,
        "cert_no": row["cert_no"] if "cert_no" in row.keys() else None,
        "holder_name": row["holder_name"] if "holder_name" in row.keys() else None,
        "project_id": row["project_id"] if "project_id" in row.keys() else None,
        "ocr_status": _row_value(row, "ocr_status", "not_started"),
        "ocr_error": _row_value(row, "ocr_error", None),
        "ocr_confidence": _row_value(row, "ocr_confidence", None),
        "deep_candidate": bool(row["deep_candidate"] or 0) if "deep_candidate" in row.keys() else False,
        "deep_status": row["deep_status"] if "deep_status" in row.keys() else "not_started",
        "create_time": row["create_time"],
    }


def _row_version(row: Any) -> dict[str, Any]:
    return {"version_id": int(row["version_id"]), "item_id": int(row["item_id"]), "title": row["title"], "version_no": int(row["version_no"]), "original_file_path": row["original_file_path"], "create_time": row["create_time"]}


def _row_chunk(row: Any) -> dict[str, Any]:
    return {
        "chunk_id": int(row["chunk_id"]),
        "section_title": row["section_title"],
        "section_path": row["section_path"] if "section_path" in row.keys() else row["section_title"],
        "project_id": row["project_id"] if "project_id" in row.keys() else None,
        "doc_role": row["doc_role"] if "doc_role" in row.keys() else "knowledge",
        "chunk_kind": row["chunk_kind"] if "chunk_kind" in row.keys() else "document_text",
        "page_number": row["page_number"],
        "source_asset_id": _row_value(row, "source_asset_id", None),
        "content": row["content"],
    }


def _scan_supported_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("~$") or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            if path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        files.append(path.resolve())
    return sorted(files, key=lambda item: str(item.relative_to(root)).lower())


def _project_name_from_file(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return _normalize_project_name(path.stem)
    if len(relative.parts) > 1:
        return relative.parts[0]
    return _normalize_project_name(path.stem)


def _normalize_project_name(stem: str) -> str:
    name = re.sub(r"[（(].*?[）)]", "", stem)
    name = re.split(r"[（(]", name, maxsplit=1)[0]
    org_match = re.match(r"^(.{2,40}?)(?:运营控股|集团|有限责任公司|有限公司)", name)
    if org_match:
        name = org_match.group(1)
    replacements = [
        "投标(响应)文件其他证明文件",
        "投标（响应）文件其他证明文件",
        "投标(响应)文件内容",
        "投标（响应）文件内容",
        "投标响应文件",
        "响应文件",
        "投标文件",
        "招标文件正文",
        "招标文件",
        "采购文件",
        "询价文件",
        "磋商文件",
        "制作文件",
        "正文",
    ]
    for word in replacements:
        name = name.replace(word, "")
    keyword_match = re.match(r"^(.{2,40}?)(?:招标|采购|询价|需求|磋商|投标|响应|报价)", name)
    if keyword_match:
        name = keyword_match.group(1)
    org_match = re.match(r"^(.{2,40}?)(?:运营控股|集团|有限责任公司|有限公司)", name)
    if org_match:
        name = org_match.group(1)
    area_match = re.match(r"^(.{2,18}?(?:街道|园区|集团|矿业|通航|国有资本))", name)
    if area_match:
        name = area_match.group(1)
    name = re.sub(r"[-_—\s]+", "", name).strip("，。,.、-_")
    return name or stem[:40]


def _find_duplicate_local_item(path: Path) -> Any | None:
    resolved = str(path.resolve())
    size = int(path.stat().st_size)
    with storage.connect() as conn:
        return conn.execute(
            "SELECT item_id, status FROM knowledge_item WHERE source_local_path = ? AND file_size = ? ORDER BY item_id DESC LIMIT 1",
            (resolved, size),
        ).fetchone()


def _is_duplicate_local_file(path: Path) -> bool:
    return _find_duplicate_local_item(path) is not None


def _create_item_from_local_file(path: Path, project_name: str) -> int:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise KnowledgeModuleError(40041, "文件格式暂不支持")
    source_path = path.resolve()
    stat = source_path.stat()
    item_dir = _knowledge_root() / "batch_import" / uuid.uuid4().hex
    item_dir.mkdir(parents=True, exist_ok=True)
    target = item_dir / source_path.name
    shutil.copy2(source_path, target)

    title = source_path.stem.strip() or source_path.name
    category_id = _guess_import_category_id(source_path.name, suffix)
    category_name = _category_name(category_id)
    tags = _import_tags_for_file(source_path, project_name, category_name)
    version_no = _next_version(title)
    now = _now()
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO knowledge_item (
                category_id, title, file_type, source_name, original_file_path, source_local_path, file_size,
                content_text, status, process_progress, version_no, valid_until,
                is_expired, tags_json, create_time, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'pending', 5, ?, NULL, 0, ?, ?, ?)
            """,
            (
                category_id,
                title,
                suffix.lstrip("."),
                source_path.name,
                _stored_path(target),
                str(source_path),
                int(stat.st_size),
                version_no,
                _json(tags),
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO knowledge_item_version (item_id, title, version_no, original_file_path, content_text, create_time) VALUES (?, ?, ?, ?, '', ?)",
            (item_id, title, version_no, _stored_path(target), now),
        )
        conn.commit()
    return item_id


def _guess_import_category_id(filename: str, suffix: str) -> int | None:
    categories = list_categories()
    name_to_id = {item["name"]: item["category_id"] for item in categories}
    text = filename.lower()
    if suffix.lower() in IMAGE_EXTENSIONS:
        if any(word in text for word in ["证书", "资质", "许可", "执照", "身份证", "健康证", "cert", "license"]):
            return name_to_id.get("资质证书") or name_to_id.get("人员资质")
        if any(word in text for word in ["设备", "车辆", "冷库", "仪器", "车", "truck", "device"]):
            return name_to_id.get("设备资产") or name_to_id.get("现场图片")
        return name_to_id.get("现场图片")
    if any(word in filename for word in ["投标", "标书", "方案", "响应", "报价", "制作文件"]):
        return name_to_id.get("方案案例")
    if any(word in filename for word in ["招标", "采购", "询价", "需求", "磋商"]):
        return name_to_id.get("招标文件")
    if suffix.lower() in {".pdf", ".doc", ".docx"}:
        return name_to_id.get("方案案例")
    return _guess_category_id(filename, "")


def _category_name(category_id: int | None) -> str:
    if not category_id:
        return "未分类"
    with storage.connect() as conn:
        row = conn.execute("SELECT name FROM knowledge_category WHERE category_id = ?", (category_id,)).fetchone()
    return row["name"] if row else "未分类"


def _import_tags_for_file(path: Path, project_name: str, category_name: str) -> list[str]:
    tags = ["批量导入"]
    if project_name:
        tags.append(project_name)
    if category_name and category_name != "未分类":
        tags.append(category_name)
    filename = path.name
    if any(word in filename for word in ["招标", "采购", "询价", "需求", "磋商"]):
        tags.append("招标资料")
    if any(word in filename for word in ["投标", "标书", "方案", "响应", "报价", "制作文件"]):
        tags.append("成品投标文件")
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        tags.extend(_classify_image(filename))
    tags.append(path.suffix.lower().lstrip(".").upper())
    return list(dict.fromkeys(tag for tag in tags if tag))[:30]


def _mark_import_current(task_id: str, import_file_id: int, file_name: str) -> None:
    now = _now()
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_import_file SET status = 'processing', update_time = ? WHERE import_file_id = ?", (now, import_file_id))
        conn.execute("UPDATE knowledge_import_task SET status = 'processing', current_file = ?, update_time = ? WHERE task_id = ?", (file_name, now, task_id))
        conn.commit()


def _mark_import_file(
    task_id: str,
    import_file_id: int,
    status: str,
    error_msg: str | None,
    *,
    success: bool = False,
    failed: bool = False,
    skipped: bool = False,
    item_id: int | None = None,
) -> None:
    del success, failed, skipped
    now = _now()
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_import_file
            SET status = ?, error_msg = ?, item_id = COALESCE(?, item_id), update_time = ?
            WHERE import_file_id = ?
            """,
            (status, error_msg, item_id, now, import_file_id),
        )
        conn.commit()
    _sync_import_task_counts(task_id, clear_current=True)


def _sync_import_task_counts(task_id: str, *, clear_current: bool = False) -> dict[str, int]:
    with storage.connect() as conn:
        rows = conn.execute("SELECT status, COUNT(1) AS count FROM knowledge_import_file WHERE task_id = ? GROUP BY status", (task_id,)).fetchall()
        counts = {row["status"]: int(row["count"]) for row in rows}
        success_count = counts.get("completed", 0)
        failed_count = counts.get("failed", 0)
        skipped_count = counts.get("skipped", 0)
        processed_files = success_count + failed_count + skipped_count
        total_files = sum(counts.values())
        failure_rows = conn.execute(
            "SELECT file_path, file_name, error_msg FROM knowledge_import_file WHERE task_id = ? AND status = 'failed' ORDER BY import_file_id",
            (task_id,),
        ).fetchall()
        failures = [
            {"file_path": row["file_path"], "file_name": row["file_name"], "reason": row["error_msg"] or "处理失败"}
            for row in failure_rows
        ]
        current_sql = ", current_file = NULL" if clear_current else ""
        conn.execute(
            f"""
            UPDATE knowledge_import_task
            SET total_files = ?, processed_files = ?, success_count = ?, failed_count = ?, skipped_count = ?,
                failures_json = ?, update_time = ?{current_sql}
            WHERE task_id = ?
            """,
            (total_files, processed_files, success_count, failed_count, skipped_count, _json(failures), _now(), task_id),
        )
        conn.commit()
    return {"total_files": total_files, "processed_files": processed_files, "success_count": success_count, "failed_count": failed_count, "skipped_count": skipped_count}


def _finish_import_task(task_id: str) -> None:
    counts = _sync_import_task_counts(task_id, clear_current=True)
    status = "completed" if counts["failed_count"] == 0 else "completed_with_errors"
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_import_task SET status = ?, current_file = NULL, update_time = ? WHERE task_id = ?", (status, _now(), task_id))
        conn.commit()


def _row_import_task(task: Any, files: list[Any]) -> dict[str, Any]:
    failures = _loads(task["failures_json"], [])
    if not failures:
        failures = [
            {"file_path": row["file_path"], "file_name": row["file_name"], "reason": row["error_msg"] or "处理失败"}
            for row in files
            if row["status"] == "failed"
        ]
    return {
        "task_id": task["task_id"],
        "dir_path": task["dir_path"],
        "status": task["status"],
        "total_files": int(task["total_files"] or 0),
        "processed_files": int(task["processed_files"] or 0),
        "success_count": int(task["success_count"] or 0),
        "failed_count": int(task["failed_count"] or 0),
        "skipped_count": int(task["skipped_count"] or 0),
        "current_file": task["current_file"],
        "failures": failures,
        "files": [
            {
                "import_file_id": int(row["import_file_id"]),
                "file_path": row["file_path"],
                "file_name": row["file_name"],
                "file_size": int(row["file_size"] or 0),
                "project_name": row["project_name"],
                "item_id": row["item_id"],
                "status": row["status"],
                "error_msg": row["error_msg"],
                "create_time": row["create_time"],
                "update_time": row["update_time"],
            }
            for row in files
        ],
        "create_time": task["create_time"],
        "update_time": task["update_time"],
    }


def _parse_tags(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        parts = value
    else:
        try:
            parsed = json.loads(value) if value.strip().startswith("[") else None
            parts = parsed if isinstance(parsed, list) else re.split(r"[,，;；\s]+", value)
        except Exception:
            parts = re.split(r"[,，;；\s]+", value)
    return list(dict.fromkeys(str(part).strip() for part in parts if str(part).strip()))[:30]


def _next_version(title: str) -> int:
    ensure_schema()
    with storage.connect() as conn:
        row = conn.execute("SELECT MAX(version_no) AS version_no FROM knowledge_item WHERE title = ?", (title,)).fetchone()
    return int(row["version_no"] or 0) + 1


def _guess_category_id(filename: str, text: str) -> int | None:
    categories = list_categories()
    name_to_id = {item["name"]: item["category_id"] for item in categories}
    haystack = f"{filename} {text[:1000]}"
    rules = [
        ("资质证书", ["证书", "资质", "许可证", "营业执照", "认证", "检测报告"]),
        ("现场图片", ["图片", "照片", "车辆", "冷库", "现场", "设备", ".jpg", ".png", ".jpeg"]),
        ("人员资质", ["人员", "身份证", "健康证", "厨师", "项目经理"]),
        ("设备资产", ["设备", "车辆", "冷链", "冷库", "检测仪"]),
        ("制度规范", ["制度", "规范", "流程", "管理办法", "应急预案"]),
        ("方案案例", ["方案", "案例", "项目", "服务", "实施"]),
    ]
    for category, keywords in rules:
        if any(keyword in haystack for keyword in keywords):
            return name_to_id.get(category)
    return categories[0]["category_id"] if categories else None


def _set_item_status(
    item_id: int,
    status: str,
    progress: int,
    error: str | None,
    *,
    total_pages: int | None = None,
    current_page: int | None = None,
    extracted_image_count: int | None = None,
    text_char_count: int | None = None,
    image_category_stats: dict[str, int] | None = None,
    text_page_count: int | None = None,
    scan_page_count: int | None = None,
    deep_status: str | None = None,
    deep_progress: int | None = None,
    deep_total: int | None = None,
    deep_error: str | None = None,
) -> None:
    assignments = ["status = ?", "process_progress = ?", "process_error = ?", "update_time = ?"]
    params: list[Any] = [status, progress, error, _now()]
    if status in {"completed", "failed"}:
        assignments.append("processing_lock = NULL")
    if total_pages is not None:
        assignments.append("total_pages = ?")
        params.append(max(0, int(total_pages)))
    if current_page is not None:
        assignments.append("current_page = ?")
        params.append(max(0, int(current_page)))
    if extracted_image_count is not None:
        assignments.append("extracted_image_count = ?")
        params.append(max(0, int(extracted_image_count)))
    if text_char_count is not None:
        assignments.append("text_char_count = ?")
        params.append(max(0, int(text_char_count)))
    if image_category_stats is not None:
        assignments.append("image_category_stats_json = ?")
        params.append(_json(image_category_stats))
    if text_page_count is not None:
        assignments.append("text_page_count = ?")
        params.append(max(0, int(text_page_count)))
    if scan_page_count is not None:
        assignments.append("scan_page_count = ?")
        params.append(max(0, int(scan_page_count)))
    if deep_status is not None:
        assignments.append("deep_status = ?")
        params.append(deep_status)
    if deep_progress is not None:
        assignments.append("deep_progress = ?")
        params.append(max(0, min(100, int(deep_progress))))
    if deep_total is not None:
        assignments.append("deep_total = ?")
        params.append(max(0, int(deep_total)))
    if deep_error is not None:
        assignments.append("deep_error = ?")
        params.append(deep_error)
    params.append(item_id)
    with storage.connect() as conn:
        conn.execute(f"UPDATE knowledge_item SET {', '.join(assignments)} WHERE item_id = ?", params)
        conn.commit()


def _set_item_processing_lock(item_id: int, lock_id: str) -> None:
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_item SET processing_lock = ?, status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END, update_time = ? WHERE item_id = ?", (lock_id, _now(), item_id))
        conn.commit()


def _clear_item_processing_lock(item_id: int, lock_id: str) -> None:
    try:
        with storage.connect() as conn:
            conn.execute("UPDATE knowledge_item SET processing_lock = NULL, update_time = ? WHERE item_id = ? AND processing_lock = ?", (_now(), item_id, lock_id))
            conn.commit()
    except Exception:
        pass


def _failure_message(exc: Exception) -> str:
    return exc.message if isinstance(exc, KnowledgeModuleError) else str(exc)


def _is_database_locked_error(exc: Exception | str) -> bool:
    message = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in message or "database is locked" in message or "database table is locked" in message or "database busy" in message


def _safe_set_item_failed(item_id: int, message: str) -> None:
    for attempt in range(3):
        try:
            _set_item_status(item_id, "failed", 100, message)
            return
        except Exception as exc:
            if not _is_database_locked_error(exc) or attempt == 2:
                print(f"[KnowledgeProcess] failed_status_update_error item_id={item_id} error={exc}", flush=True)
                return


def _extract_document(path: Path, source_name: str, file_type: str, item_id: int, *, evidence: bool = False) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path, item_id, evidence=evidence)
    if suffix == ".docx":
        return _extract_docx(path, item_id)
    if suffix == ".doc":
        text = _extract_plain_or_doc(path)
        return {"text": text, "pages": [], "total_pages": 0, "processed_pages": 0, "ocr_pages": 0, "text_page_count": 0, "scan_page_count": 0, "deep_candidate_pages": []}
    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {"text": text, "pages": [], "total_pages": 0, "processed_pages": 0, "ocr_pages": 0, "text_page_count": 0, "scan_page_count": 0, "deep_candidate_pages": []}
    if suffix in IMAGE_EXTENSIONS:
        _save_image_asset(
            item_id,
            path,
            source_name,
            context_text="",
            profile={**_image_profile(path), "decorative": False} if evidence else None,
            page_number=1 if evidence else None,
            duplicate_candidates=[] if evidence else None,
            defer_deep=True,
            deep_candidate=True,
        )
        return {"text": "", "pages": [], "total_pages": 1, "processed_pages": 1, "ocr_pages": 0, "text_page_count": 0, "scan_page_count": 1, "deep_candidate_pages": [1]}
    return {"text": "", "pages": [], "total_pages": 0, "processed_pages": 0, "ocr_pages": 0, "text_page_count": 0, "scan_page_count": 0, "deep_candidate_pages": []}


def _extract_pdf(path: Path, item_id: int, *, evidence: bool = False) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    texts: list[str] = []
    # The foreground pass intentionally does not initialize or call PaddleOCR.
    # Low-text pages are recorded for the background deep-extraction pass.
    ocr_pages = 0
    scan_pages: set[int] = set()
    deep_candidate_pages: set[int] = set()
    text_page_count = 0
    with pdfplumber.open(str(path)) as pdf:
        total_pages = len(pdf.pages)
        if evidence:
            deep_candidate_pages.update(range(1, total_pages + 1))
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if len(re.sub(r"\s+", "", text)) < OCR_SCAN_THRESHOLD:
                scan_pages.add(index)
                deep_candidate_pages.add(index)
            else:
                text_page_count += 1
            text = text.strip()
            if index == 1 or index == total_pages or index % 10 == 0:
                _set_item_status(item_id, "processing", min(45, 16 + int(index / max(1, total_pages) * 28)), None, total_pages=total_pages, current_page=index)
            if text:
                pages.append({"page_number": index, "text": text})
                texts.append(f"【第{index}页】\n{text}")
    page_contexts = {int(page["page_number"]): str(page["text"]) for page in pages}
    image_count = 0 if evidence else _extract_pdf_images(
        path,
        item_id,
        page_contexts,
        scan_pages=scan_pages,
        deep_candidate_pages=deep_candidate_pages,
        defer_deep=True,
    )
    print(f"[KnowledgeExtract] pdf_done file={path.name} total_pages={total_pages} text_pages={text_page_count} scan_pages={len(scan_pages)} ocr_pages={ocr_pages} deep_candidates={len(deep_candidate_pages)} images={image_count}", flush=True)
    return {
        "text": "\n\n".join(texts), "pages": pages, "total_pages": total_pages,
        "processed_pages": total_pages, "ocr_pages": ocr_pages,
        "text_page_count": text_page_count, "scan_page_count": len(scan_pages),
        "deep_candidate_pages": sorted(deep_candidate_pages),
    }


def _is_deep_asset_candidate(source_name: str, context_text: str, image_ocr: str) -> bool:
    text = " ".join((source_name or "", context_text or "", image_ocr or "")).lower()
    compact = re.sub(r"\s+", "", text)
    hits = sum(1 for keyword in DEEP_ASSET_KEYWORDS if keyword.lower() in compact)
    has_identity_number = bool(re.search(r"[1-9]\d{5}(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9x]", compact, re.I))
    return has_identity_number or hits >= 1


def _ocr_image_bytes(image_bytes: bytes, extension: str = "png") -> str:
    if not image_bytes or not _local_ocr_available():
        return ""
    suffix = "." + str(extension or "png").lstrip(".")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=str(_knowledge_root())) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(image_bytes)
    try:
        return _ocr_image(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _extract_docx(path: Path, item_id: int) -> dict[str, Any]:
    # DOCX is a ZIP package. python-docx eagerly builds package parts, which
    # also reads every embedded image. Read only document.xml and styles.xml
    # here so text-first imports do not decompress large media before they can
    # create searchable chunks.
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            document_xml = archive.read("word/document.xml")
            styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in names else None
            deferred_image_count = sum(name.startswith("word/media/") for name in names)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise KnowledgeModuleError(40054, f"无法读取 Word 文档结构：{exc}", 400) from exc
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise KnowledgeModuleError(40055, f"Word 正文 XML 无法解析：{exc}", 400) from exc
    body = root.find(".//w:body", DOCX_XML_NS)
    if body is None:
        raise KnowledgeModuleError(40056, "Word 文档中未找到正文内容", 400)
    style_levels = _docx_style_outline_levels(styles_xml)
    sections: list[dict[str, str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_title = "文档内容"
    current_lines: list[str] = []
    paragraph_count = 0
    def flush_section() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append({"section_title": current_title, "content": content})
        current_lines.clear()

    # Walk the document body in XML order so headings, paragraphs, and tables
    # stay together. Separate `doc.paragraphs`/`doc.tables` passes lose that
    # order and make requirement-to-response matching much less useful.
    for block in _iter_docx_blocks(body):
        if block.tag == f"{{{DOCX_WORD_NS}}}p":
            text = _docx_paragraph_text(block).strip()
            if not text:
                continue
            paragraph_count += 1
            level = _docx_heading_level_from_xml(block, text, style_levels)
            if level:
                flush_section()
                heading_stack = [(depth, title) for depth, title in heading_stack if depth < level]
                heading_stack.append((level, text))
                current_title = " > ".join(title for _, title in heading_stack)[:240]
            current_lines.append(text)
        elif block.tag == f"{{{DOCX_WORD_NS}}}tbl":
            table_lines = _extract_docx_table_lines(block)
            if table_lines:
                current_lines.extend(table_lines)
    flush_section()
    text = "\n\n".join(section["content"] for section in sections)
    # Keep the foreground DOCX pass limited to XML text, headings and tables.
    # Image extraction and OCR run in the deep background task so large
    # image-heavy Word files become searchable without waiting for media I/O.
    total_chars = len(text)
    print(f"[KnowledgeExtract] docx_done file={path.name} paragraphs={paragraph_count} sections={len(sections)} chars={total_chars} deferred_images={deferred_image_count}", flush=True)
    return {
        "text": text,
        "sections": sections,
        "pages": [],
        "total_pages": 0,
        "processed_pages": 0,
        "ocr_pages": 0,
        "deferred_image_count": deferred_image_count,
    }


def _docx_style_outline_levels(styles_xml: bytes | None) -> dict[str, int]:
    if not styles_xml:
        return {}
    try:
        root = ET.fromstring(styles_xml)
    except ET.ParseError:
        return {}
    word_tag = lambda name: f"{{{DOCX_WORD_NS}}}{name}"
    records: dict[str, dict[str, Any]] = {}
    for style in root.findall(".//w:style", DOCX_XML_NS):
        style_id = style.get(word_tag("styleId"))
        if not style_id:
            continue
        name = style.find("./w:name", DOCX_XML_NS)
        based_on = style.find("./w:basedOn", DOCX_XML_NS)
        outline = style.find("./w:pPr/w:outlineLvl", DOCX_XML_NS)
        records[style_id] = {
            "name": str(name.get(word_tag("val")) or "") if name is not None else "",
            "based_on": str(based_on.get(word_tag("val")) or "") if based_on is not None else "",
            "outline": _docx_outline_level(outline),
        }

    def resolve(style_id: str, visited: set[str]) -> int | None:
        if style_id in visited:
            return None
        visited.add(style_id)
        record = records.get(style_id, {})
        direct = record.get("outline")
        if direct is not None:
            return int(direct)
        named = re.search(r"(?:heading|标题)\s*([1-6])", f"{style_id} {record.get('name', '')}", re.IGNORECASE)
        if named:
            return int(named.group(1))
        parent = str(record.get("based_on") or "")
        return resolve(parent, visited) if parent else None

    levels: dict[str, int] = {}
    for style_id in records:
        level = resolve(style_id, set())
        if level is not None and 1 <= level <= 6:
            levels[style_id] = level
    return levels


def _docx_outline_level(element: Any) -> int | None:
    if element is None:
        return None
    try:
        outline = int(element.get(f"{{{DOCX_WORD_NS}}}val"))
    except (TypeError, ValueError):
        return None
    # Word's values 0-5 map to heading levels 1-6; 9 means body text.
    return outline + 1 if 0 <= outline <= 5 else None


def _iter_docx_blocks(container: Any):
    for child in list(container):
        if child.tag in {f"{{{DOCX_WORD_NS}}}p", f"{{{DOCX_WORD_NS}}}tbl"}:
            yield child
        elif child.tag in DOCX_BLOCK_CONTAINERS:
            yield from _iter_docx_blocks(child)


def _docx_paragraph_text(paragraph: Any) -> str:
    text_parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{DOCX_WORD_NS}}}t":
            text_parts.append(node.text or "")
        elif node.tag == f"{{{DOCX_WORD_NS}}}tab":
            text_parts.append("\t")
        elif node.tag in {f"{{{DOCX_WORD_NS}}}br", f"{{{DOCX_WORD_NS}}}cr"}:
            text_parts.append("\n")
    return "".join(text_parts)


def _docx_heading_level_from_xml(paragraph: Any, text: str, style_levels: dict[str, int]) -> int | None:
    word_tag = lambda name: f"{{{DOCX_WORD_NS}}}{name}"
    style = paragraph.find("./w:pPr/w:pStyle", DOCX_XML_NS)
    style_id = str(style.get(word_tag("val")) or "") if style is not None else ""
    if style_id in style_levels:
        return style_levels[style_id]
    style_match = re.search(r"(?:heading|标题)\s*([1-6])", style_id, re.IGNORECASE)
    if style_match:
        return int(style_match.group(1))
    paragraph_properties = paragraph.find("./w:pPr", DOCX_XML_NS)
    outline = _docx_outline_level(
        paragraph_properties.find("./w:outlineLvl", DOCX_XML_NS)
        if paragraph_properties is not None else None
    )
    if outline:
        return outline
    return _docx_heading_level_from_text(text)


def _docx_heading_level_from_text(text: str) -> int | None:
    numbered = re.match(r"^(\d+(?:\.\d+){0,5})(?:[、.．\s]+)　*\S", text)
    if numbered:
        return min(6, numbered.group(1).count(".") + 1)
    if re.match(r"^第[一二三四五六七八九十百零〇\d]+[章节部分篇]\s*\S", text):
        return 1
    if re.match(r"^[一二三四五六七八九十百零〇]+[、.．]\s*\S", text):
        return 2
    return None


def _extract_docx_table_lines(table: Any) -> list[str]:
    lines: list[str] = []
    for row in table.findall("./w:tr", DOCX_XML_NS):
        cells: list[str] = []
        nested_lines: list[str] = []
        for cell in row.findall("./w:tc", DOCX_XML_NS):
            cell_paragraphs: list[str] = []
            for block in _iter_docx_blocks(cell):
                if block.tag == f"{{{DOCX_WORD_NS}}}p":
                    value = _docx_paragraph_text(block).strip()
                    if value:
                        cell_paragraphs.append(value)
                else:
                    nested_lines.extend(_extract_docx_table_lines(block))
            if cell_paragraphs:
                cells.append(" / ".join(cell_paragraphs))
        if cells:
            lines.append(" | ".join(cells))
        lines.extend(nested_lines)
    return lines


def _extract_plain_or_doc(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_pdf_images(
    path: Path,
    item_id: int,
    page_contexts: dict[int, str],
    *,
    scan_pages: set[int] | None = None,
    deep_candidate_pages: set[int] | None = None,
    defer_deep: bool = False,
) -> int:
    if fitz is None:
        return 0
    saved = 0
    try:
        doc = fitz.open(str(path))
        asset_dir = path.parent / "assets"
        asset_dir.mkdir(exist_ok=True)
        candidates: list[dict[str, Any]] = []
        embedded_pages: set[int] = set()
        repeated_zone_profiles: list[dict[str, Any]] = []
        for page_index in range(len(doc)):
            page_number = page_index + 1
            page = doc[page_index]
            context = page_contexts.get(page_number, "")
            chapter = _source_chapter_from_text(context)
            page_height = float(page.rect.height or 1)
            for image_index, image in enumerate(page.get_images(full=True)):
                embedded_pages.add(page_number)
                xref = image[0]
                base = doc.extract_image(xref)
                ext = base.get("ext", "png")
                image_bytes = base.get("image") or b""
                profile = _image_profile_from_bytes(image_bytes)
                zone = _image_page_zone(page.get_image_rects(xref), page_height)
                if profile.get("hash") and zone in {"top", "bottom"}:
                    similar = _find_repeated_zone_profile(profile, zone, repeated_zone_profiles)
                    if similar:
                        similar["pages"].add(page_number)
                    else:
                        repeated_zone_profiles.append({"hash": profile["hash"], "zone": zone, "width": profile.get("width"), "height": profile.get("height"), "pages": {page_number}})
                candidates.append({"page_number": page_number, "image_index": image_index + 1, "ext": ext, "bytes": image_bytes, "profile": profile, "zone": zone, "context": context, "chapter": chapter})
        repeated_decorations = [item for item in repeated_zone_profiles if len(item.get("pages") or set()) >= 3]
        for candidate in candidates:
            profile = candidate["profile"]
            if profile.get("decorative") or _is_repeated_zone_decoration(profile, candidate["zone"], repeated_decorations):
                continue
            target = asset_dir / f"page{candidate['page_number']}_image{candidate['image_index']}.{candidate['ext']}"
            target.write_bytes(candidate["bytes"])
            if _save_image_asset(
                item_id, target, target.name, page_number=candidate["page_number"],
                source_chapter=candidate["chapter"], context_text=candidate["context"],
                profile=profile, defer_deep=True, deep_candidate=True,
            ):
                saved += 1

        # Scanned pages are intentionally materialized by the background deep
        # task. Rendering them here would make the initial upload slow again.
    except Exception as exc:
        print(f"[KnowledgeExtract] pdf_images_error file={path.name} error={exc}", flush=True)
        return saved
    return saved


def _extract_docx_images(path: Path, item_id: int, context_text: str = "") -> int:
    saved = 0
    seen_image_hashes: set[str] = set()
    try:
        asset_dir = path.parent / "assets"
        asset_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            media = [name for name in zf.namelist() if name.startswith("word/media/")]
            for index, name in enumerate(media, start=1):
                image_bytes = zf.read(name)
                if not image_bytes:
                    continue
                image_hash = hashlib.sha256(image_bytes).hexdigest()
                if image_hash in seen_image_hashes:
                    continue
                seen_image_hashes.add(image_hash)
                suffix = Path(name).suffix or ".png"
                target = asset_dir / f"docx_image{index}{suffix}"
                target.write_bytes(image_bytes)
                if _save_image_asset(
                    item_id,
                    target,
                    target.name,
                    source_chapter=_source_chapter_from_text(context_text),
                    context_text=context_text,
                    defer_deep=True,
                    deep_candidate=True,
                ):
                    saved += 1
    except Exception:
        return saved
    return saved


def _materialize_docx_images(item_id: int) -> int:
    """Materialize DOCX media in the background before deep OCR.

    A DOCX is a ZIP package. Reading its XML and reading its media are kept as
    separate phases so a large image-heavy tender becomes searchable quickly.
    Existing hashes are checked before inserting, making retries idempotent.
    """
    with storage.connect() as conn:
        item = conn.execute(
            "SELECT original_file_path, source_name, content_text FROM knowledge_item WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        existing_paths = [row["resource_path"] for row in conn.execute(
            "SELECT resource_path FROM knowledge_asset WHERE item_id = ?", (item_id,)
        ).fetchall()]
        duplicate_candidates = [
            dict(row)
            for row in conn.execute(
                "SELECT asset_id, resource_path, image_hash FROM knowledge_asset "
                "WHERE image_hash IS NOT NULL AND COALESCE(duplicate_of_asset_id, 0) = 0 "
                "ORDER BY asset_id DESC LIMIT 3000"
            ).fetchall()
        ]
    if not item or not str(item["original_file_path"] or "").lower().endswith(".docx"):
        return 0
    source_path = _resolve_stored_path(item["original_file_path"])
    if not source_path or not source_path.exists():
        return 0
    asset_dir = source_path.parent / "assets"
    asset_dir.mkdir(exist_ok=True)
    document_text = str(item["content_text"] or "")
    project_id = _project_id_for_item(item_id)
    media_contexts = _docx_media_contexts(source_path)
    saved = 0
    # image_hash is a perceptual fingerprint, not SHA256. Compare actual bytes.
    seen_hashes = set()
    for stored in existing_paths:
        existing = _resolve_stored_path(stored)
        if existing and existing.is_file():
            digest = hashlib.sha256()
            with existing.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            seen_hashes.add(digest.hexdigest())
    try:
        with zipfile.ZipFile(source_path) as archive:
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
            for index, name in enumerate(media, start=1):
                try:
                    image_bytes = archive.read(name)
                except (KeyError, OSError, RuntimeError):
                    continue
                if not image_bytes:
                    continue
                image_hash = hashlib.sha256(image_bytes).hexdigest()
                if image_hash in seen_hashes:
                    continue
                seen_hashes.add(image_hash)
                suffix = Path(name).suffix or ".png"
                target = asset_dir / f"docx_image{index}{suffix}"
                target.write_bytes(image_bytes)
                media_context = media_contexts.get(name, {})
                source_chapter = str(media_context.get("source_chapter") or "") or None
                context_text = str(media_context.get("context_text") or document_text[:1200])
                if _save_image_asset(
                    item_id,
                    target,
                    target.name,
                    source_chapter=source_chapter,
                    context_text=context_text,
                    profile=_image_profile_from_bytes(image_bytes),
                    project_id=project_id,
                    duplicate_candidates=duplicate_candidates,
                    defer_deep=True,
                    deep_candidate=True,
                ):
                    saved += 1
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"[KnowledgeDeepExtract] docx_media_error item_id={item_id} error={exc}", flush=True)
    return saved


def _docx_media_contexts(path: Path) -> dict[str, dict[str, str]]:
    """Resolve image relationship parts to their nearby chapter/text context."""
    try:
        with zipfile.ZipFile(path) as archive:
            document_root = ET.fromstring(archive.read("word/document.xml"))
            styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in archive.namelist() else None
            relationships_xml = archive.read("word/_rels/document.xml.rels")
        relationships_root = ET.fromstring(relationships_xml)
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"[KnowledgeDeepExtract] docx_relationship_error file={path.name} error={exc}", flush=True)
        return {}
    body = document_root.find(".//w:body", DOCX_XML_NS)
    if body is None:
        return {}
    style_levels = _docx_style_outline_levels(styles_xml)
    relationships_namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
    relationship_targets: dict[str, str] = {}
    for relationship in relationships_root.findall(f"{{{relationships_namespace}}}Relationship"):
        if not str(relationship.get("Type") or "").endswith("/image"):
            continue
        if relationship.get("TargetMode") == "External":
            continue
        rel_id = str(relationship.get("Id") or "")
        target = str(relationship.get("Target") or "")
        if not rel_id or not target:
            continue
        media_name = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("word", target))
        relationship_targets[rel_id] = media_name

    contexts: dict[str, dict[str, str]] = {}
    heading_stack: list[tuple[int, str]] = []
    current_title = "文档内容"
    embed_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

    def record_images(element: Any, text: str) -> None:
        rel_ids = {
            str(node.get(embed_attr) or "")
            for node in element.iter()
            if node.tag.endswith("}blip") and node.get(embed_attr)
        }
        for rel_id in rel_ids:
            media_name = relationship_targets.get(rel_id)
            if not media_name:
                continue
            nearby = f"章节：{current_title}\n{text}".strip()[:1400]
            prior = contexts.get(media_name)
            if prior is None:
                contexts[media_name] = {"source_chapter": current_title, "context_text": nearby}
            elif len(prior["context_text"]) < 2400 and nearby not in prior["context_text"]:
                prior["context_text"] = f"{prior['context_text']}\n{nearby}"[:2400]

    def visit_table(table: Any) -> None:
        for row in table.findall("./w:tr", DOCX_XML_NS):
            cell_texts = [
                " ".join(value for value in (_docx_paragraph_text(block).strip() for block in _iter_docx_blocks(cell) if block.tag == f"{{{DOCX_WORD_NS}}}p") if value)
                for cell in row.findall("./w:tc", DOCX_XML_NS)
            ]
            row_text = " | ".join(value for value in cell_texts if value)
            for cell in row.findall("./w:tc", DOCX_XML_NS):
                for block in _iter_docx_blocks(cell):
                    if block.tag == f"{{{DOCX_WORD_NS}}}p":
                        paragraph_text = _docx_paragraph_text(block).strip()
                        record_images(block, row_text or paragraph_text)
                    else:
                        visit_table(block)

    try:
        for block in _iter_docx_blocks(body):
            if block.tag == f"{{{DOCX_WORD_NS}}}p":
                text = _docx_paragraph_text(block).strip()
                if text:
                    level = _docx_heading_level_from_xml(block, text, style_levels)
                    if level:
                        heading_stack = [(depth, title) for depth, title in heading_stack if depth < level]
                        heading_stack.append((level, text))
                        current_title = " > ".join(title for _, title in heading_stack)[:240]
                record_images(block, text)
            elif block.tag == f"{{{DOCX_WORD_NS}}}tbl":
                visit_table(block)
    except Exception as exc:
        print(f"[KnowledgeDeepExtract] docx_context_error file={path.name} error={exc}", flush=True)
    return contexts


def _project_id_for_item(item_id: int) -> int | None:
    project_id, _ = _document_rag_metadata(item_id)
    return project_id


def _document_rag_metadata(item_id: int) -> tuple[int | None, str]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT project_id,
                   CASE WHEN tender_doc_id = ? THEN 'tender' ELSE 'bid' END AS doc_role
            FROM bid_project
            WHERE tender_doc_id = ? OR bid_doc_id = ?
            ORDER BY project_id DESC LIMIT 1
            """,
            (item_id, item_id, item_id),
        ).fetchone()
    if row is None:
        return None, "knowledge"
    return int(row["project_id"]), str(row["doc_role"] or "knowledge")


def _save_image_asset(
    item_id: int,
    path: Path,
    source_name: str,
    page_number: int | None = None,
    source_chapter: str | None = None,
    context_text: str = "",
    profile: dict[str, Any] | None = None,
    image_ocr: str | None = None,
    project_id: int | None = None,
    duplicate_candidates: list[dict[str, Any]] | None = None,
    defer_deep: bool = False,
    deep_candidate: bool = False,
) -> bool:
    profile = profile or _image_profile(path)
    if profile.get("decorative"):
        _safe_unlink_asset_file(path)
        return False
    category, tags, confidence = _classify_image(source_name, context_text, profile)
    if image_ocr is None and not defer_deep:
        image_ocr = _ocr_image(path) if _local_ocr_available() else ""
    else:
        image_ocr = image_ocr or ""
    desc = _image_description(source_name, category, tags, page_number, source_chapter, confidence)
    annotation = _image_annotation(source_name, category, tags, page_number, source_chapter, context_text, image_ocr, desc)
    person_info = {} if defer_deep else _extract_person_info(category, tags, context_text, image_ocr, source_name)
    employee_info, employee_role = ({}, "") if defer_deep else _employee_info_from_person(category, tags, context_text, image_ocr, source_name, person_info)
    contract_info = {} if defer_deep else _extract_contract_info(category, tags, context_text, image_ocr, source_name)
    archive_date = _archive_date_for_asset(contract_info, image_ocr, context_text)
    structured = {"structured_info": {}, "asset_sub_type": ASSET_SUBTYPE_OTHER, "expire_date": "", "cert_no": "", "holder_name": "", "dimension_tags": {}} if defer_deep else _extract_structured_asset_info(
        source_name,
        context_text,
        image_ocr,
        profile,
        category,
        tags,
        person_info=person_info,
        employee_info=employee_info,
        contract_info=contract_info,
    )
    annotation = _build_semantic_annotation(
        {
            "source_chapter": source_chapter,
            "asset_sub_type": structured.get("asset_sub_type"),
            "category": category,
            "tags": tags,
            "ocr_text": image_ocr,
        },
        document_text=context_text,
        base_annotation=annotation,
    )
    duplicate = (
        _find_similar_profile(profile.get("hash"), duplicate_candidates)
        if duplicate_candidates is not None
        else _find_duplicate_asset(profile.get("hash"))
    )
    # Perceptual similarity is not proof of identical document contents.
    # Never replace scans/certificates with a visually similar page from another file.
    if duplicate:
        other = _resolve_stored_path(duplicate["resource_path"])
        if not other or not other.exists() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(other.read_bytes()).digest():
            duplicate = None
    resource_path = duplicate["resource_path"] if duplicate else _stored_path(path)
    duplicate_of_asset_id = duplicate["asset_id"] if duplicate else None
    if duplicate:
        _safe_unlink_asset_file(path, keep_path=_resolve_stored_path(resource_path))
    with storage.connect() as conn:
        value_placeholders = ", ".join(["?", "'图片'", *(["?"] * 26)])
        cursor = conn.execute(
            f"""
            INSERT INTO knowledge_asset (
                item_id, asset_type, resource_path, category, tags_json, ai_description, annotation,
                storage_location, page_num, source_chapter, image_hash, duplicate_of_asset_id,
                image_width, image_height, person_info, ocr_text, employee_info, employee_role, contract_info, archive_date,
                structured_info, asset_sub_type, expire_date, cert_no, holder_name, dimension_tags, project_id, create_time,
                deep_candidate, deep_status
            )
            VALUES ({value_placeholders}, ?, ?)
            """,
            (
                item_id,
                resource_path,
                category,
                _json(tags),
                desc,
                annotation,
                path.parent.name,
                page_number,
                source_chapter,
                profile.get("hash"),
                duplicate_of_asset_id,
                profile.get("width"),
                profile.get("height"),
                _json(person_info) if person_info else None,
                image_ocr,
                _json(employee_info) if employee_info else None,
                employee_role,
                _json(contract_info) if contract_info else None,
                archive_date,
                _json(structured["structured_info"]),
                structured["asset_sub_type"],
                structured["expire_date"],
                structured["cert_no"],
                structured["holder_name"],
                _json(structured.get("dimension_tags") or {}),
                project_id if project_id is not None else _project_id_for_item(item_id),
                _now(),
                1 if deep_candidate else 0,
                "pending" if deep_candidate else "completed",
            ),
        )
        conn.commit()
    if duplicate_candidates is not None and not duplicate:
        duplicate_candidates.append({
            "asset_id": int(cursor.lastrowid),
            "resource_path": resource_path,
            "image_hash": profile.get("hash"),
        })
    return True


def _ocr_pdf_page(path: Path, page_index: int, *, dpi: int = PDF_OCR_DPI) -> str:
    if fitz is None or not _local_ocr_available():
        return ""
    try:
        doc = fitz.open(str(path))
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=str(path.parent)) as temp_file:
            image_path = Path(temp_file.name)
        try:
            pix.save(str(image_path))
            return _ocr_image(image_path)
        finally:
            image_path.unlink(missing_ok=True)
    except Exception:
        return ""


def _ocr_image(path: Path) -> str:
    from .local_ocr import recognize_cached
    return recognize_cached(path, settings.database_file.parent / "ocr-cache").text


def _get_ocr_engine() -> Any | None:
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    try:
        from paddleocr import PaddleOCR  # type: ignore

        try:
            # PaddleOCR 3.x on Windows CPU currently hits a oneDNN PIR
            # runtime error unless MKL-DNN is disabled explicitly.
            _OCR_ENGINE = PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )
        except TypeError:
            try:
                _OCR_ENGINE = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            except TypeError:
                _OCR_ENGINE = PaddleOCR(use_angle_cls=True, lang="ch")
        return _OCR_ENGINE
    except Exception as exc:
        print(f"[KnowledgeOCR] engine_init_failed error={exc}", flush=True)
        return None


def _local_ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime
        import onnxruntime
        return True
    except Exception:
        return False


def _chromadb_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("chromadb") is not None
    except Exception:
        return False


def _classify_image(name: str, context_text: str = "", profile: dict[str, Any] | None = None) -> tuple[str, list[str], float]:
    haystack = f"{name} {context_text[:1200]}".lower()
    scored: list[tuple[str, int, list[str]]] = []
    for category, keywords in IMAGE_CLASSIFY_RULES:
        matched = [keyword for keyword in keywords if keyword.lower() in haystack]
        if matched:
            scored.append((category, len(matched), matched))
    if not scored:
        return "其他附件类", ["附件", "图片资料"], 0.35
    category, score, matched = sorted(scored, key=lambda item: item[1], reverse=True)[0]
    feature_tags: list[str] = []
    if profile:
        if (profile.get("width") or 0) >= 900 and (profile.get("height") or 0) >= 500:
            feature_tags.append("高清原图")
        if (profile.get("edge_density") or 0) > 0.18:
            feature_tags.append("文字/表格密集")
    tags = list(dict.fromkeys([category.replace("类", ""), *matched, *feature_tags]))[:8]
    confidence = min(0.95, 0.52 + score * 0.13)
    if confidence < 0.70:
        fallback_tags = list(dict.fromkeys(["其他附件", *matched, *feature_tags, "图片资料"]))[:8]
        return "其他附件类", fallback_tags, confidence
    return category, tags, confidence


def _classify_structured_asset(
    name: str,
    context_text: str,
    image_ocr: str,
    profile: dict[str, Any] | None,
    legacy_category: str,
) -> tuple[str, str, float, dict[str, list[str]]]:
    """Classify a material subtype with OCR, context and image-profile evidence."""
    ocr = (image_ocr or "").lower()
    context = (context_text or "").lower()
    source_name = (name or "").lower()

    # Personnel certificates need a stricter, document-specific decision than
    # the generic keyword rules. Three core features plus a layout/name hint
    # are required so ordinary personnel photos do not become certificates.
    personnel_text = "\n".join([ocr, context, source_name])
    compact_personnel_text = re.sub(r"\s+", "", personnel_text)

    # OCR frequently drops one or more labels on identity and health cards.
    # An 18-digit identity number plus one identity label is stronger evidence
    # than a filename or a generic personnel keyword, so handle these signals
    # before the generic subtype rules.
    identity_number = re.search(r"[1-9]\d{5}(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9x]", compact_personnel_text, re.I)
    identity_terms = [
        "居民身份证",
        "公民身份号码",
        "身份证号码",
        "签发机关",
        "有效期限",
        "民族",
        "住址",
    ]
    identity_hits = [term for term in identity_terms if term in compact_personnel_text]
    if identity_number and identity_hits or len(identity_hits) >= 4:
        return ASSET_CATEGORY_PERSONNEL, ASSET_SUBTYPE_IDENTITY, 0.96 if identity_number else 0.9, {
            "ocr_keywords": [term for term in identity_hits if term in ocr][:10],
            "context_keywords": [term for term in identity_hits if term in context][:10],
            "image_features": ["身份证字段版式", "证件号码特征"] if identity_number else ["身份证字段版式"],
        }

    health_terms = [
        "健康证明",
        "健康证",
        "预防性健康检查",
        "从业人员健康合格证",
        "食品卫生",
        "健康合格",
        "从业类别",
    ]
    health_hits = [term for term in health_terms if term in compact_personnel_text]
    health_support = any(term in compact_personnel_text for term in ("姓名", "有效期", "发证日期", "发证单位", "健康结论"))
    if len(health_hits) >= 2 and health_support:
        return ASSET_CATEGORY_PERSONNEL, ASSET_SUBTYPE_HEALTH, min(0.96, 0.86 + len(health_hits) * 0.02), {
            "ocr_keywords": [term for term in health_hits if term in ocr][:10],
            "context_keywords": [term for term in health_hits if term in context][:10],
            "image_features": ["健康证版式", "人员证件字段"],
        }

    personnel_candidates: list[tuple[float, str, dict[str, list[str]]]] = []
    for rule in PERSONNEL_DOCUMENT_RULES:
        core = [word for word in rule["core"] if word.lower() in personnel_text]
        auxiliary = [word for word in rule["auxiliary"] if word.lower() in personnel_text]
        subtype = str(rule["subtype"])
        filename_hint = any(word in source_name for word in (subtype.lower(), "身份证", "健康证", "health", "idcard"))
        dense_document = bool(profile and (profile.get("edge_density") or 0) >= 0.16)
        if len(core) < 3:
            continue
        has_layout_evidence = bool(auxiliary or filename_hint or dense_document)
        if not has_layout_evidence:
            continue
        confidence = 0.79 + min(0.12, len(core) * 0.018) + (0.03 if auxiliary else 0) + (0.03 if filename_hint else 0)
        evidence = {
            "ocr_keywords": [word for word in core if word in ocr][:10],
            "context_keywords": [word for word in core if word in context][:10],
            "image_features": (["\u6587\u5b57/\u8868\u683c\u5bc6\u96c6"] if dense_document else []) + (["\u7248\u9762\u8f85\u52a9\u7279\u5f81"] if auxiliary else []),
        }
        personnel_candidates.append((min(0.98, confidence), subtype, evidence))
    if personnel_candidates:
        confidence, subtype, evidence = sorted(personnel_candidates, reverse=True, key=lambda item: item[0])[0]
        if confidence >= 0.85:
            return ASSET_CATEGORY_PERSONNEL, subtype, confidence, evidence

    best: tuple[float, str, str, dict[str, list[str]]] | None = None
    for rule in ASSET_SUBTYPE_RULES:
        subtype = str(rule["subtype"])
        category = str(rule["category"])
        ocr_keywords = [keyword for keyword in rule["keywords"] if str(keyword).lower() in ocr]
        context_keywords = [keyword for keyword in rule["keywords"] if str(keyword).lower() in context]
        name_keywords = [keyword for keyword in rule["keywords"] if str(keyword).lower() in source_name]
        matched = set(ocr_keywords + context_keywords + name_keywords)
        if not matched:
            continue
        exact_ocr = subtype.lower() in ocr
        exact_context = subtype.lower() in context
        exact_name = subtype.lower() in source_name
        if exact_ocr:
            confidence = 0.86
        elif ocr_keywords:
            confidence = 0.78
        elif exact_context:
            confidence = 0.76
        elif context_keywords:
            confidence = 0.72
        elif exact_name:
            confidence = 0.71
        else:
            confidence = 0.62
        if len(matched) >= 2:
            confidence += 0.04
        image_features: list[str] = []
        if profile:
            if (profile.get("edge_density") or 0) >= 0.18:
                image_features.append("文字/表格密集")
                confidence += 0.01
            if (profile.get("width") or 0) >= 900 and (profile.get("height") or 0) >= 500:
                image_features.append("高清原图")
        evidence = {
            "ocr_keywords": ocr_keywords[:8],
            "context_keywords": context_keywords[:8],
            "image_features": image_features[:8],
        }
        candidate = (min(confidence, 0.98), subtype, category, evidence)
        if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and len(matched) > sum(len(values) for values in best[3].values())):
            best = candidate
    if best is None:
        fallback_category = {
            "资质证书类": ASSET_CATEGORY_QUALIFICATION,
            "人员资质类": ASSET_CATEGORY_PERSONNEL,
            "设备资产类": ASSET_CATEGORY_VEHICLE,
            "现场场景类": ASSET_CATEGORY_SCENE,
            "业绩证明类": ASSET_CATEGORY_DOCUMENT,
        }.get(legacy_category, ASSET_CATEGORY_SCENE)
        return fallback_category, ASSET_SUBTYPE_OTHER, 0.35, {"ocr_keywords": [], "context_keywords": [], "image_features": []}
    confidence, subtype, category, evidence = best
    if confidence < 0.70:
        return category, ASSET_SUBTYPE_OTHER, confidence, evidence
    return category, subtype, confidence, evidence


def _extract_labeled_value(text: str, labels: list[str], max_len: int = 160) -> str:
    compact = re.sub(r"[ 	]+", " ", text or "")
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_expr})[：:]?\s*([^，,；;。\n]{{1,{max_len}}})", compact)
    if not match:
        return ""
    value = match.group(1).strip(" ：:、,，")[:max_len]
    next_label = re.search(r"\s+(?=[\u4e00-\u9fffA-Za-z]{2,20}\s*[：:])", value)
    if next_label:
        value = value[:next_label.start()].strip(" ：:、,，")
    return value[:max_len]


def _extract_identifier(text: str, labels: list[str], min_len: int = 4, max_len: int = 40) -> str:
    compact = re.sub(r"[ 	]+", " ", text or "")
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_expr})[：:]?\s*([0-9A-Za-z][0-9A-Za-z\-]{{{max(0, min_len - 1)},{max_len - 1}}})", compact, re.I)
    return match.group(1).strip() if match else ""


def _extract_holder_name(text: str) -> str:
    value = _extract_labeled_value(
        text,
        ["持证主体", "持证人", "单位名称", "企业名称", "公司名称", "申请人", "姓名", "抬头", "名称"],
        max_len=100,
    )
    return re.sub(r"(?:统一社会信用代码|证件号码|身份证号|税号).*", "", value).strip()[:100]


def _date_after_labels(text: str, labels: list[str]) -> str:
    compact = re.sub(r"[ 	]+", " ", text or "")
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_expr})[：:]?\s*([0-9]{{4}}[年./-][0-9]{{1,2}}[月./-][0-9]{{1,2}}日?)", compact)
    return _normalize_date(match.group(1)) if match else ""


def _date_range_after_labels(text: str, labels: list[str]) -> tuple[str, str]:
    compact = re.sub(r"[ \t]+", " ", text or "")
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_expr})[：:]?\s*([0-9]{{4}}[年./-][0-9]{{1,2}}[月./-][0-9]{{1,2}}日?)\s*(?:至|到|-|—|~|～)\s*([0-9]{{4}}[年./-][0-9]{{1,2}}[月./-][0-9]{{1,2}}日?)",
        compact,
    )
    if not match:
        return "", ""
    return _normalize_date(match.group(1)), _normalize_date(match.group(2))


def _extract_amount_after_labels(text: str, labels: list[str]) -> str:
    """Extract a numeric invoice amount without mixing it with nearby labels."""
    compact = re.sub(r"\s+", " ", text or "")
    collapsed = re.sub(r"\s+", "", text or "")
    amount_pattern = r"(?:人民币|RMB|¥|￥)?\s*([+-]?\d[\d,]*(?:\.\d{1,2})?)"
    for label in sorted(labels, key=len, reverse=True):
        # The generic "金额" label also appears inside "价税合计金额" on
        # OCR output. Exclude those phrases so net and gross amounts do not
        # silently swap places.
        prefix_guard = r"(?<!价税合计)(?<!含税金额)" if label == "金额" else ""
        for source in (compact, collapsed):
            label_pattern = re.escape(label)
            if source == collapsed:
                label_pattern = r"\s*".join(re.escape(char) for char in label)
            match = re.search(rf"{prefix_guard}{label_pattern}[：:]?\s*{amount_pattern}", source, re.I)
            if match:
                prefix_text = re.sub(r"\s+", "", source[max(0, match.start() - 12):match.start()])
                if label in {"金额", "金额合计", "合计金额"} and prefix_text.endswith(("价税合计", "含税金额", "价税")):
                    continue
                return match.group(1).replace(",", "")

            # OCR often places a table separator or a short unit between the
            # label and its value. Keep the fallback window deliberately short
            # so a nearby tax/total column cannot be mistaken for this value.
            label_match = re.search(rf"{prefix_guard}{label_pattern}[：:]?", source, re.I)
            if label_match:
                prefix_text = re.sub(r"\s+", "", source[max(0, label_match.start() - 12):label_match.start()])
                if label in {"金额", "金额合计", "合计金额"} and prefix_text.endswith(("价税合计", "含税金额", "价税")):
                    continue
                tail = source[label_match.end(): label_match.end() + (48 if label != "金额" else 28)]
                amount_match = re.search(amount_pattern, tail, re.I)
                if amount_match:
                    return amount_match.group(1).replace(",", "")
    return ""


def _derive_invoice_net_amount(total_amount: str, tax_amount: str) -> str:
    """Derive the net amount when an invoice exposes only total and tax."""
    try:
        total = Decimal(str(total_amount or "").replace(",", "").strip())
        tax = Decimal(str(tax_amount or "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return ""
    if total < 0 or tax < 0 or total < tax:
        return ""
    net = (total - tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(net, "f")


def _extract_qualification_fields(text: str) -> dict[str, str]:
    return {
        "cert_no": _extract_identifier(text, ["证书编号", "证件编号", "许可证编号", "统一社会信用代码", "编号", "证号"], min_len=6),
        "issuing_authority": _extract_labeled_value(text, ["发证机关", "发证部门", "认证机构", "检验机构"], 120),
        "expire_date": _date_after_labels(text, ["有效期至", "有效期限", "有效期", "截止日期", "证书有效期"]),
        "holder_name": _extract_holder_name(text),
        "business_scope": _extract_labeled_value(text, ["经营范围", "许可范围", "认证范围", "检测项目"], 240),
    }


def _extract_personnel_fields(text: str) -> dict[str, str]:
    compact = re.sub(r"\s+", " ", text or "")
    id_match = re.search(r"\b([1-9]\d{5}(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9Xx])\b", compact)
    start_date, end_date = _date_range_after_labels(text, ["有效期限", "有效期", "有效期自", "有效日期"])
    is_identity = any(token in compact for token in ["居民身份证", "公民身份号码", "身份证号", "身份证号码"])
    is_health = any(token in compact for token in ["健康证明", "健康证", "预防性健康检查", "从业人员健康合格证"])
    fields = {
        "name": _extract_labeled_value(text, ["姓名", "名字", "员工姓名"], 40),
        "gender": _extract_labeled_value(text, ["性别"], 10),
        "id_number": id_match.group(1) if id_match else _extract_identifier(text, ["身份证号", "身份证号码", "证件号"], min_len=8, max_len=24),
        "position": _extract_labeled_value(text, ["岗位", "职位", "职务", "工种"], 50),
        "expire_date": _date_after_labels(text, ["有效期至", "有效期限", "有效期", "截止日期"]),
    }
    if start_date:
        fields["valid_from"] = start_date
    if end_date:
        fields["valid_until"] = end_date
        fields["expire_date"] = end_date
    if is_identity:
        fields.update({
            "ethnicity": _extract_labeled_value(text, ["民族"], 20),
            "birth_date": _date_after_labels(text, ["出生", "出生日期"]),
            "address": _extract_labeled_value(text, ["住址", "住"], 180),
            "issuing_authority": _extract_labeled_value(text, ["签发机关", "签发单位"], 100),
            "valid_from": fields.get("valid_from", "") or _date_after_labels(text, ["有效期起始日", "有效期自"]),
            "valid_until": fields.get("valid_until", "") or _date_after_labels(text, ["有效期截止日", "有效期至"]),
        })
    if is_health:
        fields.update({
            "age": _extract_labeled_value(text, ["年龄"], 8),
            "occupation_type": _extract_labeled_value(text, ["从业类别", "从业种类", "工种"], 80),
            "issue_date": _date_after_labels(text, ["发证日期", "发证时间", "签发日期"]),
            "valid_until": fields.get("valid_until", "") or _date_after_labels(text, ["有效期至", "有效期限", "有效期"]),
            "issuing_unit": _extract_labeled_value(text, ["发证单位", "发证机构", "发证机关"], 100),
            "health_conclusion": _extract_labeled_value(text, ["健康结论", "检查结论", "结论"], 80),
        })
    return {key: value for key, value in fields.items() if value}


def _extract_personnel_fields_resilient(text: str, subtype: str) -> dict[str, str]:
    """Recover high-value personnel fields when OCR loses punctuation or spacing."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    nospace = re.sub(r"\s+", "", text or "")
    boundary_labels = [
        "姓名", "名字", "员工姓名", "性别", "民族", "出生", "出生日期", "住址",
        "公民身份号码", "身份证号码", "证件号码", "证书编号", "证件编号", "编号", "证号",
        "签发机关", "签发单位", "有效期限", "有效期起始日", "有效期限自", "有效期自",
        "有效期截止日", "有效期限至", "有效期至", "有效期", "年龄", "从业类别", "从业种类",
        "工种", "发证日期", "发证时间", "签发日期", "发证单位", "发证机关", "发证机构",
        "健康结论", "检查结论", "结论", "岗位", "职位", "职务",
    ]

    def value(labels: list[str], max_len: int = 120) -> str:
        label_expr = "|".join(re.escape(label) for label in labels)
        boundary_expr = "|".join(re.escape(label) for label in boundary_labels)
        match = re.search(
            rf"(?:{label_expr})\s*[:：]?\s*(?P<value>[^:：,，;；。\n]{{1,{max_len}}}?)(?=\s*(?:{boundary_expr})\s*[:：]?|$)",
            compact,
        )
        if not match:
            return ""
        return match.group("value").strip(" :：,，;；。").strip()[:max_len]

    def date_value(labels: list[str]) -> str:
        label_expr = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_expr})\s*[:：]?\s*(20\d{{2}}\s*[年./-]\s*\d{{1,2}}\s*[月./-]\s*\d{{1,2}})", compact)
        if not match:
            return ""
        raw = re.sub(r"\s+", "", match.group(1)).replace(".", "-").replace("/", "-")
        return _normalize_date(raw)

    fields: dict[str, str] = {}
    name = value(["姓名", "名字", "员工姓名"], 24)
    if not name:
        match = re.search(r"姓名\s*[:：]?\s*([\u4e00-\u9fff]{2,8})", compact)
        name = match.group(1) if match else ""
    if name:
        fields["name"] = name

    gender = value(["性别"], 8)
    if gender:
        fields["gender"] = gender

    id_match = re.search(r"[1-9]\d{5}(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9Xx]", nospace)
    if id_match:
        fields["id_number"] = id_match.group(0)
    else:
        identifier = value(["公民身份号码", "身份证号码", "证件号码", "证书编号", "证件编号", "编号", "证号"], 40)
        if identifier:
            fields["id_number" if subtype == ASSET_SUBTYPE_IDENTITY else "cert_no"] = identifier

    if subtype == ASSET_SUBTYPE_IDENTITY:
        for key, labels, max_len in (
            ("ethnicity", ["民族"], 20),
            ("address", ["住址"], 180),
            ("issuing_authority", ["签发机关", "签发单位"], 100),
        ):
            found = value(labels, max_len)
            if found:
                fields[key] = found
        valid_from = date_value(["有效期起始日", "有效期限自", "有效期自"])
        valid_until = date_value(["有效期截止日", "有效期限至", "有效期至"])
        if valid_from:
            fields["valid_from"] = valid_from
        if valid_until:
            fields["valid_until"] = valid_until
            fields["expire_date"] = valid_until
    elif subtype == ASSET_SUBTYPE_HEALTH:
        for key, labels, max_len in (
            ("age", ["年龄"], 8),
            ("occupation_type", ["从业类别", "从业种类", "工种"], 80),
            ("issuing_unit", ["发证单位", "发证机关", "发证机构"], 100),
            ("health_conclusion", ["健康结论", "检查结论", "结论"], 80),
        ):
            found = value(labels, max_len)
            if found:
                fields[key] = found
        issue_date = date_value(["发证日期", "发证时间", "签发日期"])
        valid_until = date_value(["有效期至", "有效期限", "有效期"])
        if issue_date:
            fields["issue_date"] = issue_date
        if valid_until:
            fields["valid_until"] = valid_until
            fields["expire_date"] = valid_until
    return fields


def _extract_vehicle_fields(text: str) -> dict[str, str]:
    compact = re.sub(r"\s+", " ", text or "")
    plate_match = re.search(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-Z0-9挂学警港澳]{5,6}", compact, re.I)
    return {
        "plate_no": plate_match.group(0) if plate_match else _extract_labeled_value(text, ["车牌号", "号牌号码", "车辆号牌"], 20),
        "vehicle_type": _extract_labeled_value(text, ["车辆类型", "车型", "车辆名称"], 60),
        "approved_load": _extract_labeled_value(text, ["核定载重", "核载", "总质量"], 40),
        "inspection_expire_date": _date_after_labels(text, ["年检有效期", "检验有效期", "检验有效至", "下次检验日期"]),
        "cold_chain_equipment": _extract_labeled_value(text, ["冷链设备", "制冷设备", "温控设备", "冷藏设备"], 120),
    }


def _extract_invoice_fields(text: str) -> dict[str, str]:
    issue_date = _date_after_labels(text, ["开票日期", "开具日期", "出票日期", "日期"])
    amount_excluding_tax = _extract_amount_after_labels(
        text,
        ["不含税金额", "金额合计", "金额", "合计金额"],
    )
    total_amount = _extract_amount_after_labels(
        text,
        ["价税合计(小写)", "价税合计（小写）", "价税合计", "含税金额", "小写"],
    )
    tax_amount = _extract_amount_after_labels(text, ["税额", "合计税额"])
    # A digital invoice's column heading "税额" can appear before the net
    # amount in extracted PDF text. Prefer the explicit two-number summary row
    # when it reconciles with the tax-inclusive total.
    summary = re.search(
        r"(?m)^[ \t]*合[ \t]*计[ \t]*[¥￥]?[ \t]*([0-9][0-9,]*\.\d{2})[ \t]+[¥￥]?[ \t]*([0-9][0-9,]*\.\d{2})[ \t]*$",
        text or "",
    )
    if summary:
        net, tax = (part.replace(",", "") for part in summary.groups())
        if not total_amount or Decimal(net) + Decimal(tax) == Decimal(total_amount):
            amount_excluding_tax, tax_amount = net, tax
    if not amount_excluding_tax:
        amount_excluding_tax = _derive_invoice_net_amount(total_amount, tax_amount)
    title = _extract_labeled_value(text, ["购买方名称", "销售方名称", "发票抬头", "抬头"], 100)
    if not title:
        buyer = re.search(
            r"(?m)^购[ \t]*名称[：:][ \t]*(.+?)(?=[ \t]+销[ \t]*名称[：:]|[ \t]*$)",
            text or "",
        )
        if buyer:
            title = buyer.group(1).strip()
    return {
        "invoice_code": _extract_identifier(text, ["发票代码", "代码"], min_len=8, max_len=30),
        "invoice_number": _extract_identifier(text, ["发票号码", "发票号", "票号"], min_len=6, max_len=30),
        "issue_date": issue_date,
        "amount_excluding_tax": amount_excluding_tax,
        "amount": amount_excluding_tax or total_amount,
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "title": title,
        "tax_no": _extract_identifier(text, ["纳税人识别号", "税号"], min_len=6, max_len=30),
    }


def _extract_structured_asset_info(
    source_name: str,
    context_text: str,
    image_ocr: str,
    profile: dict[str, Any] | None,
    legacy_category: str,
    tags: list[str],
    *,
    person_info: dict[str, Any] | None = None,
    employee_info: dict[str, Any] | None = None,
    contract_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    category, subtype, confidence, evidence = _classify_structured_asset(source_name, context_text, image_ocr, profile, legacy_category)
    text = "\n".join([source_name, image_ocr, context_text])
    fields: dict[str, str]
    if subtype == ASSET_SUBTYPE_OTHER:
        fields = {}
    elif category == ASSET_CATEGORY_QUALIFICATION:
        fields = _extract_qualification_fields(text)
    elif category == ASSET_CATEGORY_PERSONNEL:
        fields = _extract_personnel_fields(text)
        resilient_fields = _extract_personnel_fields_resilient(text, subtype)
        fields.update({key: value for key, value in resilient_fields.items() if value})
    elif category == ASSET_CATEGORY_VEHICLE:
        fields = _extract_vehicle_fields(text)
    elif category == ASSET_CATEGORY_DOCUMENT:
        fields = _extract_invoice_fields(text)
    else:
        fields = {"scene_keywords": subtype}
    fields = {key: value for key, value in fields.items() if value}
    expire_date = _normalize_date(fields.get("expire_date") or fields.get("inspection_expire_date")) or ""
    cert_no = fields.get("cert_no") or fields.get("id_number") or fields.get("invoice_number") or ""
    holder_name = fields.get("holder_name") or fields.get("title") or fields.get("name") or ""
    structured_info = {
        "category": category,
        "asset_sub_type": subtype,
        "confidence": round(confidence, 4),
        "evidence": evidence,
        "fields": fields,
        "applicable_scenarios": PERSONNEL_SCENARIO_TAGS.get(subtype, []),
        "legacy": {
            "person_info": person_info or {},
            "employee_info": employee_info or {},
            "contract_info": contract_info or {},
            "tags": tags[:12],
        },
    }
    dimension_tags = {
        "applicable_scenarios": PERSONNEL_SCENARIO_TAGS.get(subtype, []),
        "asset_sub_type": subtype,
    }
    return {
        "structured_info": structured_info,
        "asset_sub_type": subtype,
        "expire_date": expire_date,
        "cert_no": cert_no,
        "holder_name": holder_name,
        "category": category,
        "confidence": confidence,
        "dimension_tags": dimension_tags,
    }


def _extract_person_info(category: str, tags: list[str], context_text: str, image_ocr: str, source_name: str) -> dict[str, str] | None:
    haystack = f"{category} {' '.join(tags)} {source_name} {image_ocr} {context_text[:1200]}"
    if not re.search(r"人员|健康证|身份证|从业|资格证|社保|员工|姓名|性别", haystack):
        return None
    compact = re.sub(r"\s+", " ", haystack)
    info: dict[str, str] = {}
    name_match = re.search(r"(?:姓名|名字|员工)[:：\s]*([\u4e00-\u9fff]{2,4})", compact)
    if name_match:
        info["name"] = name_match.group(1)
    gender_match = re.search(r"(?:性别|男/女)[:：\s]*(男|女)", compact)
    if gender_match:
        info["gender"] = gender_match.group(1)
    id_match = re.search(r"(?:身份证(?:号|号码)?|证件(?:号|编号)?|编号|证号)[:：\s]*([0-9A-Za-z]{6,24})", compact)
    if not id_match:
        id_match = re.search(r"\b([1-9]\d{5}(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9Xx])\b", compact)
    if id_match:
        info["id_number"] = id_match.group(1)
    position_match = re.search(r"(?:职位|岗位|职务)[:：\s]*([\u4e00-\u9fffA-Za-z0-9]{2,18})", compact)
    if position_match:
        info["position"] = position_match.group(1)
    valid_match = re.search(r"(?:有效期|有效期限|有效至|截止日期)[:：\s]*([0-9]{4}[年./-][0-9]{1,2}[月./-][0-9]{1,2}日?)", compact)
    if not valid_match:
        valid_match = re.search(r"([0-9]{4}[年./-][0-9]{1,2}[月./-][0-9]{1,2}日?)\s*(?:止|到期|前)", compact)
    if valid_match:
        info["valid_until"] = valid_match.group(1)
    return info or None


def _employee_info_from_person(category: str, tags: list[str], context_text: str, image_ocr: str, source_name: str, person_info: dict[str, str] | None) -> tuple[dict[str, str] | None, str | None]:
    haystack = f"{category} {' '.join(tags)} {source_name} {image_ocr} {context_text[:1800]}"
    if not re.search(r"人员|员工|身份证|驾驶证|健康证|从业|资格证|社保|简历|岗位|职务|司机|技术|检测|仓储|分拣", haystack):
        return None, None
    role = _infer_employee_role(haystack)
    info = dict(person_info or {})
    cert_types = []
    for label in ["身份证", "驾驶证", "健康证", "从业资格证", "社保证明", "资格证", "上岗证"]:
        if label in haystack:
            cert_types.append(label)
    if cert_types:
        info["certificate_type"] = "、".join(dict.fromkeys(cert_types))
    if info.get("id_number") and not info.get("certificate_no"):
        info["certificate_no"] = info["id_number"]
    if role:
        info["role"] = role
    if not info.get("position") and role:
        info["position"] = role
    skill_tags = [word for word in ["冷链配送", "食品安全", "检测检验", "仓储分拣", "车辆驾驶", "项目管理", "质量控制", "技术服务"] if word[:2] in haystack or word in haystack]
    if skill_tags:
        info["skill_tags"] = "、".join(skill_tags)
    source_hint = _source_chapter_from_text(context_text)
    if source_hint and not info.get("source_context"):
        info["source_context"] = source_hint
    cleaned = {key: str(value).strip()[:160] for key, value in info.items() if value}
    return (cleaned or None), role


def _compact_employee_info(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    mapping = {
        "name": ["name", "姓名", "employee_name", "人员姓名"],
        "gender": ["gender", "性别"],
        "role": ["role", "employee_role", "职业角色", "人员角色", "岗位类型"],
        "certificate_type": ["certificate_type", "证件类型", "资料类型", "证照类型"],
        "certificate_no": ["certificate_no", "certificate_number", "id_number", "证件编号", "证号", "身份证号", "驾驶证号"],
        "position": ["position", "职位", "岗位", "职务"],
        "valid_until": ["valid_until", "有效期", "有效至", "截止日期"],
        "skill_tags": ["skill_tags", "skills", "技能标签", "能力标签"],
        "source_context": ["source_context", "来源上下文", "来源章节"],
    }
    result: dict[str, str] = {}
    for key, aliases in mapping.items():
        for alias in aliases:
            raw = value.get(alias)
            if raw is None:
                continue
            if isinstance(raw, list):
                raw = "、".join(str(item).strip() for item in raw if str(item).strip())
            text = str(raw).strip()
            if text:
                result[key] = text[:180]
                break
    if result.get("role"):
        normalized_role = _normalize_employee_role(result["role"])
        if normalized_role:
            result["role"] = normalized_role
    elif result.get("position"):
        normalized_role = _normalize_employee_role(result["position"])
        if normalized_role:
            result["role"] = normalized_role
    if result.get("valid_until"):
        result["valid_until"] = _normalize_date(result["valid_until"]) or result["valid_until"][:80]
    return {key: value for key, value in result.items() if value} or None


def _normalize_employee_role(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in EMPLOYEE_ROLE_CATEGORIES:
        return text
    alias_rules = [
        ("项目经理", ["项目经理", "项目负责人", "负责人", "管理人员", "项目管理"]),
        ("卡车司机", ["卡车司机", "货车司机", "冷藏车司机", "厢式货车", "B2", "A2", "驾驶证"]),
        ("配送司机", ["配送司机", "司机", "驾驶员", "配送员", "运输员"]),
        ("食品安全员", ["食品安全", "安全员", "品控", "质量安全"]),
        ("检测人员", ["检测", "检验", "化验", "实验室", "质检"]),
        ("仓储分拣人员", ["仓储", "分拣", "仓管", "库管", "拣配"]),
        ("技术人员", ["技术", "技术员", "工程师", "运维", "信息化"]),
        ("客服人员", ["客服", "客户服务", "售后", "回访"]),
        ("后勤人员", ["后勤", "行政", "保障"]),
    ]
    for role, aliases in alias_rules:
        if any(alias in text for alias in aliases):
            return role
    return "其他人员" if re.search(r"人员|员工|证件|资质|岗位|职务", text) else ""


def _infer_employee_role(text: str) -> str:
    role_rules = [
        ("项目经理", ["项目经理", "项目负责人", "负责人", "管理团队", "项目管理"]),
        ("卡车司机", ["卡车", "货车", "厢式货车", "冷藏车", "准驾车型", "B2", "A2", "驾驶证"]),
        ("配送司机", ["司机", "驾驶员", "配送员", "配送车辆", "冷链配送", "运输"]),
        ("食品安全员", ["食品安全员", "食品安全", "安全管理员", "安全管理", "品控"]),
        ("检测人员", ["检测员", "检验员", "化验员", "检测", "检验", "实验室", "质量检测"]),
        ("仓储分拣人员", ["仓储", "分拣", "库管", "仓管", "拣配", "出入库"]),
        ("技术人员", ["技术人员", "技术员", "工程师", "技术负责人", "信息化", "系统运维"]),
        ("客服人员", ["客服", "客户服务", "售后", "回访", "接单"]),
        ("后勤人员", ["后勤", "行政", "保障人员"]),
    ]
    for role, keywords in role_rules:
        if any(keyword in text for keyword in keywords):
            return role
    return "其他人员" if re.search(r"人员|员工|身份证|健康证|社保|从业", text) else ""


def _extract_contract_info(category: str, tags: list[str], context_text: str, image_ocr: str, source_name: str) -> dict[str, str] | None:
    haystack = re.sub(r"\s+", " ", f"{category} {' '.join(tags)} {source_name} {image_ocr} {context_text[:2600]}").strip()
    if not re.search(r"合同|协议|中标通知书|验收报告|客户评价|业绩|采购订单|服务期限|甲方|乙方|签订", haystack):
        return None
    info: dict[str, str] = {}
    title_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()《》·\-]{4,60}(?:合同|协议|中标通知书|验收报告|客户评价|服务协议|采购订单))", haystack)
    if title_match:
        info["contract_name"] = title_match.group(1)
    project_match = re.search(r"(?:项目名称|项目名|工程名称)[:：\s]*([^，。；;\n]{4,80})", haystack)
    if project_match:
        info["project_name"] = project_match.group(1).strip()
    party_a = re.search(r"(?:甲方|采购人|委托方|发包方)[:：\s]*([^，。；;\n]{4,80})", haystack)
    party_b = re.search(r"(?:乙方|供应商|承包方|受托方|投标人)[:：\s]*([^，。；;\n]{4,80})", haystack)
    if party_a:
        info["party_a"] = party_a.group(1).strip()
    if party_b:
        info["party_b"] = party_b.group(1).strip()
    amount = re.search(r"(?:合同金额|合同价|中标金额|金额|价款)[:：\s]*([人民币￥¥]?[0-9,.]+\s*(?:万元|元)?)", haystack)
    if amount:
        info["amount"] = amount.group(1).strip()
    signed_date = _find_labeled_or_nearby_date(haystack, ["签订日期", "签署日期", "合同日期", "落款日期", "中标日期", "验收日期"])
    if signed_date:
        info["signed_date"] = signed_date
    period_match = re.search(r"(?:服务期限|合同期限|履约期限|供货期限)[:：\s]*([^，。；;\n]{4,80})", haystack)
    if period_match:
        info["service_period"] = period_match.group(1).strip()
    type_hint = "合同"
    for keyword, label in [("中标", "中标通知"), ("验收", "验收资料"), ("评价", "客户评价"), ("协议", "服务协议"), ("订单", "采购订单")]:
        if keyword in haystack:
            type_hint = label
            break
    info["contract_type"] = type_hint
    archive_date = _archive_date_for_asset(info, image_ocr, context_text)
    if archive_date:
        info["archive_date"] = archive_date
        info["archive_year"] = archive_date[:4]
    cleaned = {key: str(value).strip()[:180] for key, value in info.items() if value}
    return cleaned or None


def _compact_contract_info(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    mapping = {
        "contract_name": ["contract_name", "合同名称", "文件名称", "name", "title"],
        "project_name": ["project_name", "项目名称", "项目名"],
        "party_a": ["party_a", "甲方", "采购人", "委托方", "发包方"],
        "party_b": ["party_b", "乙方", "供应商", "承包方", "受托方", "投标人"],
        "amount": ["amount", "合同金额", "合同价", "中标金额", "金额"],
        "signed_date": ["signed_date", "签订日期", "签署日期", "合同日期", "中标日期", "验收日期"],
        "service_period": ["service_period", "服务期限", "合同期限", "履约期限", "供货期限"],
        "contract_type": ["contract_type", "合同类型", "资料类型", "类型"],
        "archive_year": ["archive_year", "归档年份", "年份"],
        "archive_date": ["archive_date", "归档日期", "归档时间"],
    }
    result: dict[str, str] = {}
    for key, aliases in mapping.items():
        for alias in aliases:
            raw = value.get(alias)
            if raw is None:
                continue
            if isinstance(raw, list):
                raw = "、".join(str(item).strip() for item in raw if str(item).strip())
            text = str(raw).strip()
            if text:
                result[key] = text[:220]
                break
    for key in ["signed_date", "archive_date"]:
        if result.get(key):
            result[key] = _normalize_date(result[key]) or result[key][:80]
    if not result.get("archive_date"):
        result["archive_date"] = _archive_date_for_asset(result, "", "") or ""
    if result.get("archive_date") and not result.get("archive_year"):
        result["archive_year"] = result["archive_date"][:4]
    if not result.get("contract_type"):
        joined = " ".join(result.values())
        if "中标" in joined:
            result["contract_type"] = "中标通知"
        elif "验收" in joined:
            result["contract_type"] = "验收资料"
        elif "评价" in joined:
            result["contract_type"] = "客户评价"
        else:
            result["contract_type"] = "合同"
    cleaned = {key: value for key, value in result.items() if value}
    return cleaned or None


def _find_labeled_or_nearby_date(text: str, labels: list[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{label}[:：\s]*([0-9]{{4}}[年./-][0-9]{{1,2}}[月./-][0-9]{{1,2}}日?)", text)
        if match:
            return _normalize_date(match.group(1))
    dates = re.findall(r"[0-9]{4}[年./-][0-9]{1,2}[月./-][0-9]{1,2}日?", text)
    return _normalize_date(dates[0]) if dates else None


def _archive_date_for_asset(contract_info: dict[str, str] | None, image_ocr: str, context_text: str) -> str | None:
    if contract_info:
        for key in ["archive_date", "signed_date", "valid_until", "service_start"]:
            value = contract_info.get(key)
            if value:
                normalized = _normalize_date(value)
                if normalized:
                    return normalized
    return _find_labeled_or_nearby_date(f"{image_ocr}\n{context_text}", ["签订日期", "签署日期", "中标日期", "验收日期", "有效期", "日期"])


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    match = re.search(r"([0-9]{4})[年./-]([0-9]{1,2})[月./-]([0-9]{1,2})日?", text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _image_description(name: str, category: str, tags: list[str], page_number: int | None, source_chapter: str | None, confidence: float) -> str:
    page = f"，来源第{page_number}页" if page_number else ""
    chapter = f"，章节：{source_chapter}" if source_chapter else ""
    tag_text = "、".join(tags[:4]) if tags else "图片资料"
    return f"{category}：{Path(name).stem}{page}{chapter}，识别标签：{tag_text}，可作为标书证明材料或配图复用。分类置信度 {int(confidence * 100)}%。"


def _image_annotation(name: str, category: str, tags: list[str], page_number: int | None, source_chapter: str | None, context_text: str, image_ocr: str, desc: str) -> str:
    subject = _annotation_subject(name, category, tags, image_ocr, context_text)
    evidence = _annotation_points(f"{image_ocr}\n{context_text}\n{desc}")
    page = f"第{page_number}页" if page_number else "原始图片资料"
    chapter = f"，位于“{source_chapter}”章节" if source_chapter else ""
    business = _annotation_business_value(category, subject)
    return f"图片核心主体：{subject}（来源：{page}{chapter}）。\n关键信息提取：{evidence}\n业务说明：{business}"


def _annotation_subject(name: str, category: str, tags: list[str], image_ocr: str, context_text: str) -> str:
    source = f"{Path(name).stem} {' '.join(tags)} {image_ocr[:500]} {context_text[:800]}"
    subject_rules = [
        ("营业执照", "企业营业执照正本"),
        ("食品经营许可证", "食品经营许可证扫描件"),
        ("经营许可证", "经营许可证证明材料"),
        ("健康证", "人员健康证明材料"),
        ("身份证", "人员身份证明材料"),
        ("社保", "人员社保证明材料"),
        ("中标", "项目中标证明材料"),
        ("合同", "项目合同业绩证明"),
        ("验收", "项目验收证明材料"),
        ("冷库", "冷库现场实景"),
        ("冷链", "冷链配送保障场景"),
        ("配送车", "冷链配送车辆实拍"),
        ("车辆", "配送车辆实拍"),
        ("检测", "检测设备或检测场景"),
        ("设备", "项目服务设备资产"),
    ]
    for keyword, title in subject_rules:
        if keyword in source:
            return title
    if tags:
        return f"{tags[0]}相关证明图片"
    return category.replace("类", "") + "图片资料"


def _annotation_points(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    candidates = []
    patterns = [
        r"统一社会信用代码[:：\s]*[A-Z0-9]{12,20}",
        r"许可证编号[:：\s]*[A-Z0-9\-]{6,30}",
        r"证书编号[:：\s]*[A-Z0-9\-]{6,30}",
        r"有效期[:：\s]*[^，。；;\n]{4,40}",
        r"经营范围[:：\s]*[^，。；;\n]{6,80}",
        r"项目名称[:：\s]*[^，。；;\n]{4,60}",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, cleaned))
    if not candidates:
        keywords = [word for word in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{3,18}", cleaned) if any(key in word for key in ["资质", "证书", "车辆", "冷库", "设备", "人员", "合同", "验收", "检测", "配送", "经营"])]
        candidates = list(dict.fromkeys(keywords))[:4]
    if candidates:
        return "；".join(list(dict.fromkeys(candidates))[:4]) + "。"
    return "结合图片内容及所在章节，可作为响应招标评分点的可追溯证明材料。"


def _annotation_business_value(category: str, subject: str) -> str:
    if category == "资质证书类":
        return f"该图片用于证明投标人具备项目履约所需的合规资质与经营基础，可支撑资格审查、资质响应及商务评分材料编制。"
    if category == "现场场景类":
        return f"该图片直观呈现{subject}，可用于增强服务场景、履约条件和现场保障能力的可信度。"
    if category == "人员资质类":
        return "该图片用于证明项目团队人员配置、健康合规或从业资格情况，可支撑人员保障与服务组织章节。"
    if category == "业绩证明类":
        return "该图片可作为同类项目经验、服务履约成果及客户认可度的证明，适合用于业绩案例与综合实力章节。"
    if category == "设备资产类":
        return "该图片用于证明设备配置、检测能力或信息化保障能力，可增强技术方案的可执行性和可验证性。"
    return "该图片可作为标书图文补充材料，结合上下文用于说明企业能力、履约保障或附件证明。"


def _build_semantic_annotation(asset: dict[str, Any], item_title: str = "", document_text: str = "", base_annotation: str = "") -> str:
    source = "\n".join(
        str(value or "")
        for value in (
            item_title,
            asset.get("source_chapter"),
            document_text[:2600],
            asset.get("ocr_text"),
            asset.get("asset_sub_type"),
            asset.get("category"),
            " ".join(asset.get("tags") or []),
        )
    )
    matched = [label for label, words in SCENE_KEYWORDS.items() if any(word in source for word in words)]
    keyword_values: list[str] = []
    for label, words in SCENE_KEYWORDS.items():
        for word in words:
            if word in source and word not in keyword_values:
                keyword_values.append(word)
    ocr_text = re.sub(r"\s+", " ", str(asset.get("ocr_text") or "")).strip()
    context_text = re.sub(r"\s+", " ", str(document_text or "")).strip()
    subtype = str(asset.get("asset_sub_type") or "").strip() or "其他附件"
    category = str(asset.get("category") or "其他附件类").strip()
    existing = str(base_annotation or asset.get("annotation") or "").strip()
    parts = [
        f"文档主题：{item_title or '知识库素材'}",
        f"所属章节：{asset.get('source_chapter') or '未标注'}",
        f"素材分类：{category}；细分类别：{subtype}",
        f"场景关键词：{'、'.join(keyword_values[:12]) or '通用标书素材'}",
        f"场景标签：{'、'.join(matched) or '其他场景'}",
        f"OCR摘要：{ocr_text[:700] or '暂无OCR文字'}",
        f"文档上下文：{context_text[:900] or '暂无文档上下文'}",
    ]
    if existing:
        parts.append(f"原始语义注释：{existing[:1400]}")
    return "\n".join(parts)[:2400]


async def _generate_asset_annotation_with_ai(asset: dict[str, Any], item_title: str, document_text: str) -> str:
    fallback = _image_annotation(
        asset.get("resource_path") or item_title,
        asset.get("category") or "其他附件类",
        asset.get("tags") or [],
        asset.get("page_num"),
        asset.get("source_chapter"),
        document_text[:1800],
        asset.get("ocr_text") or asset.get("ai_description") or "",
        asset.get("ai_description") or "",
    )
    fallback = _build_semantic_annotation(asset, item_title, document_text, fallback)
    if not qwen_client.has_generation_api_key('knowledge_analysis'):
        return fallback
    result = await qwen_client.chat_completion(
        [
            {"role": "system", "content": "你是政务投标文件图片标注专家。请输出可直接用于标书图注的专业中文注释，不要输出JSON。"},
            {
                "role": "user",
                "content": (
                    "请基于图片所在文档、章节、页码、已有OCR/描述和上下文，生成三段式专业注释。\n"
                    "格式必须为：图片核心主体：...\n关键信息提取：...\n业务说明：...\n"
                    "语言正式、克制、可直接纳入投标文件。\n"
                    f"资料标题：{item_title}\n"
                    f"图片分类：{asset.get('category') or '其他附件类'}\n"
                    f"来源页码：{asset.get('page_num') or '-'}\n"
                    f"来源章节：{asset.get('source_chapter') or '-'}\n"
                    f"已有描述/OCR：{asset.get('ocr_text') or asset.get('ai_description') or ''}\n"
                    f"标签：{'、'.join(asset.get('tags') or [])}\n"
                    f"上下文：{document_text[:2600]}"
                ),
            },
        ], scene="knowledge_analysis",
        temperature=0.12,
        max_tokens=700,
        fallback=fallback,
    )
    return _build_semantic_annotation(asset, item_title, document_text, (result.content or fallback).strip())


def _image_profile(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {"hash": None, "width": None, "height": None, "decorative": False, "reason": "missing"}
    try:
        return _image_profile_from_bytes(path.read_bytes())
    except Exception:
        return {"hash": None, "width": None, "height": None, "decorative": False, "reason": "unreadable"}


def _image_profile_from_bytes(image_bytes: bytes) -> dict[str, Any]:
    if Image is None or ImageStat is None or ImageOps is None or not image_bytes:
        return {"hash": hashlib.md5(image_bytes or b"").hexdigest()[:16] if image_bytes else None, "width": None, "height": None, "decorative": False, "reason": "pillow_unavailable"}
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            image_hash = _dhash(img)
            normalized = ImageOps.autocontrast(img.convert("RGB").resize((96, 96)))
            gray = normalized.convert("L").resize((64, 64))
            stat = ImageStat.Stat(gray)
            variance = float(stat.var[0] if stat.var else 0)
            dominant_ratio = _dominant_color_ratio(normalized)
            edge_density = _edge_density(gray)
            aspect = width / max(1, height)
            inverse_aspect = height / max(1, width)
            decorative = False
            reason = ""
            if width < 200 or height < 200:
                decorative, reason = True, "small_icon_or_logo"
            elif (aspect > 8 or inverse_aspect > 8) and min(width, height) < 120:
                decorative, reason = True, "decorative_line_or_banner"
            elif variance < 8 or (dominant_ratio >= 0.92 and edge_density < 0.035):
                decorative, reason = True, "solid_color_block"
            elif dominant_ratio >= 0.88 and edge_density < 0.018:
                decorative, reason = True, "pure_decorative_art"
            return {"hash": image_hash, "width": width, "height": height, "decorative": decorative, "reason": reason, "variance": variance, "dominant_ratio": dominant_ratio, "edge_density": edge_density}
    except Exception:
        return {"hash": hashlib.md5(image_bytes).hexdigest()[:16], "width": None, "height": None, "decorative": False, "reason": "profile_failed"}


def _dhash(img: Any) -> str:
    if ImageOps is not None:
        gray = ImageOps.autocontrast(img.convert("L")).resize((9, 8))
    else:
        gray = img.convert("L").resize((9, 8))
    pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value <<= 1
            if pixels[row * 9 + col] > pixels[row * 9 + col + 1]:
                value |= 1
    return f"{value:016x}"


def _dominant_color_ratio(img: Any) -> float:
    try:
        small = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=16).resize((48, 48))
        colors = small.getcolors(48 * 48) or []
        if not colors:
            return 0.0
        return max(count for count, _ in colors) / float(48 * 48)
    except Exception:
        return 0.0


def _edge_density(gray: Any) -> float:
    if ImageFilter is None:
        return 0.0
    try:
        edges = gray.filter(ImageFilter.FIND_EDGES)
        pixels = list(edges.getdata())
        if not pixels:
            return 0.0
        return sum(1 for value in pixels if int(value) > 28) / float(len(pixels))
    except Exception:
        return 0.0


def _hash_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    try:
        distance = (int(a, 16) ^ int(b, 16)).bit_count()
        return 1 - distance / 64
    except Exception:
        return 1.0 if a == b else 0.0


def _find_duplicate_asset(image_hash: str | None) -> dict[str, Any] | None:
    if not image_hash:
        return None
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT asset_id, resource_path, image_hash FROM knowledge_asset WHERE image_hash IS NOT NULL AND COALESCE(duplicate_of_asset_id, 0) = 0 ORDER BY asset_id DESC LIMIT 3000"
        ).fetchall()
    for row in rows:
        if _hash_similarity(image_hash, row["image_hash"]) >= 0.95:
            return {"asset_id": int(row["asset_id"]), "resource_path": row["resource_path"], "image_hash": row["image_hash"]}
    return None


def _find_similar_profile(image_hash: str | None, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not image_hash:
        return None
    for candidate in candidates:
        if _hash_similarity(image_hash, candidate.get("image_hash")) >= 0.95:
            return candidate
    return None


def _find_repeated_zone_profile(profile: dict[str, Any], zone: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    image_hash = profile.get("hash")
    if not image_hash or zone not in {"top", "bottom"}:
        return None
    for candidate in candidates:
        if candidate.get("zone") != zone:
            continue
        if not _same_image_size_bucket(profile, candidate):
            continue
        if _hash_similarity(image_hash, candidate.get("hash")) >= 0.90:
            return candidate
    return None


def _is_repeated_zone_decoration(profile: dict[str, Any], zone: str, decorations: list[dict[str, Any]]) -> bool:
    if zone not in {"top", "bottom"} or not profile.get("hash"):
        return False
    return _find_repeated_zone_profile(profile, zone, decorations) is not None


def _same_image_size_bucket(a: dict[str, Any], b: dict[str, Any]) -> bool:
    aw, ah, bw, bh = a.get("width"), a.get("height"), b.get("width"), b.get("height")
    if not aw or not ah or not bw or not bh:
        return True
    return abs(int(aw) - int(bw)) <= 12 and abs(int(ah) - int(bh)) <= 12


def _image_page_zone(rects: list[Any], page_height: float) -> str:
    if not rects:
        return "middle"
    top = min(float(rect.y0) for rect in rects)
    bottom = max(float(rect.y1) for rect in rects)
    if bottom < page_height * 0.18:
        return "top"
    if top > page_height * 0.82:
        return "bottom"
    return "middle"


def _safe_unlink_asset_file(path: Path | None, keep_path: Path | None = None) -> None:
    if not path or not path.exists():
        return
    try:
        if keep_path and path.resolve() == keep_path.resolve():
            return
        upload_root = settings.upload_dir.resolve()
        resolved = path.resolve()
        if upload_root == resolved or upload_root not in resolved.parents:
            return
        path.unlink(missing_ok=True)
    except Exception:
        return


def _refresh_asset_annotation_chunks(item_id: int, sync_vectors: bool = True) -> None:
    from .ocr_chunks import refresh_ocr_chunks
    refresh_ocr_chunks(item_id)
    old_chunk_ids = _chunk_ids_for_item(item_id, title_like="图片语义注释#%")
    project_id, doc_role = _document_rag_metadata(item_id)
    with storage.connect() as conn:
        assets = conn.execute(
            """
            SELECT a.*, i.title AS item_title
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE a.item_id = ? AND COALESCE(a.annotation, '') <> ''
            ORDER BY a.asset_id
            """,
            (item_id,),
        ).fetchall()
        conn.execute(
            "DELETE FROM knowledge_chunk WHERE item_id = ? AND section_title LIKE ?",
            (item_id, "图片语义注释#%"),
        )
        for index, asset in enumerate(assets, start=1):
            asset_dict = _row_asset(asset)
            employee_info = asset_dict.get("employee_info") if isinstance(asset_dict.get("employee_info"), dict) else {}
            contract_info = asset_dict.get("contract_info") if isinstance(asset_dict.get("contract_info"), dict) else {}
            employee_text = "；".join(f"{key}:{value}" for key, value in employee_info.items() if value)
            contract_text = "；".join(f"{key}:{value}" for key, value in contract_info.items() if value)
            # Keep the image-local annotation; never prepend the entire document.
            semantic_text = str(asset_dict.get("annotation") or "")
            content = (
                f"图片语义注释：{semantic_text}\n"
                f"OCR摘要：{str(asset_dict.get('ocr_text') or '')[:700]}\n"
                f"分类：{asset_dict.get('category') or ''}\n"
                f"细分类别：{asset_dict.get('asset_sub_type') or ''}\n"
                f"结构化信息：{_json(asset_dict.get('structured_info') or {})}\n"
                f"证件编号：{asset_dict.get('cert_no') or ''}\n"
                f"持证主体：{asset_dict.get('holder_name') or ''}\n"
                f"有效期：{asset_dict.get('expire_date') or ''}\n"
                f"标签：{'、'.join(asset_dict.get('tags') or [])}\n"
                f"员工角色：{asset_dict.get('employee_role') or ''}\n"
                f"员工信息：{employee_text}\n"
                f"合同归档日期：{asset_dict.get('archive_date') or ''}\n"
                f"合同归档信息：{contract_text}"
            )
            tokens = _tokens(content)
            conn.execute(
                """
                INSERT INTO knowledge_chunk (
                    item_id, chunk_index, section_title, page_number, content, tokens_json, embedding_json,
                    project_id, doc_role, chunk_kind, section_path, create_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id, 100000 + index, f"图片语义注释#{asset_dict['asset_id']}",
                    asset_dict.get("page_num"), content, _json(tokens), _json(_hash_embedding(tokens)),
                    project_id, doc_role, "image_annotation",
                    asset_dict.get("source_chapter") or str(asset["item_title"] or "图片素材"), _now(),
                ),
            )
        conn.execute(
            "UPDATE knowledge_item SET vector_status = ?, vector_error = NULL, vector_updated_at = ? WHERE item_id = ?",
            ("pending" if vector_store.enabled() else "not_configured", _now(), item_id),
        )
        conn.commit()
    if sync_vectors:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            async def refresh_vectors() -> None:
                if old_chunk_ids:
                    await asyncio.to_thread(vector_store.delete_knowledge_chunks, old_chunk_ids)
                _queue_item_vector_sync(item_id)

            task = loop.create_task(refresh_vectors())
            _VECTOR_TASKS.add(task)
            task.add_done_callback(_VECTOR_TASKS.discard)
        else:
            if old_chunk_ids:
                vector_store.delete_knowledge_chunks(old_chunk_ids)
            _persist_vector_sync_task(item_id)
    elif old_chunk_ids:
        vector_store.delete_knowledge_chunks(old_chunk_ids)


def _chunk_ids_for_item(item_id: int, title_like: str | None = None) -> list[int]:
    clauses = ["item_id = ?"]
    params: list[Any] = [item_id]
    if title_like:
        clauses.append("section_title LIKE ?")
        params.append(title_like)
    with storage.connect() as conn:
        rows = conn.execute(f"SELECT chunk_id FROM knowledge_chunk WHERE {' AND '.join(clauses)}", params).fetchall()
    return [int(row["chunk_id"]) for row in rows]


def _delete_item_vectors(item_id: int) -> None:
    vector_store.delete_knowledge_chunks(_chunk_ids_for_item(item_id))


def _sync_item_chunks_to_vector_store(item_id: int) -> dict[str, int | bool]:
    if not vector_store.enabled():
        return {"attempted": 0, "indexed": 0, "complete": False}
    with storage.connect() as conn:
        project = conn.execute(
            """
            SELECT project_id,
                   CASE WHEN tender_doc_id = ? THEN 'tender' ELSE 'bid' END AS doc_role
            FROM bid_project
            WHERE tender_doc_id = ? OR bid_doc_id = ?
            ORDER BY project_id DESC LIMIT 1
            """,
            (item_id, item_id, item_id),
        ).fetchone()
        project_id = int(project["project_id"] or 0) if project else 0
        doc_role = str(project["doc_role"] or "knowledge") if project else "knowledge"
        rows = conn.execute(
            """
            SELECT k.chunk_id, k.item_id, k.section_title, k.page_number, k.content,
                   k.project_id, k.doc_role, k.chunk_kind, k.section_path,
                   i.category_id, i.source_name, i.title
            FROM knowledge_chunk k
            JOIN knowledge_item i ON i.item_id = k.item_id
            WHERE k.item_id = ?
            ORDER BY k.chunk_index, k.chunk_id
            """,
            (item_id,),
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        section_title = str(row["section_title"] or "")
        stored_role = str(row["doc_role"] or "")
        stored_kind = str(row["chunk_kind"] or "")
        records.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "item_id": int(row["item_id"]),
                "category_id": int(row["category_id"] or 0),
                # Prefer the metadata persisted with each chunk. The project
                # lookup above is only a compatibility fallback for old rows.
                "project_id": int(row["project_id"] or project_id) or None,
                "doc_role": stored_role if stored_role in {"tender", "bid"} else doc_role,
                "chunk_kind": stored_kind or (
                    "image_annotation" if section_title.startswith("图片语义注释#") else "document_text"
                ),
                "section_title": section_title,
                "section_path": str(row["section_path"] or section_title),
                "page_number": int(row["page_number"] or 0),
                "source_name": row["source_name"] or row["title"] or "",
                "content": row["content"] or "",
            }
        )
    return vector_store.upsert_knowledge_chunks_with_stats(records)


def _source_chapter_from_text(text: str) -> str | None:
    for line in (text or "").splitlines()[:30]:
        line = line.strip(" ：:，,。\t")
        if not line or len(line) > 60:
            continue
        if _looks_like_heading(line) or any(keyword in line for keyword in ["证书", "资质", "附件", "业绩", "设备", "车辆", "人员", "合同", "证明"]):
            return line[:60]
    return None


async def _enhance_assets_with_qwen_ocr(item_id: int, assets: list[dict[str, Any]], document_text: str) -> dict[str, int]:
    if not getattr(settings, "qwen_ocr_enabled", False):
        print(f"[KnowledgeOCR] qwen_ocr_disabled item_id={item_id}", flush=True)
        return {"requested": 0, "succeeded": 0, "failed": 0, "skipped": len(assets)}
    if not qwen_client.has_qwen_api_key():
        print(f"[KnowledgeOCR] qwen_ocr_skip_no_api_key item_id={item_id}", flush=True)
        return {"requested": 0, "succeeded": 0, "failed": 0, "skipped": len(assets)}
    max_assets = max(1, min(int(settings.qwen_ocr_max_assets_per_item or 1000), 2000))
    updated = 0
    requested = 0
    failed = 0
    skipped = 0
    print(f"[KnowledgeOCR] qwen_ocr_batch_start item_id={item_id} model={qwen_client.selected_model('knowledge_ocr')} assets={len(assets[:max_assets])}", flush=True)
    for asset in assets[:max_assets]:
        resource_path = _resolve_stored_path(asset.get("resource_path"))
        if not resource_path or not resource_path.exists():
            skipped += 1
            print(f"[KnowledgeOCR] qwen_ocr_skip_missing_file item_id={item_id} asset_id={asset.get('asset_id')} path={asset.get('resource_path')}", flush=True)
            continue
        requested += 1
        print(f"[KnowledgeOCR] qwen_ocr_asset_start item_id={item_id} asset_id={asset.get('asset_id')} model={qwen_client.selected_model('knowledge_ocr')}", flush=True)
        analysis = await _analyze_asset_image_with_qwen_ocr(asset, asset.get("item_title") or "知识库图片", document_text)
        if not analysis:
            failed += 1
            print(f"[KnowledgeOCR] qwen_ocr_asset_failed item_id={item_id} asset_id={asset.get('asset_id')}", flush=True)
            continue
        _apply_qwen_ocr_asset_update(item_id, int(asset["asset_id"]), analysis)
        updated += 1
        if updated % 10 == 0:
            print(f"[KnowledgeOCR] qwen_ocr_progress item_id={item_id} updated={updated}/{min(len(assets), max_assets)}", flush=True)
    print(f"[KnowledgeOCR] qwen_ocr_done item_id={item_id} requested={requested} succeeded={updated} failed={failed} skipped={skipped}", flush=True)
    return {"requested": requested, "succeeded": updated, "failed": failed, "skipped": skipped}


async def _analyze_asset_image_with_qwen_ocr(asset: dict[str, Any], item_title: str, document_text: str) -> dict[str, Any] | None:
    if not getattr(settings, "qwen_ocr_enabled", False):
        print(f"[KnowledgeOCR] qwen_ocr_disabled asset_id={asset.get('asset_id')}", flush=True)
        return None
    if not qwen_client.has_qwen_api_key():
        print(f"[KnowledgeOCR] qwen_ocr_not_called_no_api_key asset_id={asset.get('asset_id')}", flush=True)
        return None
    resource_path = _resolve_stored_path(asset.get("resource_path"))
    if not resource_path or not resource_path.exists():
        print(f"[KnowledgeOCR] qwen_ocr_not_called_missing_file asset_id={asset.get('asset_id')} path={asset.get('resource_path')}", flush=True)
        return None
    fallback = ""
    prompt = (
        "你是食材配送投标文件图片OCR与语义标注专家。请直接观察图片，完整识别图片中的中文、英文、数字、编号、日期、单位名称、证照字段、表格字段和印章相关文字。"
        "同时结合上下文判断图片业务分类、人员职业角色、合同归档信息，并生成可直接用于标书图注的专业注释。只输出JSON，不要输出解释。\n"
        "分类只能从以下枚举选择：资质证书类、现场场景类、人员资质类、业绩证明类、设备资产类、其他附件类。\n"
        "人员角色只能从以下枚举选择：项目经理、技术人员、卡车司机、配送司机、食品安全员、检测人员、仓储分拣人员、客服人员、后勤人员、其他人员；如果不是人员资料则返回空字符串。\n"
        "合同/业绩资料需要提取合同名称、项目名称、甲方、乙方、金额、签订日期、服务期限、合同类型，并给出 archive_date，优先使用签订日期/中标日期/验收日期，格式 YYYY-MM-DD。\n"
        "JSON格式：{\"ocr_text\":\"图片中完整可识别文字，尽量逐行保留\",\"category\":\"人员资质类\",\"tags\":[\"身份证\",\"有效期\"],"
        "\"description\":\"一句话精准说明图片主体和关键内容\",\"annotation\":\"图片核心主体：...\\n关键信息提取：...\\n业务说明：...\","
        "\"person_info\":{\"name\":\"\",\"gender\":\"\",\"id_number\":\"\",\"position\":\"\",\"valid_until\":\"\"},"
        "\"employee_info\":{\"name\":\"\",\"role\":\"卡车司机\",\"certificate_type\":\"驾驶证\",\"certificate_no\":\"\",\"valid_until\":\"\",\"skill_tags\":\"冷链配送、车辆驾驶\"},"
        "\"employee_role\":\"卡车司机\","
        "\"contract_info\":{\"contract_name\":\"\",\"project_name\":\"\",\"party_a\":\"\",\"party_b\":\"\",\"amount\":\"\",\"signed_date\":\"\",\"service_period\":\"\",\"contract_type\":\"合同\",\"archive_year\":\"2024\"},\"archive_date\":\"2024-01-22\"}。\n"
        "如果图片不是人员证件，person_info/employee_info 返回空对象、employee_role 返回空字符串；如果不是合同/业绩资料，contract_info 返回空对象、archive_date 返回空字符串。不要编造图片中不存在的编号、日期或姓名。\n"
        f"资料标题：{item_title}\n"
        f"当前分类：{asset.get('category') or '其他附件类'}\n"
        f"来源页码：{asset.get('page_num') or '-'}\n"
        f"来源章节：{asset.get('source_chapter') or '-'}\n"
        f"已有OCR/描述：{asset.get('ocr_text') or asset.get('ai_description') or ''}\n"
        f"上下文：{document_text[:2600]}"
    )
    result = await qwen_client.image_ocr_completion(resource_path, prompt, model=qwen_client.selected_model('knowledge_ocr'), temperature=0.02, max_tokens=2200, fallback=fallback, json_mode=True)
    if not result.usedApi or not result.content.strip():
        print(f"[KnowledgeOCR] qwen_ocr_empty_or_failed asset_id={asset.get('asset_id')} model={qwen_client.selected_model('knowledge_ocr')} error={result.error}", flush=True)
        return None
    payload = _extract_json_object(result.content)
    if not isinstance(payload, dict):
        print(f"[KnowledgeOCR] qwen_ocr_invalid_json asset_id={asset.get('asset_id')} model={qwen_client.selected_model('knowledge_ocr')} content={result.content[:160]}", flush=True)
        return None
    category = _normalize_ai_image_category(str(payload.get("category") or asset.get("category") or ""))
    tags = _parse_tags(payload.get("tags") if isinstance(payload.get("tags"), list) else str(payload.get("tags") or ""))
    ocr_text = str(payload.get("ocr_text") or "").strip()[:12000]
    description = str(payload.get("description") or "").strip()[:1200]
    annotation = str(payload.get("annotation") or "").strip()[:2400]
    person_info = payload.get("person_info") if isinstance(payload.get("person_info"), dict) else None
    employee_info = payload.get("employee_info") if isinstance(payload.get("employee_info"), dict) else None
    employee_role = str(payload.get("employee_role") or "").strip()
    contract_info = payload.get("contract_info") if isinstance(payload.get("contract_info"), dict) else None
    archive_date = _normalize_date(str(payload.get("archive_date") or ""))
    if not annotation:
        annotation = _image_annotation(resource_path.name, category, tags, asset.get("page_num"), asset.get("source_chapter"), document_text, ocr_text, description)
    fallback_employee_info, fallback_role = _employee_info_from_person(category, tags, document_text, ocr_text, resource_path.name, _compact_person_info(person_info))
    employee_info = _compact_employee_info(employee_info) or fallback_employee_info
    employee_role = _normalize_employee_role(employee_role or fallback_role or (employee_info or {}).get("role", ""))
    contract_info = _compact_contract_info(contract_info) or _extract_contract_info(category, tags, document_text, ocr_text, resource_path.name)
    archive_date = archive_date or _archive_date_for_asset(contract_info, ocr_text, document_text)
    structured = _extract_structured_asset_info(
        resource_path.name,
        document_text,
        ocr_text,
        _image_profile(resource_path),
        category,
        tags,
        person_info=person_info,
        employee_info=employee_info,
        contract_info=contract_info,
    )
    return {
        "category": category,
        "tags": tags or [category.replace("类", ""), "QwenOCR增强"],
        "description": description or _image_description(resource_path.name, category, tags, asset.get("page_num"), asset.get("source_chapter"), 0.92),
        "annotation": annotation,
        "ocr_text": ocr_text,
        "person_info": _compact_person_info(person_info) if person_info else None,
        "employee_info": employee_info,
        "employee_role": employee_role,
        "contract_info": contract_info,
        "archive_date": archive_date,
        "structured_info": structured["structured_info"],
        "asset_sub_type": structured["asset_sub_type"],
        "expire_date": structured["expire_date"],
        "cert_no": structured["cert_no"],
        "holder_name": structured["holder_name"],
    }


def _apply_qwen_ocr_asset_update(item_id: int, asset_id: int, analysis: dict[str, Any]) -> None:
    category = _normalize_ai_image_category(str(analysis.get("category") or ""))
    tags = _parse_tags(analysis.get("tags") if isinstance(analysis.get("tags"), list) else str(analysis.get("tags") or ""))
    if "QwenOCR增强" not in tags:
        tags.append("QwenOCR增强")
    description = str(analysis.get("description") or "").strip()[:1200]
    annotation = str(analysis.get("annotation") or "").strip()[:2400]
    ocr_text = str(analysis.get("ocr_text") or "").strip()[:12000]
    person_info = analysis.get("person_info") if isinstance(analysis.get("person_info"), dict) else None
    person_info = _compact_person_info(person_info)
    employee_info = _compact_employee_info(analysis.get("employee_info") if isinstance(analysis.get("employee_info"), dict) else None)
    employee_role = _normalize_employee_role(str(analysis.get("employee_role") or (employee_info or {}).get("role") or ""))
    if employee_info and employee_role:
        employee_info = {**employee_info, "role": employee_role}
    contract_info = _compact_contract_info(analysis.get("contract_info") if isinstance(analysis.get("contract_info"), dict) else None)
    archive_date = _normalize_date(str(analysis.get("archive_date") or "")) or _archive_date_for_asset(contract_info, ocr_text, annotation)
    structured_info = analysis.get("structured_info") if isinstance(analysis.get("structured_info"), dict) else {}
    asset_sub_type = str(analysis.get("asset_sub_type") or "").strip()
    expire_date = _normalize_date(str(analysis.get("expire_date") or "")) or ""
    cert_no = str(analysis.get("cert_no") or "").strip()
    holder_name = str(analysis.get("holder_name") or "").strip()
    if not structured_info or not asset_sub_type:
        structured = _extract_structured_asset_info(
            "",
            annotation,
            ocr_text,
            None,
            category,
            tags,
            person_info=person_info,
            employee_info=employee_info,
            contract_info=contract_info,
        )
        structured_info = structured["structured_info"]
        asset_sub_type = structured["asset_sub_type"]
        expire_date = expire_date or structured["expire_date"]
        cert_no = cert_no or structured["cert_no"]
        holder_name = holder_name or structured["holder_name"]
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_asset
            SET category = ?, tags_json = ?, ai_description = COALESCE(NULLIF(?, ''), ai_description),
                annotation = COALESCE(NULLIF(?, ''), annotation), ocr_text = COALESCE(NULLIF(?, ''), ocr_text),
                person_info = COALESCE(NULLIF(?, ''), person_info),
                employee_info = COALESCE(NULLIF(?, ''), employee_info), employee_role = COALESCE(NULLIF(?, ''), employee_role),
                contract_info = COALESCE(NULLIF(?, ''), contract_info), archive_date = COALESCE(NULLIF(?, ''), archive_date),
                structured_info = COALESCE(NULLIF(?, ''), structured_info), asset_sub_type = COALESCE(NULLIF(?, ''), asset_sub_type),
                expire_date = COALESCE(NULLIF(?, ''), expire_date), cert_no = COALESCE(NULLIF(?, ''), cert_no),
                holder_name = COALESCE(NULLIF(?, ''), holder_name)
            WHERE item_id = ? AND asset_id = ?
            """,
            (
                category,
                _json(tags[:12]),
                description,
                annotation,
                ocr_text,
                _json(person_info) if person_info else "",
                _json(employee_info) if employee_info else "",
                employee_role,
                _json(contract_info) if contract_info else "",
                archive_date or "",
                _json(structured_info) if structured_info else "",
                asset_sub_type,
                expire_date,
                cert_no,
                holder_name,
                item_id,
                asset_id,
            ),
        )
        conn.commit()


def _reextract_one_asset(row: Any, *, only_missing: bool = True) -> dict[str, Any]:
    asset_id = int(row["asset_id"])
    item_id = int(row["item_id"])
    existing_subtype = str(row["asset_sub_type"] or "").strip() if "asset_sub_type" in row.keys() else ""
    existing_structured = str(row["structured_info"] or "").strip() if "structured_info" in row.keys() else ""
    existing_annotation = str(row["annotation"] or "").strip() if "annotation" in row.keys() else ""
    if only_missing and _row_value(row, "ocr_status", "") in {"recognized", "no_text"} and existing_subtype and existing_structured and "场景关键词：" in existing_annotation:
        return {"asset_id": asset_id, "item_id": item_id, "status": "skipped", "reason": "already_extracted"}
    resource_path = _resolve_stored_path(row["resource_path"])
    if not resource_path or not resource_path.exists():
        return {"asset_id": asset_id, "item_id": item_id, "status": "failed", "reason": "file_missing"}
    document_text = str(row["content_text"] or "") if "content_text" in row.keys() else ""
    source_name = str(row["source_name"] or resource_path.name) if "source_name" in row.keys() else resource_path.name
    profile = _image_profile(resource_path)
    from .local_ocr import recognize_cached
    try:
        ocr_result = recognize_cached(resource_path, settings.database_file.parent / "ocr-cache")
    except Exception as exc:
        with storage.connect() as conn:
            conn.execute("UPDATE knowledge_asset SET ocr_status='failed',ocr_error=? WHERE asset_id=?", (str(exc)[:1000], asset_id))
            conn.commit()
        raise
    image_ocr = ocr_result.text
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_asset SET ocr_status=?,ocr_error=NULL,ocr_engine=?,ocr_confidence=?,ocr_text=? WHERE asset_id=?",
                     (ocr_result.status, ocr_result.engine, ocr_result.confidence, image_ocr, asset_id))
        conn.commit()
    # Facts must come from this image, not unrelated pages or old inferred labels.
    legacy_context = ""
    category, tags, confidence = _classify_image(source_name, f"{legacy_context}\n{image_ocr}", profile)
    person_info = _extract_person_info(category, tags, legacy_context, image_ocr, source_name)
    employee_info, employee_role = _employee_info_from_person(category, tags, legacy_context, image_ocr, source_name, person_info)
    contract_info = _extract_contract_info(category, tags, legacy_context, image_ocr, source_name)
    structured = _extract_structured_asset_info(
        source_name,
        legacy_context,
        image_ocr,
        profile,
        category,
        tags,
        person_info=person_info,
        employee_info=employee_info,
        contract_info=contract_info,
    )
    semantic_annotation = _build_semantic_annotation(
        {
            "source_chapter": row["source_chapter"] if "source_chapter" in row.keys() else "",
            "asset_sub_type": structured["asset_sub_type"],
            "category": structured["category"],
            "tags": tags,
            "ocr_text": image_ocr,
            "annotation": "",
        },
        str(row["source_name"] or "") if "source_name" in row.keys() else source_name,
        "",
        "",
    )
    semantic_description = _image_description(
        source_name,
        structured["category"],
        tags,
        row["page_num"] if "page_num" in row.keys() else None,
        row["source_chapter"] if "source_chapter" in row.keys() else None,
        float(structured["confidence"]),
    ) + f" 语义关键词：{'、'.join(semantic_annotation.split('场景关键词：', 1)[-1].splitlines()[0].split('、')[:12])}。"
    archive_date = _archive_date_for_asset(contract_info, image_ocr, legacy_context)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE knowledge_asset
            SET category = ?, tags_json = ?, ocr_text = COALESCE(NULLIF(?, ''), ocr_text),
                ai_description = COALESCE(NULLIF(?, ''), ai_description), annotation = COALESCE(NULLIF(?, ''), annotation),
                person_info = NULLIF(?, ''), employee_info = NULLIF(?, ''),
                employee_role = NULLIF(?, ''), contract_info = NULLIF(?, ''),
                archive_date = NULLIF(?, ''), structured_info = ?, asset_sub_type = ?,
                expire_date = ?, cert_no = ?, holder_name = ?, dimension_tags = ?
            WHERE item_id = ? AND asset_id = ?
            """,
            (
                category,
                _json(tags[:12]),
                image_ocr,
                semantic_description,
                semantic_annotation,
                _json(person_info) if person_info else "",
                _json(employee_info) if employee_info else "",
                employee_role,
                _json(contract_info) if contract_info else "",
                archive_date or "",
                _json(structured["structured_info"]),
                structured["asset_sub_type"],
                structured["expire_date"],
                structured["cert_no"],
                structured["holder_name"],
                _json(structured.get("dimension_tags") or {}),
                item_id,
                asset_id,
            ),
        )
        conn.commit()
    return {
        "asset_id": asset_id,
        "item_id": item_id,
        "status": "updated",
        "asset_sub_type": structured["asset_sub_type"],
        "confidence": round(float(structured["confidence"]), 4),
    }


def _reextract_rows(rows: list[Any], *, only_missing: bool = True) -> dict[str, Any]:
    result = {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0, "details": []}
    updated_item_ids: set[int] = set()
    for row in rows:
        result["scanned"] += 1
        try:
            detail = _reextract_one_asset(row, only_missing=only_missing)
        except Exception as exc:
            detail = {"asset_id": int(row["asset_id"]), "item_id": int(row["item_id"]), "status": "failed", "reason": str(exc)[:240]}
        result["details"].append(detail)
        result[detail["status"]] += 1
        if detail["status"] == "updated":
            updated_item_ids.add(int(detail["item_id"]))
    for item_id in sorted(updated_item_ids):
        try:
            _refresh_asset_annotation_chunks(item_id)
        except Exception as exc:
            result["details"].append({"item_id": item_id, "status": "failed", "reason": f"vector_refresh_failed: {str(exc)[:200]}"})
            result["failed"] += 1
    return result


async def reextract_item_assets(
    item_id: int,
    *,
    asset_ids: list[int] | None = None,
    asset_sub_type: str | None = None,
    only_missing: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    ensure_schema()
    with storage.connect() as conn:
        item = conn.execute("SELECT item_id FROM knowledge_item WHERE item_id = ?", (item_id,)).fetchone()
        if item is None:
            raise KnowledgeModuleError(40411, "知识库文档不存在", 404)
        clauses = ["a.item_id = ?", "a.asset_type = '图片'"]
        params: list[Any] = [item_id]
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            clauses.append(f"a.asset_id IN ({placeholders})")
            params.extend(asset_ids)
        if asset_sub_type:
            clauses.append("COALESCE(a.asset_sub_type, '') = ?")
            params.append(asset_sub_type.strip())
        rows = conn.execute(
            f"""
            SELECT a.*, i.content_text, i.source_name
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.asset_id
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 2000))),
        ).fetchall()
    return _reextract_rows(list(rows), only_missing=only_missing)


async def reextract_assets_batch(
    *,
    item_ids: list[int] | None = None,
    asset_ids: list[int] | None = None,
    asset_sub_type: str | None = None,
    only_missing: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    ensure_schema()
    if not item_ids and not asset_ids and not asset_sub_type:
        raise KnowledgeModuleError(40031, "批量重提取至少需要提供 item_ids、asset_ids 或 asset_sub_type", 400)
    clauses = ["a.asset_type = '图片'"]
    params: list[Any] = []
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        clauses.append(f"a.item_id IN ({placeholders})")
        params.extend(item_ids)
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        clauses.append(f"a.asset_id IN ({placeholders})")
        params.extend(asset_ids)
    if asset_sub_type:
        clauses.append("COALESCE(a.asset_sub_type, '') = ?")
        params.append(asset_sub_type.strip())
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*, i.content_text, i.source_name
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.asset_id
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 2000))),
        ).fetchall()
    return _reextract_rows(list(rows), only_missing=only_missing)


async def reidentify_personnel_assets(*, limit: int = 2000) -> dict[str, Any]:
    """Re-scan legacy unclassified images for identity/health certificates."""
    ensure_schema()
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT a.*, i.content_text, i.source_name
            FROM knowledge_asset a
            JOIN knowledge_item i ON i.item_id = a.item_id
            WHERE a.asset_type = '图片'
              AND COALESCE(a.asset_sub_type, '') IN (?, '')
            ORDER BY a.asset_id
            LIMIT ?
            """,
            (ASSET_SUBTYPE_OTHER, max(1, min(int(limit), 10000))),
        ).fetchall()
    result = _reextract_rows(list(rows), only_missing=False)
    details = [item for item in result.get("details", []) if item.get("status") == "updated"]
    recognized = [item for item in details if item.get("asset_sub_type") in PERSONNEL_SCENARIO_TAGS]
    result.update({
        "recognized_count": len(recognized),
        "category_corrected_count": len(recognized),
        "structured_completed_count": sum(1 for item in recognized if item.get("asset_sub_type")),
        "scope": "legacy_other_attachments",
    })
    return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except Exception:
            return None


def _normalize_ai_image_category(value: str) -> str:
    if value in IMAGE_SCENE_CATEGORIES:
        return value
    for category in IMAGE_SCENE_CATEGORIES:
        if value and (value in category or category.replace("类", "") in value):
            return category
    if any(word in value for word in ["人员", "身份证", "健康证", "从业"]):
        return "人员资质类"
    if any(word in value for word in ["营业执照", "许可证", "证书", "资质", "认证"]):
        return "资质证书类"
    if any(word in value for word in ["现场", "冷库", "车辆", "配送", "分拣", "仓储"]):
        return "现场场景类"
    if any(word in value for word in ["设备", "检测", "资产"]):
        return "设备资产类"
    if any(word in value for word in ["业绩", "合同", "中标", "验收", "评价"]):
        return "业绩证明类"
    return "其他附件类"


def _compact_person_info(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    mapping = {
        "name": ["name", "姓名"],
        "gender": ["gender", "性别"],
        "id_number": ["id_number", "身份证号", "证件编号", "证号"],
        "position": ["position", "职位", "岗位", "职务"],
        "valid_until": ["valid_until", "有效期", "有效至", "截止日期"],
    }
    result: dict[str, str] = {}
    for key, aliases in mapping.items():
        for alias in aliases:
            raw = value.get(alias)
            if raw:
                result[key] = str(raw).strip()[:80]
                break
    return result or None


async def _semantic_confirm_assets(item_id: int, document_text: str) -> None:
    if not qwen_client.has_generation_api_key('knowledge_analysis'):
        return
    assets = _assets_for_item(item_id, limit=60)
    if not assets:
        return
    compact_assets = [
        {
            "asset_id": asset["asset_id"],
            "category": asset.get("category") or "其他附件类",
            "tags": asset.get("tags") or [],
            "description": asset.get("ai_description") or "",
            "annotation": asset.get("annotation") or "",
            "ocr_text": asset.get("ocr_text") or "",
            "person_info": asset.get("person_info") or {},
            "employee_info": asset.get("employee_info") or {},
            "employee_role": asset.get("employee_role") or "",
            "contract_info": asset.get("contract_info") or {},
            "archive_date": asset.get("archive_date") or "",
            "page_num": asset.get("page_num"),
            "source_chapter": asset.get("source_chapter"),
        }
        for asset in assets
    ]
    messages = [
        {"role": "system", "content": "你是食材配送标书知识库的图片资料分类助手。只输出JSON，不要解释。"},
        {
            "role": "user",
            "content": (
                "请基于文档上下文、图片已有描述和初始注释，校正确认每张图片分类，并生成正式标书图片语义注释。"
                "分类只能从以下枚举选择：资质证书类、现场场景类、人员资质类、业绩证明类、设备资产类、其他附件类。"
                "如果图片是身份证、驾驶证、健康证、资格证、社保证明或人员照片，请提取 employee_info 并按项目经理、技术人员、卡车司机、配送司机、食品安全员、检测人员、仓储分拣人员、客服人员、后勤人员、其他人员归类 employee_role。"
                "如果图片是合同、中标通知书、验收报告、客户评价等业绩资料，请提取 contract_info，并按签订/中标/验收日期给出 archive_date，格式 YYYY-MM-DD。"
                "annotation 必须包含三部分：图片核心主体、关键信息提取、业务说明，语言正式专业，可直接作为标书图注文案。"
                "返回格式：{\"assets\":[{\"asset_id\":1,\"category\":\"资质证书类\",\"tags\":[\"营业执照\"],\"description\":\"企业营业执照正本，包含统一社会信用代码、经营范围\",\"annotation\":\"图片核心主体：...\\n关键信息提取：...\\n业务说明：...\",\"employee_info\":{},\"employee_role\":\"\",\"contract_info\":{},\"archive_date\":\"\"}]}。"
                f"\n文档上下文：{document_text[:5000]}"
                f"\n图片列表：{_json(compact_assets)}"
            ),
        },
    ]
    result = await qwen_client.chat_completion(messages, scene="knowledge_analysis", temperature=0.05, max_tokens=2400, fallback="", json_mode=True)
    if not result.usedApi or not result.content.strip():
        return
    try:
        payload = json.loads(result.content)
    except Exception:
        return
    updates = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(updates, list):
        return
    now = _now()
    with storage.connect() as conn:
        for update in updates:
            if not isinstance(update, dict):
                continue
            asset_id = int(update.get("asset_id") or 0)
            category = _normalize_ai_image_category(str(update.get("category") or "").strip())
            if asset_id <= 0 or category not in IMAGE_SCENE_CATEGORIES:
                continue
            tags = _parse_tags(update.get("tags") if isinstance(update.get("tags"), list) else str(update.get("tags") or ""))
            description = str(update.get("description") or "").strip()[:500]
            annotation = str(update.get("annotation") or "").strip()[:2400]
            ocr_text = str(update.get("ocr_text") or "").strip()[:8000]
            person_info = _compact_person_info(update.get("person_info") if isinstance(update.get("person_info"), dict) else None)
            employee_info = _compact_employee_info(update.get("employee_info") if isinstance(update.get("employee_info"), dict) else None)
            employee_role = _normalize_employee_role(str(update.get("employee_role") or (employee_info or {}).get("role") or ""))
            if employee_info and employee_role:
                employee_info = {**employee_info, "role": employee_role}
            contract_info = _compact_contract_info(update.get("contract_info") if isinstance(update.get("contract_info"), dict) else None)
            archive_date = _normalize_date(str(update.get("archive_date") or "")) or _archive_date_for_asset(contract_info, ocr_text, annotation)
            conn.execute(
                "UPDATE knowledge_asset SET category = ?, tags_json = ?, ai_description = COALESCE(NULLIF(?, ''), ai_description), annotation = COALESCE(NULLIF(?, ''), annotation), ocr_text = COALESCE(NULLIF(?, ''), ocr_text), person_info = COALESCE(NULLIF(?, ''), person_info), employee_info = COALESCE(NULLIF(?, ''), employee_info), employee_role = COALESCE(NULLIF(?, ''), employee_role), contract_info = COALESCE(NULLIF(?, ''), contract_info), archive_date = COALESCE(NULLIF(?, ''), archive_date) WHERE item_id = ? AND asset_id = ?",
                (
                    category,
                    _json(tags or [category.replace("类", "")]),
                    description,
                    annotation,
                    ocr_text,
                    _json(person_info) if person_info else "",
                    _json(employee_info) if employee_info else "",
                    employee_role,
                    _json(contract_info) if contract_info else "",
                    archive_date or "",
                    item_id,
                    asset_id,
                ),
            )
        conn.commit()
    _refresh_asset_annotation_chunks(item_id)


def _asset_count(item_id: int) -> int:
    with storage.connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS count FROM knowledge_asset WHERE item_id = ?", (item_id,)).fetchone()
    return int(row["count"] or 0)


def _clear_assets_for_item(item_id: int) -> None:
    with storage.connect() as conn:
        conn.execute("DELETE FROM knowledge_asset WHERE item_id = ?", (item_id,))
        conn.commit()


def _asset_category_stats(item_id: int) -> dict[str, int]:
    with storage.connect() as conn:
        rows = conn.execute("SELECT COALESCE(category, '其他附件类') AS category, COUNT(1) AS count FROM knowledge_asset WHERE item_id = ? GROUP BY COALESCE(category, '其他附件类')", (item_id,)).fetchall()
    return {row["category"]: int(row["count"] or 0) for row in rows}


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_chunks(
    text: str,
    pages: list[dict[str, Any]],
    *,
    sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not text.strip():
        return [{"section_title": "文档内容", "page_number": None, "content": "暂无可提取文本，已保留原始文件和图片资源。"}]
    if sections:
        structured_chunks: list[dict[str, Any]] = []
        for section in sections:
            title = str(section.get("section_title") or "文档内容").strip()[:240]
            content = str(section.get("content") or "").strip()
            if not content:
                continue
            # Keep the chapter path inside every chunk so both keyword and
            # dense retrieval retain the tender/bid hierarchy as context.
            prefix = f"章节：{title}\n"
            body_size = max(240, 1000 - len(prefix))
            for part in _window_text(content, body_size, 120):
                structured_chunks.append({
                    "section_title": title,
                    "section_path": title,
                    "page_number": None,
                    "chunk_kind": "document_text",
                    "content": f"{prefix}{part}",
                })
        if structured_chunks:
            return structured_chunks
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunks: list[dict[str, Any]] = []
    current_title = "文档内容"
    buffer: list[str] = []
    page_number: int | None = None

    def flush() -> None:
        nonlocal buffer, page_number
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        for part in _window_text(content, 1000, 120):
            chunks.append({"section_title": current_title, "section_path": current_title, "page_number": page_number, "chunk_kind": "document_text", "content": part})
        buffer = []

    for line in lines:
        page_match = re.match(r"^【第(\d+)页】", line)
        if page_match:
            # Commit the previous page before changing its source location.
            flush()
            page_number = int(page_match.group(1))
            remainder = line[page_match.end():].strip()
            if remainder:
                if _looks_like_heading(remainder):
                    current_title = remainder[:80]
                else:
                    buffer.append(remainder)
            continue
        if _looks_like_heading(line):
            flush()
            current_title = line[:80]
        else:
            buffer.append(line)
    flush()
    return chunks


def _looks_like_heading(line: str) -> bool:
    if len(line) > 40:
        return False
    return bool(re.match(r"^(第[一二三四五六七八九十百0-9]+[章节部分]|[一二三四五六七八九十]+[、.]|\d+(?:\.\d+){0,3}\s+)", line))


def _window_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    result: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        result.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return result


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", "", text.lower())
    tokens = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,8}", text.lower())
    for i in range(max(0, len(cleaned) - 1)):
        pair = cleaned[i : i + 2]
        if re.match(r"^[\u4e00-\u9fff]{2}$", pair):
            tokens.append(pair)
    return list(dict.fromkeys(tokens))[:800]


def _hash_embedding(tokens: list[str]) -> list[float]:
    vec = [0.0] * VECTOR_DIM
    for token, count in Counter(tokens).items():
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % VECTOR_DIM
        vec[idx] += 1.0 + math.log(count)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _keyword_score(query_tokens: list[str], chunk_tokens: list[str], content: str) -> float:
    if not query_tokens:
        return 0.0
    token_set = set(chunk_tokens)
    overlap = sum(1 for token in query_tokens if token in token_set or token in content)
    return overlap / max(1, len(query_tokens))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    length = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(length))


def _rrf_merge(keyword_rank: list[tuple[int, float]], vector_rank: list[tuple[int, float]], limit: int) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    raw_scores: dict[int, float] = defaultdict(float)
    rank_constant = max(1, min(int(settings.rag_rrf_constant or 60), 500))
    for rank, (chunk_id, score) in enumerate(keyword_rank, start=1):
        scores[chunk_id] += 1 / (rank_constant + rank)
        raw_scores[chunk_id] += score
    for rank, (chunk_id, score) in enumerate(vector_rank, start=1):
        scores[chunk_id] += 1 / (rank_constant + rank)
        raw_scores[chunk_id] += score
    merged = sorted(scores.items(), key=lambda item: (item[1], raw_scores[item[0]]), reverse=True)[:limit]
    if not merged:
        return []
    max_score = max(score for _, score in merged) or 1.0
    return [(chunk_id, score / max_score) for chunk_id, score in merged]


def _assets_for_item(item_id: int, limit: int = 8, image_category: str | None = None, bid_type: str | None = None, bid_scenario: str | None = None, length_level: str | None = None, asset_sub_type: list[str] | None = None, priority_expire: bool = False, exclude_ids: set[int] | None = None, chapter_theme: str | None = None, asset_ids: list[int] | None = None) -> list[dict[str, Any]]:
    clauses = ["item_id = ?"]
    params: list[Any] = [item_id]
    exclude_ids = exclude_ids or set()
    if asset_ids is not None:
        normalized_asset_ids = sorted({int(asset_id) for asset_id in asset_ids if int(asset_id) > 0})
        if not normalized_asset_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_asset_ids)
        clauses.append(f"asset_id IN ({placeholders})")
        params.extend(normalized_asset_ids)
    if image_category:
        clauses.append("category = ?")
        params.append(image_category)
    if bid_type:
        clauses.append("COALESCE(bid_type, '') = ?")
        params.append(bid_type)
    if bid_scenario:
        clauses.append("COALESCE(bid_scenario, '') = ?")
        params.append(bid_scenario)
    if length_level:
        clauses.append("COALESCE(length_level, '') = ?")
        params.append(length_level)
    if asset_sub_type:
        subtype_values = [item.strip() for item in asset_sub_type if item and item.strip()]
        if subtype_values:
            placeholders = ",".join("?" for _ in subtype_values)
            clauses.append(f"COALESCE(asset_sub_type, '') IN ({placeholders})")
            params.extend(subtype_values)
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        clauses.append(f"asset_id NOT IN ({placeholders})")
        params.extend(sorted(exclude_ids))
    if priority_expire:
        order_sql = "CASE WHEN COALESCE(expire_date, '') = '' THEN 1 WHEN substr(expire_date, 1, 10) >= ? THEN 0 ELSE 2 END, asset_id DESC"
        params.append(datetime.now(timezone.utc).date().isoformat())
    else:
        order_sql = "asset_id DESC"
    params.append(max(1, min(limit, 2000)))
    with storage.connect() as conn:
        rows = conn.execute(f"SELECT * FROM knowledge_asset WHERE {' AND '.join(clauses)} ORDER BY {order_sql} LIMIT ?", params).fetchall()
    return [_row_asset(row) for row in rows]


def _refresh_expired_flags() -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    with storage.connect() as conn:
        conn.execute("UPDATE knowledge_item SET is_expired = 1 WHERE valid_until IS NOT NULL AND valid_until <> '' AND substr(valid_until, 1, 10) < ?", (today,))
        conn.commit()


def _is_expired(valid_until: str | None) -> int:
    if not valid_until:
        return 0
    try:
        return 1 if valid_until[:10] < datetime.now(timezone.utc).date().isoformat() else 0
    except Exception:
        return 0


def _build_graph_for_item(item_id: int, text: str, category_id: int | None) -> None:
    item = get_item(item_id, include_chunks=False)
    candidates = _extract_entities(item["title"], text)
    now = _now()
    with storage.connect() as conn:
        root_id = _upsert_entity(conn, item["title"], "知识条目", category_id, item_id, now)
        for name, entity_type, relation in candidates[:40]:
            entity_id = _upsert_entity(conn, name, entity_type, category_id, item_id, now)
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_relation (source_entity_id, target_entity_id, relation_type, item_id, create_time) VALUES (?, ?, ?, ?, ?)",
                (root_id, entity_id, relation, item_id, now),
            )
        conn.commit()


def _build_import_project_relation(item_id: int, title: str, project_name: str, category_id: int | None) -> None:
    project_name = project_name.strip()
    if not project_name or project_name in {"批量导入", "招标文件", "方案案例", "未分类"}:
        return
    now = _now()
    with storage.connect() as conn:
        item_entity_id = _upsert_entity(conn, title, "知识条目", category_id, item_id, now)
        project_entity_id = _upsert_entity(conn, project_name[:60], "项目名称", category_id, item_id, now)
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_relation (source_entity_id, target_entity_id, relation_type, item_id, create_time) VALUES (?, ?, ?, ?, ?)",
            (item_entity_id, project_entity_id, "属于", item_id, now),
        )
        conn.commit()


def _upsert_entity(conn: Any, name: str, entity_type: str, category_id: int | None, item_id: int, now: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_entity (name, entity_type, category_id, item_id, create_time) VALUES (?, ?, ?, ?, ?)",
        (name[:80], entity_type, category_id, item_id, now),
    )
    row = conn.execute("SELECT entity_id FROM knowledge_entity WHERE name = ? AND entity_type = ? AND item_id = ?", (name[:80], entity_type, item_id)).fetchone()
    return int(row["entity_id"])


def _extract_entities(title: str, text: str) -> list[tuple[str, str, str]]:
    source = f"{title}\n{text[:8000]}"
    results: list[tuple[str, str, str]] = []
    patterns = [
        ("资质名称", r"[\u4e00-\u9fffA-Za-z0-9]{2,24}(?:证书|资质|许可证|营业执照|认证|检测报告)", "证明"),
        ("项目名称", r"[\u4e00-\u9fffA-Za-z0-9]{2,36}(?:项目|工程|服务)", "适用"),
        ("设备名称", r"[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:设备|车辆|冷库|仪器|系统)", "包含"),
        ("人员", r"[\u4e00-\u9fff]{2,8}(?:经理|负责人|工程师|专员|人员)", "属于"),
    ]
    seen: set[tuple[str, str]] = set()
    for entity_type, pattern, relation in patterns:
        for match in re.findall(pattern, source):
            name = str(match).strip(" ，。；：、（）()")
            if len(name) < 2 or (name, entity_type) in seen:
                continue
            seen.add((name, entity_type))
            results.append((name, entity_type, relation))
    return results
