from __future__ import annotations

import json
import re
from typing import Any

from app.services import storage


WORDS_PER_PAGE = 780
FLOAT_BUFFER_RATIO = 0.10
MIN_LEAF_WORDS = 300


class BidWordCountServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _ensure_bid(bid_id: int):
    try:
        return storage.ensure_bid_exists(bid_id)
    except FileNotFoundError as exc:
        raise BidWordCountServiceError(40401, f"标书不存在：{bid_id}", 404) from exc


def _select_version(conn, bid_id: int, version_id: int | None = None):
    if version_id:
        row = conn.execute(
            "SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ?",
            (bid_id, version_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM bid_catalog_versions WHERE bid_id = ? ORDER BY version_id ASC LIMIT 1",
            (bid_id,),
        ).fetchone()
    if row is None:
        raise BidWordCountServiceError(40001, "请先生成目录")
    return row


def _fetch_rows(conn, bid_id: int, version_id: int) -> list[Any]:
    return conn.execute(
        """
        SELECT * FROM bid_catalog_chapters
        WHERE bid_id = ? AND version_id = ?
        ORDER BY level ASC, parent_id ASC, sort_order ASC, chapter_id ASC
        """,
        (bid_id, version_id),
    ).fetchall()


def _parse_scoring_keywords(score_text: str) -> list[tuple[str, float]]:
    keywords: list[tuple[str, float]] = []
    parts = [item.strip() for item in re.split(r"[\r\n；;。]+", score_text or "") if item.strip()]
    for part in parts:
        score_match = re.search(r"(\d+(?:\.\d+)?)\s*分", part)
        score = float(score_match.group(1)) if score_match else 10.0
        cleaned = re.sub(r"\d+(?:\.\d+)?\s*分", "", part)
        for token in re.split(r"[、，,\s]+", cleaned):
            token = re.sub(r"[：:（）()\[\]【】]", "", token).strip()
            if len(token) >= 2:
                keywords.append((token[:16], score))
    return keywords[:40]


def _chapter_weight(title: str, score_keywords: list[tuple[str, float]]) -> float:
    text = title or ""
    high_score = 0.0
    for keyword, score in score_keywords:
        if keyword and keyword in text:
            high_score = max(high_score, score)
    weight = 1.0
    if high_score >= 20:
        weight = 1.50
    elif high_score >= 15:
        weight = 1.35
    elif high_score >= 10:
        weight = 1.20
    core_terms = ["技术", "方案", "实施", "服务", "质量", "风险", "保障", "响应", "团队", "安全", "验收"]
    if any(term in text for term in core_terms):
        weight = max(weight, 1.15)
    transition_terms = ["概述", "说明", "附件", "证明", "格式", "承诺", "目录"]
    if any(term in text for term in transition_terms) and not any(term in text for term in core_terms[:6]):
        weight = min(weight, 0.90)
    return max(0.80, min(1.50, weight))


def _leaf_ids(rows: list[Any]) -> list[int]:
    parent_ids = {int(row["parent_id"]) for row in rows if int(row["parent_id"])}
    return [int(row["chapter_id"]) for row in rows if int(row["chapter_id"]) not in parent_ids]


def _ancestor_titles(rows_by_id: dict[int, Any], row: Any) -> list[str]:
    titles = [str(row["chapter_title"])]
    parent_id = int(row["parent_id"])
    while parent_id and parent_id in rows_by_id:
        parent = rows_by_id[parent_id]
        titles.insert(0, str(parent["chapter_title"]))
        parent_id = int(parent["parent_id"])
    return titles


def _round_to_total(raw: dict[int, float], target_total: int) -> dict[int, int]:
    values = {key: max(MIN_LEAF_WORDS, int(round(value))) for key, value in raw.items()}
    if not values:
        return {}
    diff = target_total - sum(values.values())
    keys = sorted(values, key=lambda key: raw[key], reverse=diff > 0)
    idx = 0
    while diff != 0 and keys:
        key = keys[idx % len(keys)]
        step = 1 if diff > 0 else -1
        if step > 0 or values[key] > MIN_LEAF_WORDS:
            values[key] += step
            diff -= step
        idx += 1
        if idx > len(keys) * max(abs(diff) + 10, 20):
            break
    return values


def _aggregate_parent_targets(rows: list[Any], leaf_targets: dict[int, int]) -> dict[int, int]:
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(int(row["chapter_id"]))

    def total_for(chapter_id: int) -> int:
        if chapter_id in leaf_targets:
            return leaf_targets[chapter_id]
        return sum(total_for(child_id) for child_id in children.get(chapter_id, []))

    return {int(row["chapter_id"]): total_for(int(row["chapter_id"])) for row in rows}


def _children_map(rows: list[Any]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(int(row["chapter_id"]))
    return children


def _descendant_leaf_ids(chapter_id: int, children: dict[int, list[int]], leaf_set: set[int]) -> list[int]:
    if chapter_id in leaf_set:
        return [chapter_id]
    leaves: list[int] = []
    for child_id in children.get(chapter_id, []):
        leaves.extend(_descendant_leaf_ids(child_id, children, leaf_set))
    return leaves


def _rebuild_catalog_content(conn, version_id: int) -> None:
    rows = conn.execute(
        "SELECT * FROM bid_catalog_chapters WHERE version_id = ? ORDER BY level ASC, parent_id ASC, sort_order ASC, chapter_id ASC",
        (version_id,),
    ).fetchall()
    nodes: dict[int, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for row in rows:
        node = {
            "chapter_id": int(row["chapter_id"]),
            "title": row["chapter_title"],
            "level": int(row["level"]),
            "target_word_count": int(row["target_word_count"] or 0),
            "children": [],
        }
        nodes[int(row["chapter_id"])] = node
    for row in rows:
        node = nodes[int(row["chapter_id"])]
        parent_id = int(row["parent_id"])
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    conn.execute(
        "UPDATE bid_catalog_versions SET catalog_content = ?, update_time = ? WHERE version_id = ?",
        (json.dumps(roots, ensure_ascii=False), storage.now_utc().isoformat(), version_id),
    )


def calculate_and_assign_word_count(bid_id: int, version_id: int | None = None, *, preserve_manual: bool = True) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    with storage.connect() as conn:
        version = _select_version(conn, bid_id, version_id)
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        rows = _fetch_rows(conn, bid_id, int(version["version_id"]))
        if not rows:
            raise BidWordCountServiceError(40001, "目录章节为空，无法分配字数")

        total_target = int(bid["page_count"] or 200) * WORDS_PER_PAGE
        buffer_words = round(total_target * FLOAT_BUFFER_RATIO)
        base_total = total_target - buffer_words
        score_keywords = _parse_scoring_keywords(parse["parsed_score_requirements"] if parse else "")
        rows_by_id = {int(row["chapter_id"]): row for row in rows}
        leaves = _leaf_ids(rows)
        leaf_set = set(leaves)
        children = _children_map(rows)
        manual_targets: dict[int, int] = {}
        manual_parent_targets: dict[int, int] = {}
        assignable_leaves: list[int] = []
        blocked_by_manual_parent: set[int] = set()
        for row in rows:
            chapter_id = int(row["chapter_id"])
            existing_target = int(row["target_word_count"] or 0)
            is_manual = int(row["target_word_count_manual"] or 0) == 1
            if preserve_manual and is_manual and existing_target > 0 and chapter_id not in leaf_set:
                manual_parent_targets[chapter_id] = existing_target
                blocked_by_manual_parent.update(_descendant_leaf_ids(chapter_id, children, leaf_set))
        for leaf_id in leaves:
            row = rows_by_id[leaf_id]
            existing_target = int(row["target_word_count"] or 0)
            is_manual = int(row["target_word_count_manual"] or 0) == 1
            if leaf_id in blocked_by_manual_parent:
                continue
            if preserve_manual and is_manual and existing_target > 0:
                manual_targets[leaf_id] = existing_target
            else:
                assignable_leaves.append(leaf_id)

        for parent_id, parent_target in manual_parent_targets.items():
            child_leaves = _descendant_leaf_ids(parent_id, children, leaf_set)
            if not child_leaves:
                continue
            weighted = {
                leaf_id: _chapter_weight(" ".join(_ancestor_titles(rows_by_id, rows_by_id[leaf_id])), score_keywords)
                for leaf_id in child_leaves
            }
            weight_sum = sum(weighted.values()) or len(weighted)
            raw_children = {leaf_id: parent_target * weight / weight_sum for leaf_id, weight in weighted.items()}
            manual_targets.update(_round_to_total(raw_children, parent_target))

        remaining_total = max(0, total_target - sum(manual_targets.values()))
        raw: dict[int, float] = {}
        if assignable_leaves:
            weighted: dict[int, float] = {}
            for leaf_id in assignable_leaves:
                row = rows_by_id[leaf_id]
                path_title = " ".join(_ancestor_titles(rows_by_id, row))
                weighted[leaf_id] = _chapter_weight(path_title, score_keywords)
            weight_sum = sum(weighted.values()) or len(weighted)
            for leaf_id, weight in weighted.items():
                raw[leaf_id] = remaining_total * weight / weight_sum
        assigned = {**manual_targets, **_round_to_total(raw, remaining_total)}
        # If minimum guards push the total away from target, tolerate <=2% where possible.
        if assigned and abs(sum(assigned.values()) - total_target) > max(1, round(total_target * 0.02)):
            assigned = _round_to_total({key: float(value) for key, value in assigned.items()}, total_target)

        all_targets = _aggregate_parent_targets(rows, assigned)
        for chapter_id, target in all_targets.items():
            manual = 1 if chapter_id in manual_targets or chapter_id in manual_parent_targets else int(rows_by_id[chapter_id]["target_word_count_manual"] or 0)
            conn.execute(
                "UPDATE bid_catalog_chapters SET target_word_count = ?, target_word_count_manual = ? WHERE chapter_id = ?",
                (int(target), manual, chapter_id),
            )
        _rebuild_catalog_content(conn, int(version["version_id"]))
        conn.execute("UPDATE bids SET total_target_word_count = ?, update_time = ? WHERE bid_id = ?", (total_target, storage.now_utc().isoformat(), bid_id))
        conn.commit()

    return get_word_count_summary(bid_id, int(version["version_id"]))


def get_word_count_summary(bid_id: int, version_id: int | None = None) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    with storage.connect() as conn:
        version = _select_version(conn, bid_id, version_id)
        rows = conn.execute(
            """
            SELECT c.chapter_id, c.version_id, c.parent_id, c.chapter_title, c.level, c.sort_order,
                   COALESCE(c.target_word_count, 800) AS target_word_count,
                   COALESCE(c.target_word_count_manual, 0) AS target_word_count_manual,
                   COALESCE(bc.word_count, 0) AS actual_word_count,
                   COALESCE(bc.status, 'unwritten') AS content_status
            FROM bid_catalog_chapters c
            LEFT JOIN bid_chapter_contents bc
              ON bc.bid_id = c.bid_id AND bc.version_id = c.version_id AND bc.chapter_id = c.chapter_id
            WHERE c.bid_id = ? AND c.version_id = ?
            ORDER BY c.level ASC, c.parent_id ASC, c.sort_order ASC, c.chapter_id ASC
            """,
            (bid_id, int(version["version_id"])),
        ).fetchall()
    nodes = [
        {
            "chapter_id": int(row["chapter_id"]),
            "version_id": int(row["version_id"]),
            "parent_id": int(row["parent_id"]),
            "title": row["chapter_title"],
            "level": int(row["level"]),
            "sort_order": int(row["sort_order"]),
            "target_word_count": int(row["target_word_count"] or 800),
            "actual_word_count": int(row["actual_word_count"] or 0),
            "target_word_count_manual": bool(row["target_word_count_manual"]),
            "content_status": row["content_status"],
        }
        for row in rows
    ]
    total_target = int(bid["total_target_word_count"] or (int(bid["page_count"] or 200) * WORDS_PER_PAGE))
    leaf_parent_ids = {node["parent_id"] for node in nodes if node["parent_id"]}
    leaves = [node for node in nodes if node["chapter_id"] not in leaf_parent_ids]
    actual_total = sum(node["actual_word_count"] for node in leaves)
    assigned_total = sum(node["target_word_count"] for node in leaves)
    progress = round(actual_total / total_target * 100) if total_target else 0
    return {
        "bid_id": bid_id,
        "version_id": int(version["version_id"]),
        "version_name": version["version_name"],
        "page_count": int(bid["page_count"] or 200),
        "words_per_page": WORDS_PER_PAGE,
        "total_target_word_count": total_target,
        "assigned_target_word_count": assigned_total,
        "actual_word_count": actual_total,
        "progress": min(progress, 999),
        "chapters": nodes,
    }


def update_chapter_target_word_count(bid_id: int, chapter_id: int, target_word_count: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    if target_word_count < MIN_LEAF_WORDS:
        raise BidWordCountServiceError(40001, f"单节目标字数不能低于{MIN_LEAF_WORDS}字")
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id)).fetchone()
        if row is None:
            raise BidWordCountServiceError(40401, "目录章节不存在", 404)
        conn.execute(
            "UPDATE bid_catalog_chapters SET target_word_count = ?, target_word_count_manual = 1 WHERE chapter_id = ?",
            (target_word_count, chapter_id),
        )
        _rebuild_catalog_content(conn, int(row["version_id"]))
        conn.commit()
    return get_word_count_summary(bid_id, int(row["version_id"]))
