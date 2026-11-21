"""Durable checkpoints for the single-process desktop knowledge worker.

The database is tenant-scoped. This is deliberately not a distributed queue:
multiple backend workers require a shared lease-based scheduler.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.services import storage

_tasks: dict[tuple[str, str, int], asyncio.Task] = {}


def create_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_project_workflow (
            project_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
            phase TEXT NOT NULL DEFAULT 'parse', error TEXT, updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES bid_project(project_id) ON DELETE CASCADE
        )
    """)


def prepare_project(project_id: int, conn=None) -> None:
    from . import service as s
    def insert(db):
        db.execute(
            "INSERT OR IGNORE INTO knowledge_project_workflow (project_id,status,phase,updated_at) VALUES (?,'pending','parse',?)",
            (project_id, s._now()),
        )
    if conn is not None:
        insert(conn)
    else:
        with storage.connect() as db:
            insert(db)
            db.commit()


def workflow_state(project_id: int, *, connection=None) -> dict | None:
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else storage.connect()) as conn:
        row = conn.execute("SELECT status,phase,error FROM knowledge_project_workflow WHERE project_id=?", (project_id,)).fetchone()
    return dict(row) if row else None


async def run_project(project_id: int, tender_id: int, bid_id: int) -> None:
    from . import service as s
    key = (s._tenant_item_key(project_id)[0], "project", project_id)
    if key in _tasks:
        await asyncio.shield(_tasks[key])
        return
    task = asyncio.create_task(_run_project(project_id, tender_id, bid_id))
    _tasks[key] = task
    try:
        await task
    finally:
        _tasks.pop(key, None)


async def _run_project(project_id: int, tender_id: int, bid_id: int) -> None:
    from . import service as s
    prepare_project(project_id)

    def checkpoint(status, phase, error=None):
        with storage.connect() as conn:
            conn.execute(
                "UPDATE knowledge_project_workflow SET status=?,phase=?,error=?,updated_at=? WHERE project_id=?",
                (status, phase, error, s._now(), project_id),
            )
            conn.commit()

    phase = (workflow_state(project_id) or {}).get("phase", "parse")
    try:
        checkpoint("processing", phase)
        # Completed base parsing must never clear images/chunks during recovery.
        for item_id in (tender_id, bid_id):
            state = await asyncio.to_thread(s.item_status, item_id)
            if state["status"] in {"pending", "processing"}:
                await s.process_item(item_id)
            state = await asyncio.to_thread(s.item_status, item_id)
            if state["status"] != "completed":
                raise ValueError(f"文件 {item_id} 基础解析未完成，请先重试该文件")
        if phase in {"parse", "interpret"}:
            phase = "interpret"
            checkpoint("processing", phase)
            # OCR produces additional source chunks; wait for active workers before analysis.
            for item_id in (tender_id, bid_id):
                while s._tenant_item_key(item_id) in s._DEEP_ITEM_IDS:
                    await asyncio.sleep(0.2)
                state = await asyncio.to_thread(s.item_status, item_id)
                if state.get("deep_status") == "failed":
                    raise ValueError(f"文件 {item_id} 图片识别未完成，请先重试深度识别：{state.get('deep_error') or '存在失败图片'}")
            results = await asyncio.gather(
                s.interpret_knowledge_item(tender_id, role="tender"),
                s.interpret_knowledge_item(bid_id, role="bid"),
            )
            if any(r["status"] != "completed" for r in results):
                raise ValueError("部分 AI 解读批次失败，重试时将复用已完成批次")
            phase = "match"
            checkpoint("processing", phase)
        if phase == "match":
            await asyncio.to_thread(s.match_project, project_id, use_dense=True, completion_status="completed")
            phase = "template"
            checkpoint("processing", phase)
        if phase == "template":
            await asyncio.to_thread(s.extract_project_template, project_id)
        checkpoint("completed", "done")
    except asyncio.CancelledError:
        checkpoint("pending", phase, "任务中断，重启后继续")
        raise
    except Exception as exc:
        checkpoint("failed", phase, str(exc)[:1000])


async def resume_pending_tasks() -> int:
    """Call once at startup, after the previous local backend has exited."""
    from . import service as s
    from . import document_analysis
    await asyncio.to_thread(s.ensure_schema)
    with storage.connect() as conn:
        # Legacy paired jobs may not yet have a workflow checkpoint.
        legacy = conn.execute(
            "SELECT p.project_id FROM bid_project p "
            "LEFT JOIN knowledge_item_interpretation t ON t.item_id=p.tender_doc_id "
            "LEFT JOIN knowledge_item_interpretation b ON b.item_id=p.bid_doc_id "
            "WHERE p.status IN ('pending','processing','matching','indexing') "
            "OR t.status='processing' OR b.status='processing'"
        ).fetchall()
        for row in legacy:
            prepare_project(int(row["project_id"]), conn)
        projects = [dict(r) for r in conn.execute(
            "SELECT p.project_id,p.tender_doc_id,p.bid_doc_id FROM bid_project p "
            "JOIN knowledge_project_workflow w ON w.project_id=p.project_id "
            "WHERE w.status IN ('pending','processing')"
        ).fetchall()]
        items = [dict(r) for r in conn.execute(
            "SELECT item_id,status,deep_status,vector_status FROM knowledge_item "
            "WHERE status IN ('pending','processing') OR "
            "(status='completed' AND (deep_status='processing' OR vector_status IN ('pending','indexing')))"
        ).fetchall()]
        analyses = [dict(r) for r in conn.execute(
            "SELECT r.item_id,r.role FROM knowledge_analysis_run r JOIN knowledge_item i ON i.item_id=r.item_id "
            "WHERE r.status IN ('pending','processing') AND i.status='completed'"
        ).fetchall()]
        imports = [r["task_id"] for r in conn.execute(
            "SELECT task_id FROM knowledge_import_task WHERE status IN ('pending','processing')"
        ).fetchall()]
        import_items = set()
        for task_id in imports:
            import_items.update(int(r["item_id"]) for r in conn.execute(
                "SELECT item_id FROM knowledge_import_file WHERE task_id=? AND item_id IS NOT NULL AND status<>'completed'",
                (task_id,),
            ).fetchall())
            conn.execute(
                "UPDATE knowledge_import_file SET status='pending' WHERE task_id=? AND status='processing'", (task_id,),
            )
        conn.commit()
    paired = {int(p[k]) for p in projects for k in ("tender_doc_id", "bid_doc_id") if p[k]}
    queued = 0
    # Reset stale vector leases before starting any new local work. Otherwise
    # recovery could reset the lease of a vector task it just launched.
    try:
        queued += await s.resume_pending_vector_sync_tasks()
    except Exception as exc:
        print(f"[KnowledgeRecovery] vector recovery deferred: {exc}", flush=True)

    def launch(kind: str, entity_id: Any, factory) -> bool:
        key = (s._tenant_item_key(0)[0], kind, entity_id)
        if key in _tasks:
            return False
        async def guarded():
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[KnowledgeRecovery] {kind} id={entity_id} failed: {exc}", flush=True)
            finally:
                _tasks.pop(key, None)
        _tasks[key] = asyncio.create_task(guarded())
        return True

    for project in projects:
        pid, tid, bid = project["project_id"], project["tender_doc_id"], project["bid_doc_id"]
        # Different key from run_project's duplicate guard.
        queued += launch("project-launch", pid, lambda p=pid, t=tid, b=bid: run_project(p, t, b))
    for item in items:
        item_id = int(item["item_id"])
        if item["status"] in {"pending", "processing"}:
            if item_id not in paired and item_id not in import_items:
                queued += launch("parse", item_id, lambda i=item_id: s.process_item(i))
        else:
            if item["deep_status"] == "processing":
                queued += int(s._queue_deep_extract(item_id))
            if item["vector_status"] in {"pending", "indexing"}:
                queued += int(s._queue_item_vector_sync(item_id))
    for analysis in analyses:
        if analysis["item_id"] not in paired:
            queued += launch("analysis", analysis["item_id"],
                             lambda a=analysis: document_analysis.interpret(a["item_id"], role=a["role"]))
    for task_id in imports:
        queued += launch("import", task_id, lambda t=task_id: s.run_batch_import_task(t))
    return queued


async def shutdown() -> None:
    from . import service as s
    from . import document_analysis
    tasks = set(_tasks.values()) | set(document_analysis._running.values()) | s._DEEP_TASKS | s._VECTOR_TASKS
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
