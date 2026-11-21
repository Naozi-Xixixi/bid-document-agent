"""Repair historical perceptual-dedup pointers using the original PDF image metadata."""
from collections import defaultdict
import hashlib
from app.services import storage


def restore_pdf_sources(item_id):
    from . import service as s
    with storage.connect() as conn:
        item = conn.execute("SELECT original_file_path FROM knowledge_item WHERE item_id=?", (item_id,)).fetchone()
        assets = [dict(r) for r in conn.execute("SELECT * FROM knowledge_asset WHERE item_id=? AND duplicate_of_asset_id IS NOT NULL AND page_num IS NOT NULL AND COALESCE(source_chapter,'') NOT LIKE '扫描页全文%'", (item_id,))]
    if not item or not assets or not str(item["original_file_path"]).lower().endswith('.pdf'):
        return 0
    path = s._resolve_stored_path(item["original_file_path"])
    if not path or not path.exists() or s.fitz is None:
        raise RuntimeError('原 PDF 不可用，无法核对历史去重图片')
    grouped = defaultdict(list)
    for asset in assets:
        grouped[int(asset['page_num'])].append(asset)
    restored = 0
    with s.fitz.open(str(path)) as doc:
        for number, rows in grouped.items():
            candidates = defaultdict(list)
            for index, image in enumerate(doc[number-1].get_images(full=True),1):
                data=doc.extract_image(image[0]);content=data.get('image') or b''
                profile=s._image_profile_from_bytes(content)
                key=(profile.get('hash'),profile.get('width'),profile.get('height'))
                candidates[key].append((index,data.get('ext','png'),content))
            for asset in rows:
                matched=candidates.get((asset['image_hash'],asset['image_width'],asset['image_height']),[])
                if not matched:
                    raise RuntimeError(f"第{number}页图片#{asset['asset_id']}无法匹配原图，需人工核对")
                if len({hashlib.sha256(entry[2]).hexdigest() for entry in matched})>1:
                    raise RuntimeError(f"第{number}页存在多个相似但不同的原图，不能自动替换")
                index,ext,content=matched[0]
                target=path.parent/'assets'/f'page{number}_image{index}.{ext}'
                target.write_bytes(content)
                with storage.connect() as conn:
                    conn.execute("UPDATE knowledge_asset SET resource_path=?,duplicate_of_asset_id=NULL,ocr_status='not_started',deep_status='pending',ocr_text='' WHERE asset_id=?",
                                 (s._stored_path(target),asset['asset_id']))
                    conn.commit()
                restored+=1
    return restored


def restore_docx_sources(item_id):
    """Resolve legacy visual-dedup pointers to unambiguous original DOCX media."""
    import zipfile
    from pathlib import Path
    from . import service as s
    with storage.connect() as conn:
        item = conn.execute("SELECT original_file_path FROM knowledge_item WHERE item_id=?", (item_id,)).fetchone()
        all_assets = [dict(r) for r in conn.execute("SELECT * FROM knowledge_asset WHERE item_id=? ORDER BY asset_id", (item_id,))]
        assets = [a for a in all_assets if a["duplicate_of_asset_id"] is not None]
    if not item or not assets or not str(item["original_file_path"]).lower().endswith('.docx'):
        return 0
    path = s._resolve_stored_path(item['original_file_path'])
    if not path or not path.exists():
        raise RuntimeError('原 Word 不可用，无法核对历史去重图片')
    contexts = s._docx_media_contexts(path)
    candidates = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        media = [n for n in archive.namelist() if n.startswith('word/media/')]
        ordered = []
        seen = set()
        for index, name in enumerate(media, 1):
            content = archive.read(name)
            if not content:
                continue
            profile = s._image_profile_from_bytes(content)
            key = (profile.get('hash'), profile.get('width'), profile.get('height'))
            digest = hashlib.sha256(content).hexdigest()
            candidates[key].append((index, name, digest))
            if digest not in seen and not profile.get("decorative"):
                ordered.append((key, (index, name, digest)))
            seen.add(digest)
        # Old imports inserted unique non-decorative media in ZIP order. Use that
        # order only when the entire sequence and every surviving filename agree.
        ordered_map = {}
        if len(ordered) == len(all_assets) and all(
            (a['image_hash'],a['image_width'],a['image_height']) == entry[0]
            and (a['duplicate_of_asset_id'] is not None or Path(str(a['resource_path']).replace(chr(92),'/')).name == f"docx_image{entry[1][0]}{Path(entry[1][1]).suffix or '.png'}")
            for a,entry in zip(all_assets,ordered)
        ):
            ordered_map = {a['asset_id']:entry[1] for a,entry in zip(all_assets,ordered)}
        planned = []
        for asset in assets:
            matched = [ordered_map[asset['asset_id']]] if asset['asset_id'] in ordered_map else candidates.get((asset['image_hash'], asset['image_width'], asset['image_height']), [])
            if len({entry[2] for entry in matched}) > 1:
                matched = [entry for entry in matched if contexts.get(entry[1], {}).get('source_chapter') == asset['source_chapter']]
            if not matched or len({entry[2] for entry in matched}) > 1:
                raise RuntimeError(f"Word 图片#{asset['asset_id']}无法唯一匹配原图，不能自动替换")
            planned.append((asset, matched[0]))
        target_dir = path.parent / 'assets'
        target_dir.mkdir(exist_ok=True)
        for asset, (index, name, digest) in planned:
            target = target_dir / f'docx_image{index}{Path(name).suffix or ".png"}'
            target.write_bytes(archive.read(name))
            with storage.connect() as conn:
                conn.execute("UPDATE knowledge_asset SET resource_path=?,duplicate_of_asset_id=NULL,ocr_status='not_started',deep_status='pending',ocr_text='' WHERE asset_id=?", (s._stored_path(target),asset['asset_id']))
                conn.commit()
    return len(planned)
