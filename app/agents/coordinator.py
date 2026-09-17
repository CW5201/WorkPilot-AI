"""Coordinator / Planner Agent（第一节）。

职责：理解任务 → 拆分复杂任务 → 生成结构化 Plan → 分析依赖 → 缺参时请求澄清 →
接收 Evaluator 的 Global Replan。Planner 负责"做什么"，Executor 负责"怎么调用工具"。
"""

from __future__ import annotations

import json
import re

from ..llm import get_llm
from ..models import Plan, PlanStep, TaskRecord
from ..trace import Tracer

_PLANNER_SYSTEM = (
    "你是企业办公任务规划器。把用户自然语言任务拆解为可执行步骤。"
    "每个步骤指定一个 tool（见下方工具清单），input 为该工具入参，depends_on 指向前置步骤。"
    "输出严格 JSON：{\"intent\":str,\"steps\":[{action,tool,input,depends_on,description}],"
    "\"clarification_needed\":[缺少的关键参数],\"assumptions\":[...]}。"
    "依赖用步骤序号引用；无法确定参数时在 clarification_needed 中列出。"
)


class Coordinator:
    name = "planner"

    def plan(self, task: TaskRecord, tracer: Tracer, replan_reason: str = "") -> Plan:
        tracer.log(self.name, "PLAN_START", input_=task.instruction,
                   checkpoint_id=f"replan_{task.replan_count}")

        llm = get_llm()
        user_prompt = _plan_user_prompt(task.instruction, replan_reason, task.replan_count)
        res = llm.chat([
            {"role": "system", "content": _PLANNER_SYSTEM + "\n工具：\n" + _tool_catalog()},
            {"role": "user", "content": user_prompt},
        ], response_json=True)

        parsed = _safe_json(res.text)
        plan = self._build_plan(task, parsed)

        tracer.log(self.name, "PLAN_DONE",
                   output={"steps": len(plan.steps), "clarify": plan.clarification_needed},
                   status="NEEDS_CLARIFICATION" if plan.clarification_needed else "OK",
                   token_usage=res.token_usage)
        return plan

    def _build_plan(self, task: TaskRecord, parsed: dict) -> Plan:
        steps: list[PlanStep] = []
        raw_steps = parsed.get("steps", []) or []
        for i, rs in enumerate(raw_steps):
            if not isinstance(rs, dict):
                steps.append(PlanStep.new(action=str(rs)))
                continue
            step_id = rs.get("step_id") or f"step_{i+1}"
            resolved_deps = _resolve_deps(rs.get("depends_on", []), steps, raw_steps)
            steps.append(PlanStep(
                step_id=step_id,
                action=rs.get("action", f"步骤{i+1}"),
                tool=rs.get("tool", ""),
                input=rs.get("input", {}) or {},
                depends_on=resolved_deps,
                description=rs.get("description", "")))
        if not steps:
            steps = [PlanStep.new(action=task.instruction)]
        return Plan(
            task_id=task.task_id,
            goal=task.instruction,
            steps=steps,
            clarification_needed=parsed.get("clarification_needed", []) or [],
            assumptions=parsed.get("assumptions", []) or [],
        )


def _resolve_deps(dep: list, steps: list[PlanStep], raw_steps: list) -> list[str]:
    out: list[str] = []
    for d in dep or []:
        ds = str(d)
        if ds in {"prev", "${prev}"}:
            if steps:
                out.append(steps[-1].step_id)
        elif ds.isdigit():
            j = int(ds)
            if j < len(raw_steps) and isinstance(raw_steps[j], dict):
                out.append(raw_steps[j].get("step_id") or f"step_{j+1}")
            else:
                out.append(ds)
        else:
            out.append(ds)
    return out


def _tool_catalog() -> str:
    try:
        from ..tools.registry import ensure_tools
        g = ensure_tools()
        return "\n".join(f"{n}: {s.description} (入参 {s.input_schema}, 风险 {s.risk_level})"
                         for n, s in g._tools.items())
    except Exception:  # noqa: BLE001
        return ("file.read, file.write, file.search, document.create, spreadsheet.read,"
                " spreadsheet.write, ppt.create, email.draft, email.send, calendar.create,"
                " calendar.update, web.search, knowledge.search, sql.query,"
                " analysis.summarize, analysis.extract_todos")


def _plan_user_prompt(instruction: str, replan_reason: str, replan_count: int) -> str:
    base = f"任务：{instruction}\n请规划执行步骤。"
    if replan_reason:
        base += f"\n\n这是第 {replan_count+1} 次重规划。上一次被 Evaluator 拒绝：{replan_reason}。"
    return base


def _safe_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {}


_coord: "Coordinator | None" = None


def get_coordinator() -> Coordinator:
    global _coord
    if _coord is None:
        _coord = Coordinator()
    return _coord
