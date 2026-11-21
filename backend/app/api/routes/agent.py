from contextlib import aclosing, nullcontext

from anyio import CancelScope

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.schemas.tender import AgentChatRequest, AgentChatResponse
from app.services import storage, tender_agent
from app.services.project_activity import ActivityConflict, project_request
from app.services.wordagent_protocol import done, error, sse

router = APIRouter()


class _ClosingStreamingResponse(StreamingResponse):
    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            # Disconnects can cancel socket send while the iterator is paused
            # at yield. Explicitly close it in that same task so leases and the
            # provider stream are released before the response finishes.
            with CancelScope(shield=True):
                await self.body_iterator.aclose()


def _load_project(project_id: str | None):
    if not project_id:
        return None
    try:
        return storage.load_project(project_id)
    except FileNotFoundError:
        return None


@router.post("/agent/chat", response_model=AgentChatResponse)
async def chat(payload: AgentChatRequest):
    try:
        with project_request(payload.projectId) if payload.projectId else nullcontext():
            project = _load_project(payload.projectId)
            message, document, events = await tender_agent.chat_once(payload.message, payload.mode, project)
            return AgentChatResponse(message=message, document=document, events=events)
    except ActivityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/agent/chat/stream")
async def chat_stream(payload: AgentChatRequest):
    async def protected_stream():
        try:
            with project_request(payload.projectId) if payload.projectId else nullcontext():
                project = _load_project(payload.projectId)
                async with aclosing(tender_agent.stream_chat(payload.message, payload.mode, project)) as stream:
                    async for event in stream:
                        yield sse(event)
        except ActivityConflict as exc:
            yield sse(error(str(exc)))
            yield sse(done())
    return _ClosingStreamingResponse(protected_stream(), media_type="text/event-stream")


@router.websocket("/agent/ws")
@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") == "stop":
                await websocket.send_json(done())
                continue
            if payload.get("type") != "chat":
                await websocket.send_json(error("Unsupported message type"))
                await websocket.send_json(done())
                continue
            project_id = str(payload.get("projectId") or payload.get("project_id") or "")
            try:
                with project_request(project_id) if project_id else nullcontext():
                    project = _load_project(project_id)
                    stream = tender_agent.stream_chat(str(payload.get("message") or ""), str(payload.get("mode") or "agent"), project)
                    async with aclosing(stream):
                        async for item in stream:
                            await websocket.send_json(item)
            except ActivityConflict as exc:
                await websocket.send_json(error(str(exc)))
                await websocket.send_json(done())
    except WebSocketDisconnect:
        return
