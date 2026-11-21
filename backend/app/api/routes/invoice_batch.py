from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.services import invoice_batch_service as service

router = APIRouter(prefix='/invoice/batches')


def ok(data: Any): return {'code': 0, 'message': 'success', 'data': data}


class FieldsUpdate(BaseModel):
    revision: int = Field(ge=0)
    fields: dict[str, str]


@router.get('')
async def list_batches(): return ok(service.list_batches())


@router.post('')
async def create_batch(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...)):
    if not files or len(files) > service.MAX_FILES: raise HTTPException(400, '每批请选择 1–30 个文件')
    collected = []
    total = 0
    for file in files:
        filename = file.filename or '未命名文件'
        suffix = Path(filename.replace('\\','/')).suffix.lower()
        data = await file.read(service.MAX_FILE + 1)
        total += len(data)
        if total > service.MAX_BATCH: raise HTTPException(413, '单批文件总大小不能超过 100 MB')
        error = '不支持的文件类型' if suffix not in service.ALLOWED else '文件为空' if not data else '文件超过 20 MB' if len(data) > service.MAX_FILE else ''
        collected.append((filename, data if not error else b'', error))
    batch_id = service.create_batch(collected)
    background.add_task(service.process_batch, batch_id, request.state.tenant_context)
    return ok(service.get_batch(batch_id))


@router.get('/{batch_id}')
async def batch_details(batch_id: str): return ok(service.get_batch(batch_id))


@router.post('/{batch_id}/resume')
async def resume_batch(batch_id: str, request: Request, background: BackgroundTasks):
    service.get_batch(batch_id)
    background.add_task(service.process_batch,batch_id,request.state.tenant_context)
    return ok({'queued': True})


@router.post('/{batch_id}/items/{item_id}/retry')
async def retry(batch_id: str, item_id: str, request: Request, background: BackgroundTasks):
    batch = service.get_batch(batch_id)
    item = next((x for x in batch['items'] if x['id'] == item_id), None)
    if not item: raise HTTPException(404, '发票不存在或无权访问')
    if item['ocr_status'] not in {'failed','pending','processing'}: raise HTTPException(409, '已完成的记录不能重复识别')
    if item['ocr_status'] == 'failed' and ('文件类型' in item['error'] or '文件超过' in item['error'] or '文件为空' in item['error']):
        raise HTTPException(400, '原文件未保存，请重新上传')
    background.add_task(service.process_batch,batch_id,request.state.tenant_context,item_id)
    return ok({'queued': True})


@router.put('/{batch_id}/items/{item_id}/fields')
async def save_fields(batch_id: str, item_id: str, payload: FieldsUpdate):
    if len(payload.fields) > 30 or any(len(k) > 100 or len(v) > 2000 for k,v in payload.fields.items()): raise HTTPException(400,'字段内容过长')
    return ok(service.update_fields(batch_id,item_id,payload.fields,payload.revision))


@router.post('/{batch_id}/items/{item_id}/verification')
async def save_verification(batch_id: str, item_id: str, status: str = Form(...), note: str = Form(default=''), revision: int = Form(...), evidence: UploadFile | None = File(default=None)):
    data = await evidence.read(10 * 1024 * 1024 + 1) if evidence else None
    if data is not None and (not data or len(data) > 10 * 1024 * 1024): raise HTTPException(400,'截图为空或超过 10 MB')
    return ok(service.verify(batch_id,item_id,status,note,data,revision))


@router.get('/{batch_id}/items/{item_id}/history')
async def history(batch_id: str, item_id: str): return ok(service.get_history(batch_id,item_id))


@router.get('/{batch_id}/items/{item_id}/history/{event_id}/evidence')
async def history_evidence(batch_id: str, item_id: str, event_id: str):
    from PIL import Image
    path = service.get_history_evidence(batch_id,item_id,event_id)
    with Image.open(path) as image: mime = Image.MIME.get(image.format, 'application/octet-stream')
    return FileResponse(path,media_type=mime,headers={'Cache-Control':'private, no-store'})


@router.get('/{batch_id}/items/{item_id}/source')
async def source_file(batch_id: str, item_id: str):
    path,name = service.get_file(batch_id,item_id)
    return FileResponse(path,filename=name,headers={'Cache-Control':'private, no-store'})


@router.get('/{batch_id}/items/{item_id}/evidence')
async def evidence_file(batch_id: str, item_id: str):
    from PIL import Image
    path,_ = service.get_file(batch_id,item_id,True)
    with Image.open(path) as image: mime = Image.MIME.get(image.format, 'application/octet-stream')
    return FileResponse(path,media_type=mime,headers={'Cache-Control':'private, no-store'})


@router.get('/{batch_id}/report.csv')
async def report(batch_id: str):
    return Response(content=service.report_csv(batch_id),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="invoice-report.csv"','Cache-Control':'private, no-store'})
