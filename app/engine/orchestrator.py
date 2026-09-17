"""主链路编排（Orchestrator）。

自然语言任务 → Planner → Executor → 按需 Retriever → Tool → Artifact →
Reporter → Evaluator → Retry/Replan → Human Approval → Final Output → Trace

这里把 5 个 Agent + ToolGovernor + Artifact + Evaluator + HITL 串成闭环。
（若后续引入 LangGraph，可用 StateGraph 封装同样的节点；当前用显式循环
保证最小可运行、可测试、可 checkpoint。）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..agents import (get_coordinator, get_evaluator, get_executor,
                      get_reporter)
from ..artifacts import get_artifact_store
from ..llm import get_llm
from ..memory import get_memory
from ..models import (Approval, Plan, TaskRecord, TaskStatus)
from ..tools.registry import ensure_tools
from ..trace import Tracer


@dataclass
class RunOutcome:
    task: TaskRecord
    status: TaskStatus
    final_result: dict | None = None
    pending_approval: Approval | None = None
    error: str = ""
    step_results: list = field(default_factory=list)


class Orchestrator:
    def __init__(self):
        self.coordinator = get_coordinator()
        self.executor = get_executor()
        self.reporter = get_reporter()
        self.evaluator = get_evaluator()
        self.artifacts = get_artifact_store()
        self.memory = get_memory()
        from ..persistence import get_task_store
        self.store = get_task_store()

    # ---------------------------------------------------------------- run
    def create_task(self, user_id: str, instruction: str) -> TaskRecord:
        task = TaskRecord(task_id=f"task_{uuid.uuid4().hex[:10]}",
                          user_id=user_id, instruction=instruction)
        self.store.save(task, force=True)
        return task

    def _ckpt(self, task: TaskRecord) -> None:
        self.store.checkpoint(task)

    def run(self, task: TaskRecord, *, tracer: Tracer | None = None,
            deliverable: str = "markdown") -> RunOutcome:
        tracer = tracer or Tracer(task.task_id)
        governor = ensure_tools()
        s = _settings()

        # 1) PLANNING
        task.status = TaskStatus.PLANNING
        plan = self.coordinator.plan(task, tracer)
        task.plan = plan
        self._ckpt(task)

        # 缺参 → 澄清（HITL 第一层）
        if plan.clarification_needed:
            task.status = TaskStatus.WAITING_CLARIFICATION
            tracer.log("orchestrator", "CLARIFY",
                       output=plan.clarification_needed)
            self._ckpt(task)
            return RunOutcome(task, TaskStatus.WAITING_CLARIFICATION,
                              error="需要澄清：" + "；".join(plan.clarification_needed))

        task.status = TaskStatus.READY
        self._ckpt(task)

        # 2) EXECUTE + EVALUATE 循环（分级错误恢复）
        pending_retry_step: str | None = None
        for attempt in range(s.replan_limit + 1):
            results = self.executor.execute_plan(
                task, tracer,
                only_steps=[pending_retry_step] if pending_retry_step else None)
            pending_retry_step = None
            task.status = TaskStatus.EVALUATING
            eval_res = self.evaluator.evaluate(task, results, tracer)

            if eval_res.passed:
                return self._finalize(task, tracer, deliverable=deliverable)

            action = eval_res.suggested_action
            if action.value == "RETRY_STEP" and eval_res.failed_step:
                retried = task.retry_counts.get(eval_res.failed_step, 0)
                if retried >= s.retry_step_limit:
                    return self._handoff(task, tracer,
                                          "局部重试超限", results)
                task.status = TaskStatus.RETRYING
                task.retry_counts[eval_res.failed_step] = retried + 1
                pending_retry_step = eval_res.failed_step
                continue  # 下一轮只重跑失败步骤


            if action.value == "REPLAN":
                if task.replan_count >= s.replan_limit:
                    return self._handoff(task, tracer, "全局重规划超限", results)
                task.status = TaskStatus.REPLANNING
                task.replan_count += 1
                new_plan = self.coordinator.plan(
                    task, tracer, replan_reason=eval_res.reason or "方向错误")
                task.plan = new_plan
                continue

            if action.value == "HUMAN":
                # 高风险审批（HITL 第二层）
                if eval_res.failed_step and eval_res.error_type.value == "SAFETY_ERROR":
                    step = _find_step(task.plan, eval_res.failed_step)
                    approval = governor.build_approval(
                        task.task_id, eval_res.failed_step, step.tool,
                        dict(step.input or {}))
                    task.status = TaskStatus.WAITING_APPROVAL
                    task.pending_approval = approval
                    tracer.log("orchestrator", "APPROVAL_REQUEST",
                               step_id=eval_res.failed_step,
                               tool_name=step.tool, tool_args=step.input,
                               output=approval.model_dump())
                    self._ckpt(task)
                    return RunOutcome(task, TaskStatus.WAITING_APPROVAL,
                                      pending_approval=approval)
                return self._handoff(task, tracer, eval_res.reason or "需人工介入",
                                      results)

        # 循环耗尽
        return self._handoff(task, tracer, "达到最大恢复次数", results)

    # -- HITL：审批后继续 --------------------------------------------------
    def approve_and_continue(self, task: TaskRecord, approval: Approval,
                             approved: bool, tracer: Tracer,
                             deliverable: str = "markdown") -> RunOutcome:
        s = _settings()
        if not approved:
            task.status = TaskStatus.HUMAN_HANDOFF
            tracer.log("orchestrator", "APPROVAL_DENIED")
            self._ckpt(task)
            return RunOutcome(task, TaskStatus.HUMAN_HANDOFF,
                              error="用户拒绝审批")
        approval.approved = True
        approval.decided_at = time.time()
        # 审批通过 → 重跑该高风险步骤并放行
        step = _find_step(task.plan, approval.step_id)
        task.status = TaskStatus.EXECUTING
        results = self.executor.execute_plan(
            task, tracer, only_steps=[step.step_id],
            approved_step_ids={step.step_id})
        eval_res = self.evaluator.evaluate(task, results, tracer)
        if eval_res.passed:
            task.status = TaskStatus.COMPLETED
        else:
            return self._handoff(task, tracer, eval_res.reason, results)
        return self._finalize(task, tracer, deliverable=deliverable)

    # ---------------------------------------------------------------- helpers
    def _finalize(self, task: TaskRecord, tracer: Tracer,
                  deliverable: str = "markdown") -> RunOutcome:
        out = self.reporter.produce(task, deliverable=deliverable, tracer=tracer)
        ref = self.artifacts.get(out["result_id"])
        from ..models import ResultReference
        task.final_result = ResultReference(
            result_id=out["result_id"],
            summary=f"[{deliverable}] 最终交付物已生成",
            key_entities=[deliverable])
        task.status = TaskStatus.COMPLETED
        tracer.log("orchestrator", "FINAL",
                   output={"result_id": out["result_id"],
                           "deliverable": deliverable,
                           "path": out["path"]})
        self._ckpt(task)
        return RunOutcome(task, TaskStatus.COMPLETED,
                          final_result=out,
                          step_results=[self.artifacts.get(r.result_id)
                                        for r in [task.final_result]])

    def _handoff(self, task: TaskRecord, tracer: Tracer, reason: str,
                  results: list) -> RunOutcome:
        task.status = TaskStatus.HUMAN_HANDOFF
        tracer.log("orchestrator", "HUMAN_HANDOFF", output=reason, status="HANDOFF")
        self._ckpt(task)
        return RunOutcome(task, TaskStatus.HUMAN_HANDOFF, error=reason,
                          step_results=results)


def _find_step(plan: Plan | None, step_id: str):
    if plan is None:
        return None
    for st in plan.steps:
        if st.step_id == step_id:
            return st
    return plan.steps[0] if plan.steps else None


def _settings():
    from ..config import get_settings
    return get_settings()


_orch: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch
