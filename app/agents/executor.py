"""Executor Agent（第二节）：真正"干活"的 Agent。

按 Planner 生成的步骤执行：
  - 调用 MCP / Tool
  - 参数校验
  - 超时处理
  - Tool error 处理 + 重试
  - 保存执行结果到 Artifact Store（result_id + summary）
  - 按需调用 Retriever Agent（Agentic Retrieval）

上下文传递原则（第四节）：每步只把 summary + key_entities + result_id 交给下游，
完整结果外置到 Artifact Store。
"""

from __future__ import annotations

import json
from typing import Any

from ..artifacts import get_artifact_store
from ..models import (ArtifactType, PlanStep, TaskRecord, TaskStatus)
from ..tools.registry import ensure_tools
from ..trace import Tracer
from .retriever import get_retriever_agent


class StepRunResult:
    def __init__(self, step: PlanStep, ok: bool, summary: str,
                 result_ref: Any, artifact: Any, error: str = "",
                 retries_used: int = 0, token_usage: dict | None = None):
        self.step = step
        self.ok = ok
        self.summary = summary
        self.result_ref = result_ref
        self.artifact = artifact
        self.error = error
        self.retries_used = retries_used
        self.token_usage = token_usage or {}


class Executor:
    name = "executor"

    def __init__(self):
        self.artifacts = get_artifact_store()
        self.retriever = get_retriever_agent()

    # -- 执行整个计划 ------------------------------------------------------
    def execute_plan(self, task: TaskRecord, tracer: Tracer,
                     only_steps: list[str] | None = None,
                     approved_step_ids: set[str] | None = None) -> list[StepRunResult]:
        """按计划拓扑顺序执行。only_steps 非空时只执行这些步骤（用于 RETRY_STEP）。
        approved_step_ids 是 HITL 审批通过后重跑的高风险步骤，注入 _approved=True。"""
        plan = task.plan
        order = plan.topological_order() if plan else []
        results: list[StepRunResult] = []
        approved_step_ids = approved_step_ids or set()
        for step_id in order:
            if only_steps is not None and step_id not in only_steps:
                continue
            step = next((s for s in plan.steps if s.step_id == step_id), None)
            if step is None:
                continue
            task.status = TaskStatus.EXECUTING
            res = self._run_step(task, step, tracer,
                                 approved=step_id in approved_step_ids)
            results.append(res)
            if not res.ok:
                break  # 单步失败即停，交给 Evaluator 决策（RETRY / REPLAN / HUMAN）
        return results

    # -- 单步执行 ----------------------------------------------------------
    def _run_step(self, task: TaskRecord, step: PlanStep, tracer: Tracer,
                  approved: bool = False) -> StepRunResult:
        tool = step.tool
        step_input = dict(step.input or {})

        # 按需检索（Agentic Retrieval）
        if self.retriever.needs_retrieval(tool, step_input):
            ctx_query = step_input.get("query") or step.action or task.instruction
            ctx = self.retriever.fetch_context(task.task_id, ctx_query, top_k=5, tracer=tracer)
            step_input["_knowledge_context"] = {
                "hit": ctx["hit"], "results": ctx["results"]}

        tracer.log(self.name, "TOOL_CALL", step_id=step.step_id,
                   tool_name=tool, tool_args=step_input,
                   input_=step.action)

        governor = ensure_tools()
        # 内部键（_ 前缀）仅用于上下文传递，不传给 tool handler
        exec_args = {k: v for k, v in step_input.items() if not k.startswith("_")}
        if approved and governor.requires_approval(tool):
            exec_args["_approved"] = True  # HITL 审批通过 → 放行高风险执行
        tres = governor.execute(tool, exec_args, task_id=task.task_id, step_id=step.step_id)

        if not tres.ok and governor.requires_approval(tool):
            # 高风险工具未获批 → 返回给上层挂起 HITL，而不是直接失败
            return StepRunResult(step, ok=False, summary=f"{tool} 需要人工审批",
                                 result_ref=None, artifact=None,
                                 error="APPROVAL_REQUIRED", retries_used=tres.retries_used)

        artifact_type = _map_artifact_type(tool)
        if tres.ok:
            content, summary, key_entities, ext = _materialize(tool, tres.data, step)
            art = self.artifacts.save_bytes(task.task_id, step.step_id, content,
                                           artifact_type, summary=summary,
                                           key_entities=key_entities, ext=ext)
            ref = self.artifacts.record_reference(art.result_id, summary, key_entities)
            tracer.log(self.name, "TOOL_OK", step_id=step.step_id, tool_name=tool,
                       tool_result={"result_id": art.result_id},
                       latency_ms=tres.latency_ms, status="OK")
            task.status = TaskStatus.EXECUTING
            task.artifacts.append(art)   # 同步回填，供持久化/前端查询
            from ..memory import get_memory
            get_memory().task_mark_done(task.task_id, step.step_id)
            return StepRunResult(step, True, summary, ref, art,
                                 retries_used=tres.retries_used)
        else:
            tracer.log(self.name, "TOOL_ERR", step_id=step.step_id, tool_name=tool,
                       error=tres.error, latency_ms=tres.latency_ms,
                       retry_count=tres.retries_used, status="ERROR")
            return StepRunResult(step, False, f"{tool} 失败", None, None,
                                 error=tres.error, retries_used=tres.retries_used)


# ---------------------------------------------------------------- helpers
def _map_artifact_type(tool: str) -> ArtifactType:
    return {
        "file.read": ArtifactType.FILE,
        "file.search": ArtifactType.FILE,
        "spreadsheet.read": ArtifactType.EXCEL,
        "spreadsheet.write": ArtifactType.EXCEL,
        "ppt.create": ArtifactType.PPT,
        "document.create": ArtifactType.DOCUMENT,
        "email.draft": ArtifactType.EMAIL,
        "email.send": ArtifactType.EMAIL,
        "calendar.create": ArtifactType.CALENDAR,
        "calendar.update": ArtifactType.CALENDAR,
        "web.search": ArtifactType.TOOL_OUTPUT,
        "knowledge.search": ArtifactType.AGENT_INTERMEDIATE,
        "sql.query": ArtifactType.TOOL_OUTPUT,
        "analysis.summarize": ArtifactType.AGENT_INTERMEDIATE,
        "analysis.extract_todos": ArtifactType.AGENT_INTERMEDIATE,
        "file.write": ArtifactType.DOCUMENT,
    }.get(tool, ArtifactType.TOOL_OUTPUT)


def _materialize(tool: str, data: Any, step: PlanStep) -> tuple[bytes, str, list[str], str]:
    """把 tool 结果物化为可落盘内容 + 摘要 + 关键实体 + 扩展名。"""
    if isinstance(data, dict):
        if "path" in data and tool in {"ppt.create", "document.create", "spreadsheet.write"}:
            from pathlib import Path
            p = Path(data["path"])
            blob = p.read_bytes() if p.exists() else json.dumps(data, ensure_ascii=False).encode()
            summary = _summarize_dict(tool, data)
            return blob, summary, _key_entities(data), p.suffix.lstrip(".") or "json"
        if tool == "file.read":
            docs = data.get("documents", [])
            combined = "\n\n".join(d.get("content", "") for d in docs)
            nfiles = len([d for d in docs if "error" not in d])
            summary = f"已读取 {nfiles} 份文件：" + "、".join(
                d.get("file", "?") for d in docs[:8])
            return combined.encode("utf-8"), summary, _key_entities(docs), "txt"
        if tool == "analysis.summarize":
            return (json.dumps(data, ensure_ascii=False).encode(),
                    _summarize_dict(tool, data),
                    data.get("key_entities", []) or _key_entities(data), "json")
        if tool == "analysis.extract_todos":
            todos = data.get("todos", [])
            return (json.dumps(data, ensure_ascii=False).encode(),
                    f"提取到 {len(todos)} 条待办", todos[:6], "json")
        if tool in {"email.draft", "email.send"}:
            return (json.dumps(data, ensure_ascii=False).encode(),
                    _summarize_dict(tool, data), ["email"], "json")
        if tool in {"calendar.create", "calendar.update"}:
            return (json.dumps(data, ensure_ascii=False).encode(),
                    _summarize_dict(tool, data), ["calendar"], "json")
        if tool == "web.search":
            res = data.get("results", [])
            return (json.dumps(data, ensure_ascii=False).encode(),
                    f"检索到 {len(res)} 条结果", [r.get("title", "") for r in res[:5]], "json")
        if tool == "knowledge.search":
            res = data.get("results", [])
            return (json.dumps(data, ensure_ascii=False).encode(),
                    ("命中知识：" if data.get("hit") else "未命中知识，") +
                    f"{len(res)} 条", [r.get("title", "") for r in res[:5]], "json")
        if tool == "sql.query":
            return (json.dumps(data, ensure_ascii=False).encode(),
                    f"查询到 {len(data.get('rows', []))} 行",
                    data.get("columns", []), "json")
        if tool == "spreadsheet.read":
            return (json.dumps(data, ensure_ascii=False).encode(),
                    f"表格 {data.get('row_count',0)} 行 / {len(data.get('columns',[]))} 列",
                    data.get("columns", []), "json")
        # 通用 dict
        return (json.dumps(data, ensure_ascii=False).encode(),
                _summarize_dict(tool, data), _key_entities(data), "json")
    # 非 dict 兜底
    return (json.dumps(str(data), ensure_ascii=False).encode(),
            f"{tool} 结果", [], "json")


def _summarize_dict(tool: str, data: dict) -> str:
    if "pages" in data:
        return f"PPT {data.get('title','')} 共 {data.get('pages')} 页"
    if "written" in data:
        return f"已写入 {data.get('written','')}"
    if "rows" in data and isinstance(data.get("rows"), list):
        return f"写入 {len(data['rows'])} 行"
    if "todos" in data:
        return f"{len(data.get('todos', []))} 条待办"
    return f"{tool} 完成"


def _key_entities(data: Any) -> list[str]:
    if isinstance(data, dict):
        return [k for k in data if isinstance(data[k], (list, dict))][:8]
    return []


_executor: "Executor | None" = None


def get_executor() -> Executor:
    global _executor
    if _executor is None:
        _executor = Executor()
    return _executor
