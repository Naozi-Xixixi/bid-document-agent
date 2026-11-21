"""Company-scoped chapter workflow; CAS, body and audit snapshot share one transaction."""
from __future__ import annotations

import difflib
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import HTTPException
from app.core.tenant_context import get_current_tenant
from app.services import storage, auth_service


def fail(message, status=409):
    raise HTTPException(status_code=status, detail=message)


def actor():
    tenant = get_current_tenant()
    return (tenant.user_id, tenant.username) if tenant else (None, "系统任务")


def now():
    return datetime.now(timezone.utc).isoformat()


def chapter(conn, bid_id, chapter_id):
    row = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id)).fetchone()
    if row is None:
        fail("章节不存在或无权访问", 404)
    return dict(row)


def members():
    tenant = get_current_tenant()
    if tenant is None:
        fail("请先登录", 401)
    auth_service.ensure_schema()
    with auth_service._session() as conn:
        rows = conn.execute("SELECT user_id, username, display_name, role FROM auth_user WHERE company_id = ? AND status = 'active' ORDER BY user_id", (tenant.company_id,)).fetchall()
    return [dict(row) for row in rows]


def permissions(row):
    tenant = get_current_tenant()
    uid = tenant.user_id if tenant else None
    admin = tenant is not None and tenant.role == "admin"
    writer = admin or not row["assigned_user_id"] or uid == row["assigned_user_id"]
    reviewer = uid is not None and (admin or uid == row["reviewer_user_id"])
    return {"can_assign": admin, "can_edit": writer and row["review_state"] == "draft",
            "can_submit": writer and row["review_state"] == "draft",
            "can_review": reviewer and uid != row["submitted_by"] and row["review_state"] == "in_review",
            "can_reopen": reviewer and row["review_state"] != "draft"}


def writable(row):
    if row["review_state"] != "draft":
        fail("本章正在审核或已经定稿，请先由审核人/管理员退回或重新打开")
    tenant = get_current_tenant()
    if tenant and not permissions(row)["can_edit"]:
        fail("本章已分配给其他负责人，只有负责人或管理员可以修改", 403)


def start_revision(bid_id, chapter_id, *, only_if_empty=False):
    with storage.connect() as conn:
        row = chapter(conn, bid_id, chapter_id)
        if only_if_empty and str(row.get("graphic_markdown") or "").strip():
            return None
        writable(row)
        return row["edit_revision"]


def snapshot(conn, row):
    body = conn.execute("SELECT * FROM bid_chapter_contents WHERE bid_id = ? AND chapter_id = ?", (row["bid_id"], row["chapter_id"])).fetchone()
    return {"workflow": {key: row.get(key) for key in ("assigned_user_id", "reviewer_user_id", "review_state", "submitted_by")}, "markdown": row.get("graphic_markdown") or "", "ref_chart_ids": row.get("ref_chart_ids") or "[]",
            "ref_image_asset_ids": row.get("ref_image_asset_ids") or "[]", "format_template_id": row.get("graphic_format_template_id"),
            "content": dict(body) if body else None}


def record(conn, row, action, note="", baseline=False):
    uid, name = (None, "历史内容（接入协作前）") if baseline else actor()
    conn.execute("INSERT INTO chapter_revisions (bid_id, chapter_id, revision, snapshot_json, actor_id, actor_name, action, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (row["bid_id"], row["chapter_id"], row["edit_revision"], json.dumps(snapshot(conn, row), ensure_ascii=False), uid, name, action, note, now()))


@contextmanager
def change(conn, bid_id, chapter_id, expected_revision, action="save", note="", edit=True):
    row = chapter(conn, bid_id, chapter_id)
    if expected_revision is None:
        if get_current_tenant() is not None:
            fail("缺少章节版本号，请重新加载章节后再保存", 428)
        expected_revision = row["edit_revision"]  # offline maintenance only
    if edit:
        writable(row)
    # This write locks the row before reading snapshots/comments; stale writes cannot commit.
    changed = conn.execute("UPDATE bid_catalog_chapters SET edit_revision = edit_revision + 1 WHERE bid_id = ? AND chapter_id = ? AND edit_revision = ?", (bid_id, chapter_id, expected_revision))
    if changed.rowcount != 1:
        fail("本章已被其他人修改或审核，未覆盖服务器内容。请保留本地草稿，对比最新版本后再保存")
    # Re-read after obtaining the write lock (also correct with MySQL REPEATABLE READ).
    row["edit_revision"] = expected_revision
    if conn.execute("SELECT id FROM chapter_revisions WHERE chapter_id = ? AND revision = ?", (chapter_id, expected_revision)).fetchone() is None:
        record(conn, row, "baseline", baseline=True)
    yield row
    record(conn, chapter(conn, bid_id, chapter_id), action, note)


def detail(bid_id, chapter_id):
    with storage.connect() as conn:
        row = chapter(conn, bid_id, chapter_id)
        versions = conn.execute("SELECT revision, actor_id, actor_name, action, note, created_at FROM chapter_revisions WHERE bid_id = ? AND chapter_id = ? ORDER BY revision DESC LIMIT 100", (bid_id, chapter_id)).fetchall()
        unresolved = conn.execute("SELECT COUNT(*) FROM chapter_comments WHERE bid_id = ? AND chapter_id = ? AND resolved = 0", (bid_id, chapter_id)).fetchone()[0]
        comments = conn.execute("SELECT * FROM chapter_comments WHERE bid_id = ? AND chapter_id = ? ORDER BY resolved ASC, id DESC LIMIT 200", (bid_id, chapter_id)).fetchall()
    return {"revision": row["edit_revision"], "assigned_user_id": row["assigned_user_id"], "reviewer_user_id": row["reviewer_user_id"],
            "state": row["review_state"], "unresolved_count": unresolved, "permissions": permissions(row), "versions": [dict(r) for r in versions], "comments": [dict(r) for r in comments]}


def assign(bid_id, chapter_id, payload):
    tenant = get_current_tenant()
    if not tenant or tenant.role != "admin":
        fail("只有管理员可以分配章节和审核人", 403)
    allowed = {m["user_id"] for m in members()}
    for uid in (payload.assigned_user_id, payload.reviewer_user_id):
        if uid is not None and uid not in allowed:
            fail("只能选择本公司启用的账号", 400)
    if payload.assigned_user_id and payload.assigned_user_id == payload.reviewer_user_id:
        fail("负责人和审核人必须是不同账号", 400)
    with storage.connect() as conn:
        with change(conn, bid_id, chapter_id, payload.expected_revision, "assign", "调整负责人/审核人"):
            conn.execute("UPDATE bid_catalog_chapters SET assigned_user_id = ?, reviewer_user_id = ? WHERE bid_id = ? AND chapter_id = ?", (payload.assigned_user_id, payload.reviewer_user_id, bid_id, chapter_id))
        conn.commit()
    return detail(bid_id, chapter_id)


def transition(bid_id, chapter_id, payload):
    with storage.connect() as conn:
        with change(conn, bid_id, chapter_id, payload.expected_revision, payload.action, payload.note, edit=False) as row:
            perms = permissions(row)
            action = payload.action
            if action == "submit":
                if not perms["can_submit"]:
                    fail("只有章节负责人或管理员可以提交草稿审核", 403)
                uid, _ = actor()
                if row["reviewer_user_id"] is None or row["reviewer_user_id"] == uid:
                    fail("请先指定另一位本公司审核人", 400)
                if row["reviewer_user_id"] not in {m["user_id"] for m in members()}:
                    fail("审核账号已停用，请重新分配", 400)
                snap = snapshot(conn, row)
                if not snap["markdown"].strip() and not (snap["content"] or {}).get("content_text", "").strip():
                    fail("正文为空，不能提交审核", 400)
                if (snap["content"] or {}).get("status") == "generating":
                    fail("正文仍在生成，暂不能提交审核")
                state = "in_review"
                conn.execute("UPDATE bid_catalog_chapters SET submitted_by = ? WHERE chapter_id = ?", (uid, chapter_id))
            elif action in {"approve", "reject"}:
                if not perms["can_review"]:
                    fail("仅指定审核人或另一位管理员可审核，不能审批自己提交的章节", 403)
                if action == "approve" and conn.execute("SELECT id FROM chapter_comments WHERE chapter_id = ? AND resolved = 0 LIMIT 1", (chapter_id,)).fetchone():
                    fail("还有未解决的批注，请处理后再定稿")
                if action == "reject" and not payload.note.strip():
                    fail("请填写退回原因", 400)
                state = "approved" if action == "approve" else "draft"
            else:
                if not perms["can_reopen"]:
                    fail("仅审核人或管理员可重新打开章节", 403)
                if not payload.note.strip():
                    fail("请填写重新打开的原因", 400)
                state = "draft"
            conn.execute("UPDATE bid_catalog_chapters SET review_state = ? WHERE chapter_id = ?", (state, chapter_id))
        conn.commit()
    return detail(bid_id, chapter_id)


def add_comment(bid_id, chapter_id, payload):
    uid, name = actor()
    if uid is None:
        fail("请先登录", 401)
    with storage.connect() as conn:
        with change(conn, bid_id, chapter_id, payload.expected_revision, "comment", payload.body, edit=False) as row:
            if row["review_state"] == "approved":
                fail("定稿章节请先重新打开，再添加批注")
            conn.execute("INSERT INTO chapter_comments (bid_id, chapter_id, revision, actor_id, actor_name, body, quote, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (bid_id, chapter_id, payload.expected_revision, uid, name, payload.body, payload.quote, now()))
        conn.commit()
    return detail(bid_id, chapter_id)


def resolve(bid_id, chapter_id, comment_id, payload):
    uid, name = actor()
    with storage.connect() as conn:
        with change(conn, bid_id, chapter_id, payload.expected_revision, "resolve_comment", str(comment_id), edit=False) as row:
            comment = conn.execute("SELECT * FROM chapter_comments WHERE bid_id = ? AND chapter_id = ? AND id = ?", (bid_id, chapter_id, comment_id)).fetchone()
            if comment is None:
                fail("批注不存在", 404)
            tenant = get_current_tenant()
            if not tenant or (tenant.role != "admin" and uid not in {comment["actor_id"], row["reviewer_user_id"]}):
                fail("只有批注作者、审核人或管理员可标记解决", 403)
            if row["review_state"] == "approved":
                fail("定稿章节请先重新打开")
            conn.execute("UPDATE chapter_comments SET resolved = 1, resolved_by = ?, resolved_at = ? WHERE id = ?", (name, now(), comment_id))
        conn.commit()
    return detail(bid_id, chapter_id)


def version(conn, bid_id, chapter_id, revision):
    row = conn.execute("SELECT snapshot_json FROM chapter_revisions WHERE bid_id = ? AND chapter_id = ? AND revision = ?", (bid_id, chapter_id, revision)).fetchone()
    if row is None:
        fail("版本不存在", 404)
    return json.loads(row["snapshot_json"])


def compare(bid_id, chapter_id, left, right):
    with storage.connect() as conn:
        chapter(conn, bid_id, chapter_id)
        a, b = version(conn, bid_id, chapter_id, left), version(conn, bid_id, chapter_id, right)
    def text(s):
        return s["markdown"] or (s["content"] or {}).get("content_text", "")
    return {"left": text(a), "right": text(b), "diff": "\n".join(difflib.unified_diff(text(a).splitlines(), text(b).splitlines(), fromfile=f"版本 {left}", tofile=f"版本 {right}", lineterm=""))}


def restore(bid_id, chapter_id, payload):
    with storage.connect() as conn:
        with change(conn, bid_id, chapter_id, payload.expected_revision, "restore", f"回退自版本 {payload.revision}") as row:
            old = version(conn, bid_id, chapter_id, payload.revision)
            conn.execute("UPDATE bid_catalog_chapters SET graphic_markdown = ?, ref_chart_ids = ?, ref_image_asset_ids = ?, graphic_format_template_id = ? WHERE chapter_id = ?", (old["markdown"], old["ref_chart_ids"], old["ref_image_asset_ids"], old["format_template_id"], chapter_id))
            if old["content"]:
                c = old["content"]
                from app.services.bid_content_service import _write_content_status
                _write_content_status(conn, bid_id, row["version_id"], chapter_id,
                                      "completed" if c["content_text"] else "unwritten",
                                      c["content_text"], c["content_html"], json.loads(c["style_config"] or "{}"))
            else:
                conn.execute("DELETE FROM bid_chapter_contents WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id))
        conn.commit()
    return detail(bid_id, chapter_id)


def guard_catalog_change(conn, bid_id):
    # Lock before checking: a concurrent approval must not be bypassed by deleting/replacing its chapter.
    conn.execute("UPDATE bid_catalog_chapters SET edit_revision = edit_revision WHERE bid_id = ?", (bid_id,))
    rows = conn.execute("SELECT review_state, assigned_user_id FROM bid_catalog_chapters WHERE bid_id = ?", (bid_id,)).fetchall()
    if any(row["review_state"] != "draft" for row in rows):
        fail("目录包含审核中或已定稿章节，请先重新打开这些章节，再修改或重新生成目录")
    tenant = get_current_tenant()
    if tenant and tenant.role != "admin" and any(row["assigned_user_id"] for row in rows):
        fail("已分工项目的目录由管理员调整，以免影响其他成员章节", 403)
