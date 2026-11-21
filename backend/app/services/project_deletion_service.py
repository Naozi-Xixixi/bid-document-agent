"""Delete one tenant project without deleting shared library material."""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.services import storage
from app.services.project_activity import ActivityConflict, begin_deletion

logger = logging.getLogger(__name__)


class ProjectDeletionError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


# Children first, also covering old databases whose foreign keys were absent.
_BID_TABLES = (
    "knowledge_source_refs", "bid_case_reference", "bid_analysis_result", "bid_chapter_material_selections",
    "bid_chapter_contents", "bid_catalog_chapters", "bid_catalog_versions",
    "bid_mermaid_assets", "bid_list_files", "bid_reference_materials",
    "bid_parse_info", "bid_analysis_documents", "bid_analysis_tasks", "bid_audit_result",
)
_REFERENCE_COLUMNS = {
    "projects": ("state_json",),
    "bid_parse_info": ("tender_file_path",),
    "bid_list_files": ("file_path",),
    "bid_reference_materials": ("file_path",),
    "bid_analysis_documents": ("tender_file_path", "bid_file_path", "tender_markdown", "bid_markdown"),
    "bid_audit_result": ("tender_file_path", "bid_file_path"),
    "bid_catalog_chapters": ("graphic_markdown",),
    "bid_chapter_contents": ("content_text", "content_html"),
    "bid_mermaid_assets": ("chart_image_data", "source_text"),
    "knowledge_asset": ("resource_path", "content_text", "metadata_json"),
    "knowledge_item": ("original_file_path", "source_local_path", "content_text", "content_markdown"),
    "knowledge_item_version": ("original_file_path", "source_local_path", "content_text"),
    "knowledge_items": ("file_path", "content_text"),
    "rag_case_documents": ("file_path", "source_path", "markdown"),
}


def _columns(conn, table: str) -> set[str]:
    # All callers use the fixed table names above; the adapter supports PRAGMA.
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _assert_idle(conn, bid_id: int | None) -> None:
    if bid_id is None:
        return
    tenant = get_current_tenant()
    company = tenant.company_id if tenant else "company_default"
    parse = sys.modules.get("app.services.bid_parse_service")
    if parse is not None:
        with parse._PARSE_TASK_LOCK:
            if any(key[:2] == (company, bid_id) and value for key, value in parse._ACTIVE_PARSE_TASKS.items()):
                raise ProjectDeletionError("项目正在解析或提取信息，请等待完成或停止后再删除")
    catalog = sys.modules.get("app.services.bid_catalog_service")
    if catalog is not None:
        with catalog._CATALOG_TASK_LOCK:
            task = catalog.CATALOG_TASKS.get(catalog._catalog_task_key(bid_id))
            if task and (task.get("status") == "processing" or task.get("running")):
                raise ProjectDeletionError("项目正在生成目录，请等待完成或停止后再删除")
    content = sys.modules.get("app.services.bid_content_service")
    if content is not None:
        with content._CONTENT_GENERATION_LOCK:
            content_company = tenant.company_id if tenant else "default"
            if any(key[:2] == (content_company, bid_id) for key in content._ACTIVE_CONTENT_GENERATIONS):
                raise ProjectDeletionError("项目正在生成正文，请等待完成或停止后再删除")
    for table, column, states, label in (
        ("bid_parse_info", "parse_status", ("processing",), "解析或信息提取"),
        ("bid_reference_materials", "parse_status", ("processing",), "参考资料解析"),
        ("bid_chapter_contents", "status", ("generating",), "正文生成"),
        ("bid_analysis_tasks", "status", ("processing",), "标书分析"),
        ("bid_audit_result", "status", ("processing", "running", "parsing", "analyzing"), "标书审核"),
    ):
        if {"bid_id", column}.issubset(_columns(conn, table)):
            marks = ",".join("?" for _ in states)
            if conn.execute(f"SELECT 1 FROM {table} WHERE bid_id = ? AND {column} IN ({marks}) LIMIT 1", (bid_id, *states)).fetchone():
                raise ProjectDeletionError(f"项目仍有{label}任务，请等待完成或停止后再删除")


def _checked(path: Path, root: Path) -> Path:
    """Validate lexically and canonically, rejecting every reparse component."""
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if path == root or not path.is_relative_to(root):
        raise ProjectDeletionError("项目文件路径异常，已取消删除以保护其他资料")
    cursor = root
    for part in ("", *path.relative_to(root).parts):
        if part:
            cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ProjectDeletionError("项目目录包含符号链接或目录联接，已取消删除")
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise ProjectDeletionError("项目文件路径越界，已取消删除")
    return path


def _walk_files(path: Path, root: Path) -> list[Path]:
    path = _checked(path, root)
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    result: list[Path] = []
    for entry in path.iterdir():
        result.extend(_walk_files(entry, root))
    return result


def _resolve_stored(value: str) -> Path:
    value = unquote(value).replace("\\", "/")
    if value.startswith("/uploads/"):
        return settings.resolve_local_path(settings.data_dir) / "uploads" / value[len("/uploads/"):]
    path = Path(value)
    return path if path.is_absolute() else settings.backend_root / path


def _owned_files(conn, row, bid_id: int | None) -> tuple[dict[Path, Path], list[tuple[Path, Path]]]:
    upload_root = settings.upload_dir.absolute()
    project_root = settings.project_dir.absolute()
    project_id = str(row["id"])
    if project_id in {".", ".."} or any(char in project_id for char in "/\\:"):
        raise ProjectDeletionError("项目标识异常，已取消删除")
    owned_dirs: list[tuple[Path, Path]] = [(project_root / project_id, project_root)]
    if bid_id is not None:
        owned_dirs.extend([(upload_root / "bids" / str(bid_id), upload_root), (upload_root / "analysis" / str(bid_id), upload_root)])
    files: dict[Path, Path] = {}
    for directory, root in owned_dirs:
        files.update((path, root) for path in _walk_files(directory, root))
    project_json = _checked(project_root / f"{project_id}.json", project_root)
    if project_json.is_file():
        files[project_json] = project_root
    try:
        payload = json.loads(row["state_json"])
    except (ValueError, TypeError) as exc:
        raise ProjectDeletionError("项目文件清单损坏，请修复后再删除") from exc
    for item in payload.get("files", []):
        file_id = str(item.get("fileId") or "")
        if not file_id or Path(file_id).name != file_id or any(char in file_id for char in "/\\:") or file_id in {".", ".."}:
            raise ProjectDeletionError("项目上传文件路径异常，已取消删除")
        candidate = _checked(upload_root / file_id, upload_root)
        if candidate.is_file():
            files[candidate] = upload_root
    # The independent audit module uses a flat shared audit directory. Only
    # explicitly linked audit file copies are owned, never the directory itself.
    columns = _columns(conn, "bid_audit_result")
    if bid_id is not None and {"bid_id", "tender_file_path", "bid_file_path"}.issubset(columns):
        for audit in conn.execute("SELECT tender_file_path, bid_file_path FROM bid_audit_result WHERE bid_id = ?", (bid_id,)):
            for value in audit:
                if not value:
                    continue
                candidate = Path(os.path.abspath(_resolve_stored(str(value))))
                if candidate.is_relative_to(upload_root / "audit"):
                    candidate = _checked(candidate, upload_root)
                    if candidate.is_file():
                        files[candidate] = upload_root
    # A previous deletion may have retained its upload because this project
    # referenced it (for example merge-parse). Release that last reference now.
    selected_refs = [_normalized(row["state_json"])]
    if bid_id is not None:
        for table, requested in _REFERENCE_COLUMNS.items():
            columns = _columns(conn, table)
            selected = [column for column in requested if column in columns]
            if table.startswith("bid_") and "bid_id" in columns and selected:
                for value_row in conn.execute(f"SELECT {','.join(selected)} FROM {table} WHERE bid_id = ?", (bid_id,)):
                    selected_refs.extend(_normalized(value) for value in value_row if value)
    for old in conn.execute("SELECT d.bid_id FROM project_deletions d LEFT JOIN bids b ON b.bid_id = d.bid_id WHERE d.bid_id IS NOT NULL AND b.bid_id IS NULL"):
        for folder in ("bids", "analysis"):
            directory = upload_root / folder / str(int(old["bid_id"]))
            names = {_normalized(directory)}
            try:
                names.add(_normalized(directory.relative_to(settings.resolve_local_path(settings.data_dir))))
            except ValueError:
                pass
            try:
                names.add(_normalized(directory.relative_to(settings.backend_root)))
            except ValueError:
                pass
            try:
                names.add(_normalized("/uploads/" + directory.relative_to(settings.resolve_local_path(settings.data_dir) / "uploads").as_posix()))
            except ValueError:
                pass
            if any(name + "/" in text for text in selected_refs for name in names):
                owned_dirs.append((directory, upload_root))
                files.update((path, upload_root) for path in _walk_files(directory, upload_root))
    return files, owned_dirs


def _normalized(value: Any) -> str:
    return unquote(str(value or "")).replace("\\\\", "\\").replace("\\", "/").casefold()


def _reference_strings(conn, project_id: str, bid_id: int | None):
    for table, requested in _REFERENCE_COLUMNS.items():
        columns = _columns(conn, table)
        selected = [column for column in requested if column in columns]
        if not selected:
            continue
        query = f"SELECT {','.join(selected)} FROM {table}"
        args: tuple[Any, ...] = ()
        if table == "projects":
            query += " WHERE id <> ?"
            args = (project_id,)
        elif table.startswith("bid_") and "bid_id" in columns and bid_id is not None:
            query += " WHERE bid_id <> ? OR bid_id IS NULL"
            args = (bid_id,)
        for row in conn.execute(query, args):
            for value in row:
                if value:
                    yield _normalized(value)


def _shared_files(conn, files: dict[Path, Path], project_id: str, bid_id: int | None) -> set[Path]:
    variants: dict[Path, set[str]] = {}
    global_uploads = (settings.resolve_local_path(settings.data_dir) / "uploads").resolve()
    for path in files:
        names = {_normalized(path)}
        for base, prefix in ((settings.backend_root, ""), (settings.resolve_local_path(settings.data_dir), ""), (global_uploads, "/uploads/"), (settings.upload_dir, "uploads/")):
            try:
                names.add(_normalized(prefix + path.relative_to(base).as_posix()))
            except ValueError:
                pass
        # Legacy ProjectState.files stores a basename as fileId.
        if path.parent == settings.upload_dir.absolute():
            names.add(_normalized(path.name))
        variants[path] = {name for name in names if name}
    shared: set[Path] = set()
    for text in _reference_strings(conn, project_id, bid_id):
        for path, names in variants.items():
            if path not in shared and any(name in text for name in names):
                shared.add(path)
    return shared


def _stage_files(files: dict[Path, Path], shared: set[Path]) -> tuple[Path | None, list[tuple[Path, Path]]]:
    candidates = [path for path in files if path not in shared]
    if not candidates:
        return None, []
    upload_root = settings.upload_dir.absolute()
    quarantine = _checked(upload_root / ".project-deletions" / uuid.uuid4().hex, upload_root)
    quarantine.mkdir(parents=True, exist_ok=False)
    moves = [(path, quarantine / str(index)) for index, path in enumerate(candidates)]
    # A durable manifest also permits manual recovery after process/power loss.
    (quarantine / "manifest.json").write_text(json.dumps([{"source": str(a), "staged": b.name} for a, b in moves], ensure_ascii=False), encoding="utf-8")
    staged: list[tuple[Path, Path]] = []
    try:
        for source, target in moves:
            _checked(source, files[source])
            source.replace(target)
            staged.append((source, target))
    except Exception:
        try:
            _restore_files(quarantine, staged)
        except Exception as restore_exc:
            logger.exception("Staged project files require manual recovery: %s", quarantine.name)
            raise ProjectDeletionError("删除未完成，项目数据已保留，但部分文件仍在删除暂存目录中，需要管理员恢复", 500) from restore_exc
        raise
    return quarantine, staged


def _restore_files(quarantine: Path | None, moves: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moves):
        if target.exists():
            # Parents were retained during staging. Never overwrite a newer file.
            if source.exists():
                raise RuntimeError("暂存恢复遇到同名文件，需要管理员恢复")
            target.replace(source)
    if quarantine is not None:
        _purge_quarantine(quarantine)


def _purge_quarantine(path: Path) -> None:
    root = settings.upload_dir.absolute()
    path = _checked(path, root)
    base = root / ".project-deletions"
    if path.parent != base or len(path.name) != 32 or any(c not in "0123456789abcdef" for c in path.name):
        raise ProjectDeletionError("文件清理记录路径异常，已停止磁盘清理")
    _walk_files(path, root)  # Refuse a reparse point anywhere before rmtree.
    if path.exists():
        shutil.rmtree(path)


def _remove_empty_dirs(directories: list[tuple[Path, Path]]) -> None:
    def prune(path: Path, root: Path):
        path = _checked(path, root)
        if not path.is_dir():
            return
        for child in path.iterdir():
            if child.is_dir():
                prune(child, root)
        if not any(path.iterdir()):
            path.rmdir()
    for directory, root in directories:
        prune(directory, root)


def _finish_cleanup(project_id: str, bid_id: int | None, quarantine: Path | None, removed: int, preserved: int, directories=()) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        if quarantine is not None:
            _purge_quarantine(quarantine)
        _remove_empty_dirs(list(directories))
        with closing(storage.connect()) as conn:
            conn.execute("UPDATE project_deletions SET cleanup_path = NULL WHERE project_id = ?", (project_id,))
            conn.commit()
    except Exception:
        logger.exception("Project deletion committed but disk cleanup is pending: %s", project_id)
        warnings.append("项目已删除，但部分本地文件清理失败，磁盘空间尚未完全释放；请联系管理员处理暂存文件。")
    return {"success": True, "deleted": True, "projectId": project_id, "bidId": bid_id,
            "message": "项目已删除", "warnings": warnings,
            "cleanup": {"status": "pending" if warnings else "completed", "removed": removed, "preservedShared": preserved}}


def delete_project(project_id: str) -> dict[str, Any]:
    # Read once to learn both aliases, then re-read while holding the deletion
    # lease and DB write lock. Requests cannot start in the check/delete gap.
    with closing(storage.connect()) as conn:
        row = conn.execute("SELECT p.*, b.bid_id AS linked_bid_id FROM projects p LEFT JOIN bids b ON b.project_id = p.id WHERE p.id = ?", (project_id,)).fetchone()
        tombstone = conn.execute("SELECT * FROM project_deletions WHERE project_id = ?", (project_id,)).fetchone()
    if row is None and tombstone is None:
        raise ProjectDeletionError("项目不存在或已被删除", 404)
    bid_id = (row["linked_bid_id"] if row is not None else tombstone["bid_id"])
    bid_id = int(bid_id) if bid_id is not None else None
    try:
        with begin_deletion(project_id, bid_id):
            if row is None:
                return _finish_cleanup(project_id, bid_id, Path(tombstone["cleanup_path"]) if tombstone["cleanup_path"] else None, int(tombstone["removed_count"]), int(tombstone["preserved_count"]))
            quarantine: Path | None = None
            moves: list[tuple[Path, Path]] = []
            with closing(storage.connect()) as conn:
                try:
                    mysql = (settings.db_backend or "sqlite").strip().lower() == "mysql"
                    conn.execute("START TRANSACTION" if mysql else "BEGIN IMMEDIATE")
                    suffix = " FOR UPDATE" if mysql else ""
                    current = conn.execute("SELECT * FROM projects WHERE id = ?" + suffix, (project_id,)).fetchone()
                    if current is None:
                        raise ProjectDeletionError("项目不存在或已被删除", 404)
                    _assert_idle(conn, bid_id)
                    files, directories = _owned_files(conn, current, bid_id)
                    shared = _shared_files(conn, files, project_id, bid_id)
                    quarantine, moves = _stage_files(files, shared)
                    conn.execute("INSERT INTO project_deletions (project_id, bid_id, deleted_at, cleanup_path, removed_count, preserved_count) VALUES (?, ?, ?, ?, ?, ?)", (project_id, bid_id, storage.now_utc().isoformat(), str(quarantine) if quarantine else None, len(moves), len(shared)))
                    if bid_id is not None:
                        for table in _BID_TABLES:
                            if "bid_id" in _columns(conn, table):
                                conn.execute(f"DELETE FROM {table} WHERE bid_id = ?", (bid_id,))
                        conn.execute("DELETE FROM bids WHERE bid_id = ?", (bid_id,))
                    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    try:
                        _restore_files(quarantine, moves)
                    except Exception as restore_exc:
                        logger.exception("Project delete rollback requires file recovery: %s", project_id)
                        raise ProjectDeletionError("删除未完成，项目数据已保留，但部分文件仍在删除暂存目录中，需要管理员恢复", 500) from restore_exc
                    if isinstance(exc, ProjectDeletionError):
                        raise
                    logger.exception("Project deletion rolled back: %s", project_id)
                    raise ProjectDeletionError("删除失败，项目数据和上传文件已恢复，请稍后重试", 500) from exc
            catalog = sys.modules.get("app.services.bid_catalog_service")
            if catalog is not None and bid_id is not None:
                with catalog._CATALOG_TASK_LOCK:
                    catalog.CATALOG_TASKS.pop(catalog._catalog_task_key(bid_id), None)
            return _finish_cleanup(project_id, bid_id, quarantine, len(moves), len(shared), directories)
    except ActivityConflict as exc:
        raise ProjectDeletionError(str(exc), 409) from exc
