"""Company-local invoice batches, verification records and append-only history."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException, UploadFile
from app.core.config import settings
from app.core.tenant_context import get_current_tenant, set_current_tenant, reset_current_tenant
from app.services import storage

MAX_FILE = 20 * 1024 * 1024
MAX_BATCH = 100 * 1024 * 1024
MAX_FILES = 30
ALLOWED = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.pdf'}


def ensure_schema():
    with closing(storage.connect()) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS invoice_batch (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS invoice_batch_item (
            id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, filename TEXT NOT NULL, file_path TEXT NOT NULL,
            ocr_status TEXT NOT NULL, error TEXT NOT NULL, result_json TEXT NOT NULL,
            verification_status TEXT NOT NULL, verification_note TEXT NOT NULL, evidence_path TEXT NOT NULL,
            verified_by TEXT NOT NULL, verified_at TEXT NOT NULL, revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS invoice_batch_event (
            id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, item_id TEXT NOT NULL, action TEXT NOT NULL,
            actor TEXT NOT NULL, created_at TEXT NOT NULL, snapshot_json TEXT NOT NULL)''')
        conn.commit()


def _now(): return storage.now_utc().isoformat()
def _actor():
    tenant = get_current_tenant()
    if tenant is None: raise HTTPException(401, '请先登录')
    return tenant.username


def _directory():
    return settings.upload_dir / 'invoice-batches'


def _row(conn, batch_id, item_id=None):
    batch = conn.execute('SELECT * FROM invoice_batch WHERE id = ?', (batch_id,)).fetchone()
    if not batch: raise HTTPException(404, '批次不存在或无权访问')
    if item_id is None: return batch
    item = conn.execute('SELECT * FROM invoice_batch_item WHERE batch_id = ? AND id = ?', (batch_id, item_id)).fetchone()
    if not item: raise HTTPException(404, '发票不存在或无权访问')
    return item


def _item(row):
    result = json.loads(row['result_json'] or '{}')
    return {'id': row['id'], 'batch_id': row['batch_id'], 'filename': row['filename'], 'ocr_status': row['ocr_status'],
            'error': row['error'], 'result': result, 'verification_status': row['verification_status'],
            'verification_note': row['verification_note'], 'has_evidence': bool(row['evidence_path']),
            'verified_by': row['verified_by'], 'verified_at': row['verified_at'], 'revision': row['revision'],
            'created_at': row['created_at'], 'updated_at': row['updated_at']}


def _event(conn, row, action, actor):
    # Include the previous or new state, including evidence path, for traceability.
    data = dict(row)
    conn.execute('INSERT INTO invoice_batch_event (id,batch_id,item_id,action,actor,created_at,snapshot_json) VALUES (?,?,?,?,?,?,?)',
                 (uuid.uuid4().hex, data['batch_id'], data['id'], action, actor, _now(), json.dumps(data, ensure_ascii=False)))


def create_batch(files):
    ensure_schema()
    batch_id, now, actor = uuid.uuid4().hex, _now(), _actor()
    folder = _directory() / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    with closing(storage.connect()) as conn:
        conn.execute('INSERT INTO invoice_batch (id,title,created_by,created_at) VALUES (?,?,?,?)',
                     (batch_id, '发票批次 ' + now[:10], actor, now))
        for filename, data, error in files:
            name = Path(filename.replace('\\', '/')).name[:255] or '未命名文件'
            suffix = Path(name).suffix.lower()
            path = ''
            if not error:
                path = str(folder / (uuid.uuid4().hex + suffix))
                Path(path).write_bytes(data)
            item_id = uuid.uuid4().hex
            conn.execute('''INSERT INTO invoice_batch_item
                (id,batch_id,filename,file_path,ocr_status,error,result_json,verification_status,verification_note,evidence_path,verified_by,verified_at,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (item_id,batch_id,name,path,'failed' if error else 'pending',error,'{}','manual_pending','','','','',0,now,now))
        conn.commit()
    return batch_id


def list_batches():
    ensure_schema()
    with closing(storage.connect()) as conn:
        rows = conn.execute('SELECT * FROM invoice_batch ORDER BY created_at DESC LIMIT 100').fetchall()
        return [dict(row) for row in rows]


def get_batch(batch_id):
    ensure_schema()
    with closing(storage.connect()) as conn:
        batch = dict(_row(conn, batch_id))
        rows = conn.execute('SELECT * FROM invoice_batch_item WHERE batch_id = ? ORDER BY created_at, id', (batch_id,)).fetchall()
    items = [_item(row) for row in rows]
    summary = {'total': len(items), 'pending': sum(i['ocr_status'] in {'pending', 'processing'} for i in items),
               'ocr_failed': sum(i['ocr_status'] == 'failed' for i in items),
               'passed': sum(i['verification_status'] == 'passed' for i in items),
               'failed': sum(i['verification_status'] == 'failed' for i in items),
               'review': sum(i['verification_status'] == 'review' for i in items),
               'unverified': sum(i['verification_status'] == 'manual_pending' for i in items)}
    return {**batch, 'items': items, 'summary': summary}


def process_item(batch_id, item_id):
    from app.api.routes.invoice import ocr_invoice
    with closing(storage.connect()) as conn:
        row = _row(conn, batch_id, item_id)
        if row['ocr_status'] not in {'pending', 'failed', 'processing'} or not row['file_path']: return
        revision = int(row['revision']) + 1
        changed = conn.execute('UPDATE invoice_batch_item SET ocr_status=?,error=?,revision=?,updated_at=? WHERE id=? AND revision=?',
                               ('processing','',revision,_now(),item_id,row['revision'])).rowcount
        conn.commit()
        if not changed: return
        path, filename = row['file_path'], row['filename']
    try:
        raw = Path(path).read_bytes()
        response = asyncio.run(ocr_invoice(UploadFile(filename=filename, file=io.BytesIO(raw))))
        if not isinstance(response, dict) or response.get('code') != 0:
            detail = json.loads(response.body).get('message', '识别失败') if hasattr(response, 'body') else '识别失败'
            raise ValueError(detail)
        result = response['data']
        if result.get('status') != 'ocr_completed': raise ValueError('未识别出文字，请检查文件或 OCR 后重试')
        status, error, result_json = 'completed', '', json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        status, error, result_json = 'failed', str(exc)[:500], '{}'
    with closing(storage.connect()) as conn:
        conn.execute('UPDATE invoice_batch_item SET ocr_status=?,error=?,result_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND ocr_status=?',
                     (status,error,result_json,_now(),item_id,revision,'processing'))
        conn.commit()


def process_batch(batch_id, tenant, item_id=None):
    token = set_current_tenant(tenant)
    try:
        ensure_schema()
        with closing(storage.connect()) as conn:
            _row(conn, batch_id)
            rows = conn.execute('SELECT id FROM invoice_batch_item WHERE batch_id = ?' + (' AND id = ?' if item_id else ''),
                                (batch_id,item_id) if item_id else (batch_id,)).fetchall()
        for row in rows: process_item(batch_id, row['id'])
    finally:
        reset_current_tenant(token)


def update_fields(batch_id, item_id, fields, revision):
    from app.api.routes.invoice import _validate_invoice
    ensure_schema()
    with closing(storage.connect()) as conn:
        row = _row(conn, batch_id, item_id)
        if row['ocr_status'] != 'completed': raise HTTPException(409, '请先完成识别')
        if int(row['revision']) != revision: raise HTTPException(409, '记录已变更，请刷新后重试')
        result = json.loads(row['result_json'])
        target = 'receipt_fields' if result.get('document_type') == 'non_tax_receipt' else 'fields'
        previous = result.get(target) or {}
        updated = {**previous, **fields}
        if updated == previous: return _item(row)
        result[target] = updated
        if target == 'fields': result['local_verification'] = _validate_invoice(updated)
        result['external_verification'] = {'status': 'manual_pending', 'message': '字段已修改，请重新查验'}
        _event(conn, row, 'fields_before', _actor())
        count = conn.execute('''UPDATE invoice_batch_item SET result_json=?,verification_status='manual_pending',verification_note='',
            evidence_path='',verified_by='',verified_at='',revision=revision+1,updated_at=? WHERE id=? AND revision=?''',
            (json.dumps(result,ensure_ascii=False),_now(),item_id,revision)).rowcount
        if not count: conn.rollback(); raise HTTPException(409, '记录已变更，请刷新后重试')
        _event(conn, _row(conn,batch_id,item_id), 'fields_changed', _actor())
        conn.commit()
        return _item(_row(conn,batch_id,item_id))


def verify(batch_id, item_id, status, note, evidence, revision):
    ensure_schema()
    if status not in {'passed','failed','review'}: raise HTTPException(400, '查验状态无效')
    if status in {'passed','failed'} and evidence is None: raise HTTPException(400, '记录通过或未通过时请上传官方结果截图')
    evidence_path = ''
    if evidence:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(evidence)) as image:
                if image.format not in {'JPEG','PNG','WEBP','BMP'}: raise ValueError('格式不支持')
                image.verify()
        except Exception: raise HTTPException(400, '截图不是有效的 JPG、PNG、WEBP 或 BMP 图片')
        evidence_path = str(_directory() / batch_id / ('evidence-' + uuid.uuid4().hex + '.img'))
    with closing(storage.connect()) as conn:
        row = _row(conn,batch_id,item_id)
        if row['ocr_status'] != 'completed': raise HTTPException(409,'请先完成识别')
        if int(row['revision']) != revision: raise HTTPException(409,'记录已变更，请刷新后重试')
        result = json.loads(row['result_json'])
        if status in {'passed', 'failed'}:
            if result.get('document_type') == 'non_tax_receipt':
                fields = result.get('receipt_fields') or {}
                if any(not str(fields.get(k) or '').strip() for k in ('receipt_code','receipt_number','verification_code','issue_date','total_amount')):
                    raise HTTPException(400, '请先补齐财政票据的查验字段')
            else:
                from app.api.routes.invoice import _validate_invoice
                if not _validate_invoice(result.get('fields') or {}).get('official_ready'):
                    raise HTTPException(400, '请先补齐并核对官方查验字段')
        if evidence_path: Path(evidence_path).write_bytes(evidence)
        try:
            _event(conn,row,'verification_before',_actor())
            now = _now()
            count = conn.execute('''UPDATE invoice_batch_item SET verification_status=?,verification_note=?,evidence_path=?,verified_by=?,verified_at=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?''',
                                 (status,note[:2000],evidence_path,_actor(),now,now,item_id,revision)).rowcount
            if not count: conn.rollback(); raise HTTPException(409,'记录已变更，请刷新后重试')
            new_row = _row(conn,batch_id,item_id)
            _event(conn,new_row,'manual_verification',_actor())
            conn.commit()
            return _item(new_row)
        except Exception:
            if evidence_path: Path(evidence_path).unlink(missing_ok=True)
            raise


def get_file(batch_id,item_id,evidence=False):
    ensure_schema()
    with closing(storage.connect()) as conn:
        row = _row(conn,batch_id,item_id)
        value = row['evidence_path' if evidence else 'file_path']
        if not value: raise HTTPException(404,'文件不存在')
        path = Path(value).resolve()
        if not path.is_relative_to((_directory() / batch_id).resolve()) or not path.is_file(): raise HTTPException(404,'文件不存在')
        return path, row['filename']


def get_history(batch_id,item_id):
    ensure_schema()
    with closing(storage.connect()) as conn:
        _row(conn,batch_id,item_id)
        rows = conn.execute('SELECT id,action,actor,created_at,snapshot_json FROM invoice_batch_event WHERE batch_id=? AND item_id=? ORDER BY created_at', (batch_id,item_id)).fetchall()
        return [{'id': r['id'], 'action': r['action'], 'actor': r['actor'], 'created_at': r['created_at'], 'snapshot': _item(json.loads(r['snapshot_json']))} for r in rows]


def get_history_evidence(batch_id, item_id, event_id):
    ensure_schema()
    with closing(storage.connect()) as conn:
        _row(conn, batch_id, item_id)
        row = conn.execute('SELECT snapshot_json FROM invoice_batch_event WHERE id=? AND batch_id=? AND item_id=?', (event_id,batch_id,item_id)).fetchone()
        if not row: raise HTTPException(404,'记录不存在或无权访问')
        value = json.loads(row['snapshot_json']).get('evidence_path')
        if not value: raise HTTPException(404,'该记录没有截图')
        path = Path(value).resolve()
        if not path.is_relative_to((_directory() / batch_id).resolve()) or not path.is_file(): raise HTTPException(404,'截图不存在')
        return path


def report_csv(batch_id):
    batch = get_batch(batch_id)
    output = io.StringIO(newline=''); writer = csv.writer(output)
    writer.writerow(['文件名','票据类型','识别状态','识别错误','票据号码','开票日期','不含税金额','价税合计','抬头/交款人','本地检查','人工查验结果','查验备注','操作人','记录时间(UTC)','截图','说明'])
    def safe(v):
        text = str(v or '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text
    ocr_labels = {'pending': '排队中', 'processing': '识别中', 'completed': '识别完成', 'failed': '识别失败'}
    verification_labels = {'manual_pending': '待人工查验', 'passed': '人工记录：通过', 'failed': '人工记录：未通过', 'review': '人工记录：待复核'}
    type_labels = {'vat_invoice': '增值税发票', 'non_tax_receipt': '非税收入票据'}
    for item in batch['items']:
        result = item['result']; fields = result.get('fields') or result.get('receipt_fields') or {}
        writer.writerow([safe(v) for v in [item['filename'],type_labels.get(result.get('document_type'), result.get('document_type')),ocr_labels.get(item['ocr_status']),item['error'],fields.get('invoice_number') or fields.get('receipt_number'),fields.get('issue_date'),fields.get('amount_excluding_tax'),fields.get('total_amount'),fields.get('title') or fields.get('payer'),(result.get('local_verification') or {}).get('status'),verification_labels.get(item['verification_status']),item['verification_note'],item['verified_by'],item['verified_at'],'有' if item['has_evidence'] else '无','人工记录，非系统自动官方核验']])
    return ('\ufeff' + output.getvalue()).encode('utf-8')
