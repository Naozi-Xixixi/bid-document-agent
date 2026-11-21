from __future__ import annotations

import mimetypes
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import settings
from app.schemas.tender import BidSettings, BridgeSettings, ProjectSettingsUpdateRequest, ProjectState, UploadedFileInfo


_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED_PATHS: set[str] = set()
_MYSQL_INITIALIZED_DATABASES: set[str] = set()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def read_bridge_settings() -> BridgeSettings:
    qwen_model = settings.qwen_generation_model or "qwen3.7-flash"
    legacy_provider = "deep" + "seek"
    if not settings.settings_file.exists():
        return BridgeSettings(
            providers=[
                {
                    "name": "qwen",
                    "baseUrl": settings.qwen_base_url,
                    "apiType": "openai",
                    "models": [
                        {"id": qwen_model, "name": "通义千问文本模型", "provider": "qwen"},
                    ],
                }
            ],
            defaultModel=qwen_model,
            defaultProvider="qwen",
        )
    current = BridgeSettings.model_validate_json(settings.settings_file.read_text(encoding="utf-8"))
    if current.defaultProvider == legacy_provider or current.defaultModel.startswith(legacy_provider):
        current.defaultProvider = "qwen"
        current.defaultModel = qwen_model
    for provider in current.providers:
        if provider.name == legacy_provider:
            provider.name = "qwen"
            provider.baseUrl = settings.qwen_base_url
            provider.models = [{"id": qwen_model, "name": "通义千问文本模型", "provider": "qwen"}]
    if not current.providers:
        current.providers = [
            {
                "name": "qwen",
                "baseUrl": settings.qwen_base_url,
                "apiType": "openai",
                "models": [{"id": qwen_model, "name": "通义千问文本模型", "provider": "qwen"}],
            }
        ]
    return BridgeSettings.model_validate(current.model_dump())


def write_bridge_settings(payload: BridgeSettings) -> BridgeSettings:
    settings.settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.settings_file.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    return payload


def connect() -> sqlite3.Connection:
    if (settings.db_backend or "sqlite").strip().lower() == "mysql":
        from app.services.mysql_compat import MySQLConnection, database_name_for_company

        from app.core.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        company_id = tenant.company_id if tenant is not None else "default"
        database_name = database_name_for_company(company_id)
        conn = MySQLConnection(database_name)
        if database_name not in _MYSQL_INITIALIZED_DATABASES:
            with _DB_INIT_LOCK:
                if database_name not in _MYSQL_INITIALIZED_DATABASES:
                    init_db(conn)
                    _MYSQL_INITIALIZED_DATABASES.add(database_name)
        return conn  # type: ignore[return-value]
    database_file = settings.database_file.resolve()
    database_key = str(database_file)
    conn = sqlite3.connect(database_file, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # If another worker is initializing the database, busy_timeout will keep
        # normal reads/writes waiting; WAL can be applied on the next connection.
        pass
    if database_key not in _DB_INITIALIZED_PATHS:
        with _DB_INIT_LOCK:
            if database_key not in _DB_INITIALIZED_PATHS:
                init_db(conn)
                _DB_INITIALIZED_PATHS.add(database_key)
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    db = conn or sqlite3.connect(settings.database_file, timeout=30.0)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            db.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                bid_id INTEGER UNIQUE,
                bid_title TEXT NOT NULL DEFAULT '',
                page_count INTEGER NOT NULL DEFAULT 200,
                words_per_page INTEGER NOT NULL DEFAULT 800,
                estimated_word_count INTEGER NOT NULL DEFAULT 160000,
                total_target_word_count INTEGER,
                bid_type TEXT NOT NULL DEFAULT 'open',
                selected_model TEXT NOT NULL DEFAULT 'qwen3.7-flash',
                selected_provider TEXT NOT NULL DEFAULT 'qwen',
                current_step INTEGER NOT NULL DEFAULT 1,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS project_deletions (
                project_id TEXT PRIMARY KEY,
                bid_id INTEGER,
                deleted_at TEXT NOT NULL,
                cleanup_path TEXT,
                removed_count INTEGER NOT NULL DEFAULT 0,
                preserved_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(projects)").fetchall()}
        if "bid_id" not in existing_columns:
            db.execute("ALTER TABLE projects ADD COLUMN bid_id INTEGER")
        if "words_per_page" not in existing_columns:
            db.execute("ALTER TABLE projects ADD COLUMN words_per_page INTEGER NOT NULL DEFAULT 800")
        if "estimated_word_count" not in existing_columns:
            db.execute("ALTER TABLE projects ADD COLUMN estimated_word_count INTEGER NOT NULL DEFAULT 160000")
        if "current_step" not in existing_columns:
            db.execute("ALTER TABLE projects ADD COLUMN current_step INTEGER NOT NULL DEFAULT 1")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bids (
                bid_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT UNIQUE NOT NULL,
                bid_title TEXT NOT NULL DEFAULT '',
                page_count INTEGER NOT NULL DEFAULT 200,
                words_per_page INTEGER NOT NULL DEFAULT 800,
                estimated_word_count INTEGER NOT NULL DEFAULT 160000,
                bid_type TEXT NOT NULL DEFAULT 'open',
                selected_model TEXT NOT NULL DEFAULT 'qwen3.7-flash',
                selected_provider TEXT NOT NULL DEFAULT 'qwen',
                current_step INTEGER NOT NULL DEFAULT 1,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        bid_columns = {row[1] for row in db.execute("PRAGMA table_info(bids)").fetchall()}
        if "current_step" not in bid_columns:
            db.execute("ALTER TABLE bids ADD COLUMN current_step INTEGER NOT NULL DEFAULT 1")
        if "total_target_word_count" not in bid_columns:
            db.execute("ALTER TABLE bids ADD COLUMN total_target_word_count INTEGER")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_parse_info (
                parse_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL UNIQUE,
                parse_mode TEXT CHECK(parse_mode IN ('auto_upload', 'paste_text')),
                tender_file_path TEXT,
                tender_file_name TEXT,
                tender_file_size INTEGER,
                paste_content TEXT,
                bid_category TEXT NOT NULL DEFAULT 'service' CHECK(bid_category IN ('service', 'goods', 'engineering', 'other')),
                parsed_project_overview TEXT,
                parsed_score_requirements TEXT,
                parsed_tech_scoring TEXT,
                parsed_biz_requirements TEXT,
                parsed_invalid_clauses TEXT,
                raw_full_text TEXT,
                raw_markdown TEXT,
                full_markdown TEXT,
                chapter_blocks TEXT,
                project_basic_info TEXT,
                tech_scoring_items TEXT,
                biz_scoring_items TEXT,
                qualification_requirements TEXT,
                invalid_bid_clauses TEXT,
                bid_key_points TEXT,
                parse_current_step INTEGER,
                parse_status TEXT NOT NULL DEFAULT 'pending' CHECK(parse_status IN ('pending', 'processing', 'completed', 'failed')),
                parse_error TEXT,
                parse_error_msg TEXT,
                parse_warning_msg TEXT,
                extraction_progress TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_case_reference (
                bid_id INTEGER PRIMARY KEY,
                knowledge_project_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        parse_columns = {row[1] for row in db.execute("PRAGMA table_info(bid_parse_info)").fetchall()}
        if "raw_full_text" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN raw_full_text TEXT")
        if "raw_markdown" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN raw_markdown TEXT")
        if "full_markdown" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN full_markdown TEXT")
        if "chapter_blocks" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN chapter_blocks TEXT")
        if "project_basic_info" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN project_basic_info TEXT")
        if "tech_scoring_items" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN tech_scoring_items TEXT")
        if "biz_scoring_items" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN biz_scoring_items TEXT")
        if "qualification_requirements" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN qualification_requirements TEXT")
        if "invalid_bid_clauses" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN invalid_bid_clauses TEXT")
        if "bid_key_points" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN bid_key_points TEXT")
        if "parsed_tech_scoring" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parsed_tech_scoring TEXT")
        if "parsed_biz_requirements" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parsed_biz_requirements TEXT")
        if "parsed_invalid_clauses" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parsed_invalid_clauses TEXT")
        if "parse_current_step" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parse_current_step INTEGER")
        if "parse_error" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parse_error TEXT")
        if "parse_error_msg" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parse_error_msg TEXT")
        if "parse_warning_msg" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN parse_warning_msg TEXT")
        if "extraction_progress" not in parse_columns:
            db.execute("ALTER TABLE bid_parse_info ADD COLUMN extraction_progress TEXT")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_list_files (
                list_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                list_type TEXT NOT NULL CHECK(list_type IN ('upload', 'paste')),
                file_path TEXT,
                file_name TEXT,
                file_size INTEGER,
                paste_content TEXT,
                create_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_reference_materials (
                ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('template', 'writing_data')),
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER,
                raw_text TEXT,
                full_markdown TEXT,
                chunks_json TEXT,
                company_profile TEXT,
                style_profile TEXT,
                parse_status TEXT NOT NULL DEFAULT 'pending',
                parse_error TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        reference_columns = {row[1] for row in db.execute("PRAGMA table_info(bid_reference_materials)").fetchall()}
        for column_name, definition in {
            "raw_text": "TEXT",
            "full_markdown": "TEXT",
            "chunks_json": "TEXT",
            "company_profile": "TEXT",
            "style_profile": "TEXT",
            "parse_status": "TEXT NOT NULL DEFAULT 'pending'",
            "parse_error": "TEXT",
            "update_time": "TEXT",
        }.items():
            if column_name not in reference_columns:
                db.execute(f"ALTER TABLE bid_reference_materials ADD COLUMN {column_name} {definition}")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_catalog_versions (
                version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                version_name TEXT NOT NULL,
                version_style TEXT NOT NULL,
                version_type TEXT NOT NULL DEFAULT 'standard',
                is_final INTEGER NOT NULL DEFAULT 0,
                catalog_content TEXT NOT NULL,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                UNIQUE(bid_id, version_name),
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        catalog_version_columns = {row[1] for row in db.execute("PRAGMA table_info(bid_catalog_versions)").fetchall()}
        if "version_type" not in catalog_version_columns:
            db.execute("ALTER TABLE bid_catalog_versions ADD COLUMN version_type TEXT NOT NULL DEFAULT 'standard'")
        if "is_final" not in catalog_version_columns:
            db.execute("ALTER TABLE bid_catalog_versions ADD COLUMN is_final INTEGER NOT NULL DEFAULT 0")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_catalog_chapters (
                chapter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                parent_id INTEGER NOT NULL DEFAULT 0,
                chapter_title TEXT NOT NULL,
                level INTEGER NOT NULL CHECK(level IN (1, 2, 3)),
                sort_order INTEGER NOT NULL,
                target_word_count INTEGER,
                target_word_count_manual INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE,
                FOREIGN KEY(version_id) REFERENCES bid_catalog_versions(version_id) ON DELETE CASCADE
            )
            """
        )
        catalog_chapter_columns = {row[1] for row in db.execute("PRAGMA table_info(bid_catalog_chapters)").fetchall()}
        if "target_word_count" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN target_word_count INTEGER")
        if "target_word_count_manual" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN target_word_count_manual INTEGER NOT NULL DEFAULT 0")
        if "graphic_markdown" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN graphic_markdown TEXT")
        if "ref_chart_ids" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN ref_chart_ids TEXT NOT NULL DEFAULT '[]'")
        if "ref_image_asset_ids" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN ref_image_asset_ids TEXT NOT NULL DEFAULT '[]'")
        if "graphic_format_template_id" not in catalog_chapter_columns:
            db.execute("ALTER TABLE bid_catalog_chapters ADD COLUMN graphic_format_template_id TEXT")

        # Collaboration revisions are local to each company's database.
        for name, definition in {
            "edit_revision": "INTEGER NOT NULL DEFAULT 0",
            "assigned_user_id": "INTEGER",
            "reviewer_user_id": "INTEGER",
            "review_state": "TEXT NOT NULL DEFAULT 'draft'",
            "submitted_by": "INTEGER",
        }.items():
            if name not in catalog_chapter_columns:
                db.execute(f"ALTER TABLE bid_catalog_chapters ADD COLUMN {name} {definition}")
        db.execute("""CREATE TABLE IF NOT EXISTS chapter_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bid_id INTEGER NOT NULL, chapter_id INTEGER NOT NULL, revision INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, actor_id INTEGER, actor_name TEXT NOT NULL,
            action TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(chapter_id, revision),
            FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE CASCADE
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS chapter_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bid_id INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
            revision INTEGER NOT NULL, actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
            body TEXT NOT NULL, quote TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
            resolved_by TEXT, resolved_at TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE CASCADE
        )""")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_mermaid_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                chart_title TEXT NOT NULL,
                chart_type TEXT NOT NULL,
                mermaid_code TEXT NOT NULL,
                chart_image_data TEXT,
                source_text TEXT,
                saved_to_rag INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        mermaid_columns = {row[1] for row in db.execute("PRAGMA table_info(bid_mermaid_assets)").fetchall()}
        if "source_text" not in mermaid_columns:
            db.execute("ALTER TABLE bid_mermaid_assets ADD COLUMN source_text TEXT")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_chapter_contents (
                content_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                content_text TEXT,
                content_html TEXT,
                word_count INTEGER,
                status TEXT NOT NULL DEFAULT 'unwritten' CHECK(status IN ('unwritten', 'generating', 'completed', 'failed')),
                style_config TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                UNIQUE(bid_id, version_id, chapter_id),
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE,
                FOREIGN KEY(version_id) REFERENCES bid_catalog_versions(version_id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE CASCADE
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_chapter_material_selections (
                selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                selected_asset_ids TEXT NOT NULL DEFAULT '[]',
                selected_chunk_ids TEXT NOT NULL DEFAULT '[]',
                bid_scenario TEXT,
                length_level TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                UNIQUE(bid_id, chapter_id),
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE CASCADE
            )
            """
        )

        db.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_bid_id ON projects(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bids_project_id ON bids(project_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_list_files_bid_id ON bid_list_files(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_reference_materials_bid_id ON bid_reference_materials(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_catalog_versions_bid_id ON bid_catalog_versions(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_catalog_chapters_version_id ON bid_catalog_chapters(version_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_chapter_contents_bid_id ON bid_chapter_contents(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_chapter_contents_chapter_id ON bid_chapter_contents(chapter_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_mermaid_assets_bid_id ON bid_mermaid_assets(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_chapter_material_selections_bid_id ON bid_chapter_material_selections(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_chapter_material_selections_chapter_id ON bid_chapter_material_selections(chapter_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_key TEXT NOT NULL UNIQUE,
                case_name TEXT NOT NULL,
                project_type TEXT,
                tender_summary TEXT,
                proposal_summary TEXT,
                company_profile TEXT,
                style_profile TEXT,
                source_folder TEXT,
                ingest_status TEXT NOT NULL DEFAULT 'pending' CHECK(ingest_status IN ('pending', 'processing', 'completed', 'failed')),
                ingest_error TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_case_documents (
                document_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                doc_role TEXT NOT NULL CHECK(doc_role IN ('tender', 'proposal')),
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                raw_text TEXT,
                text_chars INTEGER NOT NULL DEFAULT 0,
                parse_status TEXT NOT NULL DEFAULT 'pending' CHECK(parse_status IN ('pending', 'completed', 'failed')),
                parse_error TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                UNIQUE(case_id, file_path),
                FOREIGN KEY(case_id) REFERENCES rag_cases(case_id) ON DELETE CASCADE
            )
            """
        )
        # File metadata lets the case indexer skip unchanged PDFs.  Keep this
        # migration additive so databases created by older versions continue
        # to work without a rebuild.
        document_columns = {row[1] for row in db.execute("PRAGMA table_info(rag_case_documents)").fetchall()}
        if "file_mtime" not in document_columns:
            db.execute("ALTER TABLE rag_case_documents ADD COLUMN file_mtime REAL NOT NULL DEFAULT 0")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_case_chunks (
                chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                document_id INTEGER NOT NULL,
                doc_role TEXT NOT NULL CHECK(doc_role IN ('tender', 'proposal')),
                chunk_kind TEXT NOT NULL DEFAULT 'general',
                chunk_title TEXT,
                content TEXT NOT NULL,
                tokens_json TEXT NOT NULL DEFAULT '[]',
                char_count INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES rag_cases(case_id) ON DELETE CASCADE,
                FOREIGN KEY(document_id) REFERENCES rag_case_documents(document_id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_scoring_mappings (
                mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                tender_chunk_id INTEGER NOT NULL,
                proposal_chunk_id INTEGER NOT NULL,
                match_score REAL NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                UNIQUE(tender_chunk_id, proposal_chunk_id),
                FOREIGN KEY(case_id) REFERENCES rag_cases(case_id) ON DELETE CASCADE,
                FOREIGN KEY(tender_chunk_id) REFERENCES rag_case_chunks(chunk_id) ON DELETE CASCADE,
                FOREIGN KEY(proposal_chunk_id) REFERENCES rag_case_chunks(chunk_id) ON DELETE CASCADE
            )
            """
        )
        # Persist one clean text record per PDF page.  Similarity lookup can
        # then locate evidence pages from SQLite instead of reopening a large
        # PDF for every requirement.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_case_pages (
                page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tokens_json TEXT NOT NULL DEFAULT '[]',
                create_time TEXT NOT NULL,
                UNIQUE(document_id, page_number),
                FOREIGN KEY(document_id) REFERENCES rag_case_documents(document_id) ON DELETE CASCADE
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_rag_cases_case_key ON rag_cases(case_key)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rag_case_documents_case_id ON rag_case_documents(case_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rag_case_chunks_case_role ON rag_case_chunks(case_id, doc_role)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rag_case_chunks_kind ON rag_case_chunks(chunk_kind)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rag_case_pages_document_id ON rag_case_pages(document_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_analysis_tasks (
                task_id TEXT PRIMARY KEY,
                bid_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing' CHECK(status IN ('processing', 'completed', 'failed')),
                check_items TEXT NOT NULL,
                error_message TEXT,
                report_markdown TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_analysis_result (
                result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                bid_id INTEGER NOT NULL,
                check_type TEXT NOT NULL,
                risk_level TEXT NOT NULL CHECK(risk_level IN ('high', 'medium', 'suggestion')),
                risk_title TEXT NOT NULL,
                source_text TEXT,
                problem_text TEXT NOT NULL,
                suggestion_text TEXT NOT NULL,
                chapter_id INTEGER,
                chapter_title TEXT,
                resolved INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES bid_analysis_tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE SET NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_analysis_tasks_bid_id ON bid_analysis_tasks(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_analysis_result_bid_id ON bid_analysis_result(bid_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bid_analysis_result_task_id ON bid_analysis_result(task_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_analysis_documents (
                bid_id INTEGER PRIMARY KEY,
                tender_file_name TEXT NOT NULL,
                tender_file_path TEXT NOT NULL,
                tender_text TEXT NOT NULL,
                tender_markdown TEXT NOT NULL,
                bid_file_name TEXT NOT NULL,
                bid_file_path TEXT NOT NULL,
                bid_text TEXT NOT NULL,
                bid_markdown TEXT NOT NULL,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'proposal',
                industry TEXT,
                region TEXT,
                year INTEGER,
                project_type TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                valid_until TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'expired')),
                source_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                content_text TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL
            )
            """
        )
        knowledge_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_items)").fetchall()}
        for column_name, definition in {
            "project_type": "TEXT",
            "tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "valid_until": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "content_text": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column_name not in knowledge_columns:
                db.execute(f"ALTER TABLE knowledge_items ADD COLUMN {column_name} {definition}")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_source_refs (
                ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                bid_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                snippet TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES knowledge_items(item_id) ON DELETE CASCADE,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES bid_catalog_chapters(chapter_id) ON DELETE CASCADE
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_status ON knowledge_items(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_type ON knowledge_items(item_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_source_refs_bid ON knowledge_source_refs(bid_id)")
        _migrate_projects_to_bids(db)
        db.commit()
    finally:
        if owns_conn:
            db.close()


def _sync_bid_row_for_project(conn: sqlite3.Connection, project: ProjectState) -> int:
    """Mirror the existing first-step project row into the bids table.

    The frontend still uses project UUIDs for step 1, while step 2 is designed
    around integer bid_id values. Keeping this mapping here lets both flows use
    the same SQLite database without rewriting the first-step API.
    """

    row = conn.execute("SELECT bid_id, current_step FROM bids WHERE project_id = ?", (project.id,)).fetchone()
    current_step = int(row["current_step"]) if row else 1
    conn.execute(
        """
        INSERT INTO bids (
            project_id, bid_title, page_count, words_per_page, estimated_word_count,
            bid_type, selected_model, selected_provider, current_step, create_time, update_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            bid_title = excluded.bid_title,
            page_count = excluded.page_count,
            words_per_page = excluded.words_per_page,
            estimated_word_count = excluded.estimated_word_count,
            bid_type = excluded.bid_type,
            selected_model = excluded.selected_model,
            selected_provider = excluded.selected_provider,
            update_time = excluded.update_time
        """,
        (
            project.id,
            project.settings.bidTitle,
            project.settings.pageCount,
            project.settings.wordsPerPage,
            project.settings.estimatedWordCount,
            project.settings.bidType,
            project.settings.selectedModel,
            project.settings.selectedProvider,
            current_step,
            project.createdAt.isoformat(),
            project.updatedAt.isoformat(),
        ),
    )
    bid_id = int(conn.execute("SELECT bid_id FROM bids WHERE project_id = ?", (project.id,)).fetchone()["bid_id"])
    conn.execute("UPDATE projects SET bid_id = ?, current_step = ? WHERE id = ?", (bid_id, current_step, project.id))
    return bid_id


def _migrate_projects_to_bids(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, state_json, current_step
        FROM projects
        WHERE bid_id IS NULL OR id NOT IN (SELECT project_id FROM bids)
        """
    ).fetchall()
    for row in rows:
        project = _project_from_row(row)
        _sync_bid_row_for_project(conn, project)


def normalize_bid_settings(value: BidSettings | None) -> BidSettings:
    raw = (value or BidSettings()).model_dump()
    title = str(raw.get("bidTitle") or "").strip()
    page_count = int(raw.get("pageCount") if raw.get("pageCount") is not None else 200)
    page_count = max(0, min(page_count, 4000))
    words_per_page = int(raw.get("wordsPerPage") or 800)
    words_per_page = max(100, min(words_per_page, 2000))
    estimated_word_count = page_count * words_per_page
    bid_type = raw.get("bidType") if raw.get("bidType") in {"open", "blind"} else "open"
    return BidSettings(
        bidTitle=title,
        pageCount=page_count,
        wordsPerPage=words_per_page,
        estimatedWordCount=estimated_word_count,
        bidType=bid_type,
        selectedModel=settings.qwen_generation_model or "qwen3.7-flash",
        selectedProvider="qwen",
    )


def _upsert_project(conn: sqlite3.Connection, project: ProjectState) -> None:
    # Serialize the tombstone check with deletion, including callers holding an
    # old ProjectState across a model await. Deleted UUIDs must never be reused.
    mysql = (settings.db_backend or "sqlite").strip().lower() == "mysql"
    if mysql:
        conn.execute("SELECT id FROM projects WHERE id = ? FOR UPDATE", (project.id,)).fetchone()
    elif not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    suffix = " FOR UPDATE" if mysql else ""
    if conn.execute("SELECT project_id FROM project_deletions WHERE project_id = ?" + suffix, (project.id,)).fetchone():
        raise FileNotFoundError("项目已删除，无法保存旧任务结果")
    project.settings = normalize_bid_settings(project.settings)
    payload = project.model_dump_json(indent=2)
    conn.execute(
        """
        INSERT INTO projects (
            id, bid_title, page_count, words_per_page, estimated_word_count,
            bid_type, selected_model, selected_provider,
            state_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            bid_title = excluded.bid_title,
            page_count = excluded.page_count,
            words_per_page = excluded.words_per_page,
            estimated_word_count = excluded.estimated_word_count,
            bid_type = excluded.bid_type,
            selected_model = excluded.selected_model,
            selected_provider = excluded.selected_provider,
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (
            project.id,
            project.settings.bidTitle,
            project.settings.pageCount,
            project.settings.wordsPerPage,
            project.settings.estimatedWordCount,
            project.settings.bidType,
            project.settings.selectedModel,
            project.settings.selectedProvider,
            payload,
            project.createdAt.isoformat(),
            project.updatedAt.isoformat(),
        ),
    )
    _sync_bid_row_for_project(conn, project)


def _project_from_row(row: sqlite3.Row) -> ProjectState:
    return ProjectState.model_validate_json(str(row["state_json"]))


def list_projects() -> list[ProjectState]:
    with connect() as conn:
        rows = conn.execute("SELECT state_json FROM projects ORDER BY updated_at DESC").fetchall()
    return [_project_from_row(row) for row in rows]


def create_project(bid_settings: BidSettings | None = None) -> ProjectState:
    project = ProjectState(
        id=str(uuid.uuid4()),
        settings=normalize_bid_settings(bid_settings),
        createdAt=now_utc(),
        updatedAt=now_utc(),
    )
    return save_project(project)


def load_project(project_id: str) -> ProjectState:
    with connect() as conn:
        row = conn.execute("SELECT state_json FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"Project not found: {project_id}")
    return _project_from_row(row)


def get_bid_id(project_id: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT bid_id FROM bids WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            project_row = conn.execute("SELECT state_json FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project_row is None:
                raise FileNotFoundError(f"Project not found: {project_id}")
            project = _project_from_row(project_row)
            bid_id = _sync_bid_row_for_project(conn, project)
            conn.commit()
            return bid_id
        return int(row["bid_id"])


def ensure_bid_exists(bid_id: int) -> sqlite3.Row:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"Bid not found: {bid_id}")
    return row


def get_project_id_by_bid_id(bid_id: int) -> str:
    row = ensure_bid_exists(bid_id)
    return str(row["project_id"])


def load_project_by_bid_id(bid_id: int) -> ProjectState:
    return load_project(get_project_id_by_bid_id(bid_id))


def update_bid_current_step(bid_id: int, step: int) -> None:
    now = now_utc().isoformat()
    with connect() as conn:
        row = conn.execute("SELECT project_id FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"Bid not found: {bid_id}")
        conn.execute(
            "UPDATE bids SET current_step = ?, update_time = ? WHERE bid_id = ?",
            (step, now, bid_id),
        )
        conn.execute(
            "UPDATE projects SET current_step = ?, updated_at = ? WHERE id = ?",
            (step, now, row["project_id"]),
        )
        conn.commit()


def save_project(project: ProjectState) -> ProjectState:
    project.updatedAt = now_utc()
    with connect() as conn:
        _upsert_project(conn, project)
        conn.commit()
    return project


def update_project_settings(project_id: str, payload: ProjectSettingsUpdateRequest) -> ProjectState:
    project = load_project(project_id)
    values: dict[str, Any] = project.settings.model_dump()
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            values[key] = value
    project.settings = normalize_bid_settings(BidSettings.model_validate(values))
    return save_project(project)


async def save_upload(project: ProjectState, upload: UploadFile) -> UploadedFileInfo:
    suffix = Path(upload.filename or "file").suffix
    file_id = f"{uuid.uuid4().hex}{suffix}"
    target = settings.upload_dir / file_id
    content = await upload.read()
    target.write_bytes(content)

    content_type = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "application/octet-stream"
    info = UploadedFileInfo(
        fileId=file_id,
        filename=upload.filename or file_id,
        size=len(content),
        contentType=content_type,
        projectPath=f"uploads/{file_id}",
    )
    project.files.append(info)
    save_project(project)
    return info


def read_uploaded_text(info: UploadedFileInfo) -> str:
    path = settings.upload_dir / info.fileId
    if not path.exists() or not path.is_file():
        return ""
    if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".yml", ".yaml"}:
        return path.read_text(encoding="utf-8", errors="ignore")[:20000]
    return f"[{info.filename}] uploaded, binary parsing will be implemented later."
