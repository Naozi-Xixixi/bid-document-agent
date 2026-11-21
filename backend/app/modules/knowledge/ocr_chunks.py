"""Index verbatim OCR separately from inferred image labels, with stable source IDs."""
from app.services import storage


def refresh_ocr_chunks(item_id):
    from . import service as s
    project_id, role = s._document_rag_metadata(item_id)
    stale = []
    with storage.connect() as conn:
        rows = conn.execute("SELECT asset_id,ocr_text,page_num,source_chapter FROM knowledge_asset WHERE item_id=? AND ocr_status='recognized' ORDER BY asset_id", (item_id,)).fetchall()
        old = {r["section_title"]: dict(r) for r in conn.execute("SELECT * FROM knowledge_chunk WHERE item_id=? AND chunk_kind='ocr_text'", (item_id,))}
        seen_texts = set()
        for asset in rows:
            text = str(asset["ocr_text"] or "").strip()
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            for index, part in enumerate(s._window_text(text, 1000, 120)) if text else []:
                title = f"图片OCR#{asset['asset_id']}/{index+1}"
                path = f"{asset['source_chapter'] or '扫描资料'} / 图片#{asset['asset_id']}"
                previous = old.pop(title, None)
                if previous and previous["content"] == part and previous["page_number"] == asset["page_num"] and previous["section_path"] == path:
                    continue
                if previous:
                    stale.append(previous["chunk_id"])
                    conn.execute("DELETE FROM knowledge_chunk WHERE chunk_id=?", (previous["chunk_id"],))
                tokens = s._tokens(part)
                conn.execute("""INSERT INTO knowledge_chunk
                    (item_id,chunk_index,section_title,page_number,content,tokens_json,embedding_json,
                     project_id,doc_role,chunk_kind,section_path,source_asset_id,create_time)
                    VALUES (?,?,?,?,?,?,?,?,?,'ocr_text',?,?,?)""",
                    (item_id, 2000000 + int(asset["asset_id"]) * 1000 + index, title, asset["page_num"], part,
                     s._json(tokens), s._json(s._hash_embedding(tokens)), project_id, role, path, asset["asset_id"], s._now()))
        for previous in old.values():
            stale.append(previous["chunk_id"])
            conn.execute("DELETE FROM knowledge_chunk WHERE chunk_id=?", (previous["chunk_id"],))
        conn.commit()
    if stale:
        s.vector_store.delete_knowledge_chunks(stale)
