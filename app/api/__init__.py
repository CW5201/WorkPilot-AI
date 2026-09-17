"""FastAPI 接口层。

端点：
  POST /tasks                 创建任务（自然语言），后台异步执行
  GET  /tasks/{id}           查任务状态/计划/产物
  POST /tasks/{id}/resume    服务重启恢复：继续执行未完成任务（幂等）
  POST /tasks/{id}/approve   HITL 第二层：批准/拒绝高风险操作
  GET  /tasks/{id}/trace     Trace Timeline
  GET  /tasks/{id}/artifacts 产物列表
  GET  /tasks                 任务列表
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..engine.orchestrator import get_orchestrator
from ..models import TaskRecord, TaskStatus
from ..persistence import get_task_store
from ..trace import Tracer
from ..tools.registry import ensure_tools

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ------------------------------------------------------------------ schemas
class TaskCreate(BaseModel):
    instruction: str = Field(..., min_length=1,
                             description="自然语言任务，例如：汇总本周5份部门周报…")
    user_id: str = "default_user"
    deliverable: str = "markdown"  # markdown/ppt/excel/email/summary


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = ""


class ResumeReq(BaseModel):
    auto_approve: bool = True
    deliverable: str = "markdown"


def _task_view(task: TaskRecord) -> dict:
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "instruction": task.instruction,
        "plan_steps": ([{"step_id": s.step_id, "action": s.action, "tool": s.tool,
                         "input": s.input, "depends_on": s.depends_on,
                         "description": s.description} for s in task.plan.steps]
                       if task.plan else []),
        "clarification_needed": task.plan.clarification_needed if task.plan else [],
        "pending_approval": task.pending_approval.model_dump()
                            if task.pending_approval else None,
        "final_result": task.final_result.model_dump()
                        if task.final_result else None,
        "retry_counts": task.retry_counts,
        "replan_count": task.replan_count,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _run_async(task: TaskRecord, store, deliverable: str) -> None:
    """后台线程执行，避免阻塞请求；结果落 checkpoint。"""
    ensure_tools()
    orch = get_orchestrator()
    tracer = Tracer(task.task_id)
    outcome = orch.run(task, tracer=tracer, deliverable=deliverable)
    store.checkpoint(task)


# ------------------------------------------------------------------ routes
@router.post("", response_model=dict, status_code=202)
@router.post("/")
def create_task(body: TaskCreate, bg: BackgroundTasks) -> dict:
    orch = get_orchestrator()
    task = orch.create_task(body.user_id, body.instruction)
    task.deliverable = body.deliverable
    orch.store.save(task, force=True)
    store = get_task_store()
    bg.add_task(_run_async, task, store, body.deliverable)
    return {"task_id": task.task_id, "status": task.status.value,
            "note": "已创建，后台执行中", "deliverable": body.deliverable}


@router.get("/{task_id}", response_model=dict)
def get_task(task_id: str) -> dict:
    store = get_task_store()
    task = store.load(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return _task_view(task)


@router.post("/{task_id}/resume", response_model=dict)
def resume_task(task_id: str, body: ResumeReq) -> dict:
    """服务重启后恢复未完成任务（幂等）。"""
    store = get_task_store()
    task = store.load(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.HUMAN_HANDOFF}
    if task.status in terminal:
        return _task_view(task)
    orch = get_orchestrator()
    tracer = Tracer(task.task_id)
    outcome = orch.run(task, tracer=tracer, deliverable=body.deliverable)
    task.deliverable = body.deliverable
    if outcome.status == TaskStatus.WAITING_APPROVAL:
        store.checkpoint(task)
    return _task_view(task)


@router.post("/{task_id}/approve", response_model=dict)
def approve(task_id: str, body: ApprovalDecision) -> dict:
    store = get_task_store()
    task = store.load(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.pending_approval is None:
        raise HTTPException(409, "no pending approval")
    orch = get_orchestrator()
    tracer = Tracer(task.task_id)
    deliverable = task.deliverable
    outcome = orch.approve_and_continue(
        task, task.pending_approval, body.approved, tracer,
        deliverable=deliverable)
    store.checkpoint(task)
    return _task_view(task)


@router.get("/{task_id}/trace", response_model=dict)
def get_trace(task_id: str) -> dict:
    tracer = Tracer(task_id)
    events = tracer.load_events()
    return {"task_id": task_id,
            "events": [e.model_dump() for e in events],
            "timeline": tracer.timeline()}


@router.get("/{task_id}/artifacts", response_model=dict)
def get_artifacts(task_id: str) -> dict:
    from ..artifacts import get_artifact_store
    arts = get_artifact_store().list_for_task(task_id)
    return {"task_id": task_id,
            "artifacts": [{"result_id": a.result_id,
                           "type": a.artifact_type.name,
                           "path": a.storage_path,
                           "summary": a.summary,
                           "key_entities": a.key_entities} for a in arts]}


@router.get("", response_model=list)
@router.get("/")
def list_tasks(limit: int = 50) -> list[dict]:
    store = get_task_store()
    return [{"task_id": t.task_id, "status": t.status.value,
             "instruction": t.instruction[:60]}
            for t in store.list_tasks()[:limit]]
