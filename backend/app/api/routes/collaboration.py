from typing import Literal
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from app.services import chapter_collaboration as service

router = APIRouter(prefix="/bid", tags=["collaboration"])

class Revision(BaseModel):
    expected_revision: int = Field(ge=0)

class Assignment(Revision):
    assigned_user_id: int | None = None
    reviewer_user_id: int | None = None

class Workflow(Revision):
    action: Literal["submit", "approve", "reject", "reopen"]
    note: str = Field(default="", max_length=2000)

class Comment(Revision):
    body: str = Field(min_length=1, max_length=4000)
    quote: str = Field(default="", max_length=2000)

class Restore(Revision):
    revision: int = Field(ge=0)


def ok(data):
    return {"code": 0, "message": "success", "data": data}

@router.get("/collaboration/members")
def members():
    return ok(service.members())

@router.get("/{bid_id}/chapters/{chapter_id}/collaboration")
def detail(bid_id: int, chapter_id: int):
    return ok(service.detail(bid_id, chapter_id))

@router.put("/{bid_id}/chapters/{chapter_id}/collaboration/assignment")
def assign(bid_id: int, chapter_id: int, payload: Assignment):
    return ok(service.assign(bid_id, chapter_id, payload))

@router.post("/{bid_id}/chapters/{chapter_id}/collaboration/workflow")
def workflow(bid_id: int, chapter_id: int, payload: Workflow):
    return ok(service.transition(bid_id, chapter_id, payload))

@router.post("/{bid_id}/chapters/{chapter_id}/collaboration/comments")
def comment(bid_id: int, chapter_id: int, payload: Comment):
    return ok(service.add_comment(bid_id, chapter_id, payload))

@router.post("/{bid_id}/chapters/{chapter_id}/collaboration/comments/{comment_id}/resolve")
def resolve(bid_id: int, chapter_id: int, comment_id: int, payload: Revision):
    return ok(service.resolve(bid_id, chapter_id, comment_id, payload))

@router.get("/{bid_id}/chapters/{chapter_id}/collaboration/compare")
def compare(bid_id: int, chapter_id: int, left: int = Query(ge=0), right: int = Query(ge=0)):
    return ok(service.compare(bid_id, chapter_id, left, right))

@router.post("/{bid_id}/chapters/{chapter_id}/collaboration/restore")
def restore(bid_id: int, chapter_id: int, payload: Restore):
    return ok(service.restore(bid_id, chapter_id, payload))
