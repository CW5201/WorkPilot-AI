"""Pydantic 领域模型：Plan / Step / TaskState / Artifact / Evaluation / TraceEvent。

设计原则（对应需求第四节）：Agent 间传递 summary + key_entities + result_id，
完整结果外置到 Artifact Store，不塞入下游上下文。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- state machine
class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EVALUATING = "EVALUATING"
    RETRYING = "RETRYING"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


# ---------------------------------------------------------------- Plan
class PlanStep(BaseModel):
    step_id: str
    action: str
    tool: str = ""                      # 空表示该步骤为纯 Agent 决策（如 analysis）
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    description: str = ""

    @classmethod
    def new(cls, action: str, tool: str = "", **kw) -> "PlanStep":
        s = cls(step_id=_new_id("step"), action=action, tool=tool, **kw)
        return s


class Plan(BaseModel):
    task_id: str
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    clarification_needed: list[str] = Field(default_factory=list)  # 缺参时向用户澄清
    assumptions: list[str] = Field(default_factory=list)

    def topological_order(self) -> list[str]:
        """按 depends_on 做拓扑排序；若存在环，退化为原始顺序。"""
        by_id = {s.step_id: s for s in self.steps}
        indeg = {s.step_id: 0 for s in self.steps}
        for s in self.steps:
            for d in s.depends_on:
                if d in by_id:
                    indeg[s.step_id] += 1
        queue = [k for k, v in indeg.items() if v == 0]
        seen: list[str] = []
        while queue:
            cur = queue.pop(0)
            seen.append(cur)
            for s in self.steps:
                if cur in s.depends_on and s.step_id in indeg:
                    indeg[s.step_id] -= 1
                    if indeg[s.step_id] == 0:
                        queue.append(s.step_id)
        if len(seen) != len(self.steps):  # 环或未解析依赖 → 追加剩余
            for s in self.steps:
                if s.step_id not in seen:
                    seen.append(s.step_id)
        return seen


# ---------------------------------------------------------------- Artifact
class ArtifactType(str, Enum):
    FILE = "FILE"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    AGENT_INTERMEDIATE = "AGENT_INTERMEDIATE"
    DOCUMENT = "DOCUMENT"
    PPT = "PPT"
    EXCEL = "EXCEL"
    EMAIL = "EMAIL"
    CALENDAR = "CALENDAR"
    FINAL_DELIVERABLE = "FINAL_DELIVERABLE"


class Artifact(BaseModel):
    result_id: str
    task_id: str
    step_id: str = ""
    artifact_type: ArtifactType
    storage_path: str
    summary: str = ""
    key_entities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    @classmethod
    def new(cls, task_id: str, artifact_type: ArtifactType, storage_path: str,
            summary: str = "", step_id: str = "", **kw) -> "Artifact":
        return cls(result_id=_new_id("result"), task_id=task_id, step_id=step_id,
                  artifact_type=artifact_type, storage_path=storage_path,
                  summary=summary, **kw)


class ResultReference(BaseModel):
    """Agent 间传递的轻量引用（第四节核心）。"""
    result_id: str
    summary: str
    key_entities: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- Evaluation
class ErrorType(str, Enum):
    STEP_ERROR = "STEP_ERROR"
    PLAN_ERROR = "PLAN_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    OUTPUT_ERROR = "OUTPUT_ERROR"
    SAFETY_ERROR = "SAFETY_ERROR"


class SuggestedAction(str, Enum):
    PASS = "PASS"
    RETRY_STEP = "RETRY_STEP"
    REPLAN = "REPLAN"
    HUMAN = "HUMAN"


class Evaluation(BaseModel):
    passed: bool
    error_type: ErrorType | None = None
    failed_step: str | None = None
    reason: str = ""
    suggested_action: SuggestedAction = SuggestedAction.PASS


# ---------------------------------------------------------------- Trace
class TraceEvent(BaseModel):
    trace_id: str
    task_id: str
    step_id: str = ""
    agent_name: str = ""
    event_type: str = "INFO"
    input: Any = None
    output: Any = None
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    status: str = "OK"
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: str = ""
    retry_count: int = 0
    checkpoint_id: str = ""
    timestamp: float = Field(default_factory=time.time)


# ---------------------------------------------------------------- HITL
class Approval(BaseModel):
    task_id: str
    step_id: str
    tool: str
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "HIGH"
    approved: bool | None = None  # None=待审批, True/False=结果
    decided_at: float | None = None
    decision_note: str = ""


# ---------------------------------------------------------------- Task record
class TaskRecord(BaseModel):
    task_id: str
    user_id: str = "default_user"
    instruction: str
    deliverable: str = "markdown"  # 期望交付物类型，随任务持久化
    status: TaskStatus = TaskStatus.CREATED
    plan: Plan | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)
    retry_counts: dict[str, int] = Field(default_factory=dict)  # step_id -> count
    replan_count: int = 0
    replan_reason: str = ""   # 最近一次 Evaluator 拒绝原因，传给 Planner 做 Global Replan
    pending_approval: Approval | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    final_result: ResultReference | None = None


class LlmResult(BaseModel):
    text: str
    token_usage: dict[str, int] = Field(default_factory=dict)
