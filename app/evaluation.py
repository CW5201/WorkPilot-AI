"""评测框架（第十四节）。

固定任务集（100 个复合办公任务，覆盖文档/报表/PPT/邮件/日程/知识检索/
多步骤/Tool 失败/人工审批）。对比 Single Agent vs Multi-Agent。
统计：Task Success Rate / Average Steps / Token Cost / Execution Time /
Tool Success Rate / Human Handoff Rate。

**不虚构结果**：这里实现评测框架 + 任务集，真实数据在跑通后生成。
当前离线（无 LLM key）下，Multi-Agent 走规则式 Planner，可产出真实指标；
Single-Agent 基线用一个"顺序执行、无规划/评估/恢复"的简化代理对比。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_settings, TASKSET_DIR
from .models import TaskRecord, TaskStatus
from .trace import Tracer


@dataclass
class TaskSpec:
    task_id: str
    instruction: str
    category: str
    expected_tools: list[str] = field(default_factory=list)
    requires_approval: bool = False
    expected_success: bool = True


def load_taskset(limit: int | None = None) -> list[TaskSpec]:
    f = TASKSET_DIR / "benchmark_tasks.json"
    if not f.exists():
        return _builtin_taskset(limit)
    data = json.loads(f.read_text(encoding="utf-8"))
    specs = [TaskSpec(**d) for d in data]
    return specs[:limit] if limit else specs


def _builtin_taskset(limit: int | None = None) -> list[TaskSpec]:
    """100 个固定任务的种子（真实数据以 benchmark_tasks.json 为准；
    文件缺失时由本函数确定性生成 100 条，便于离线跑通评测框架）。"""
    templates = [
        ("周报汇总生成PPT", "汇总{d}部门周报，提取OKR，生成{n}页PPT，整理邮件草稿",
         "文档处理/PPT", ["file.read", "analysis.summarize", "ppt.create", "email.draft"]),
        ("会议纪要转日程", "读取{d}会议纪要，提取待办，创建日程，生成跟进邮件",
         "日程/邮件", ["file.read", "analysis.extract_todos", "calendar.create", "email.draft"]),
        ("Excel数据分析", "读取销售Excel，分析达成率，生成统计报告",
         "报表分析", ["spreadsheet.read", "analysis.summarize", "document.create"]),
        ("知识检索成文", "查询企业知识库SOP，按规范生成文档并保存",
         "知识检索", ["knowledge.search", "document.create", "file.write"]),
        ("网页研究简报", "网页搜索{q}趋势，汇总资料，生成研究简报",
         "多步骤/研究", ["web.search", "analysis.summarize", "document.create"]),
        ("邮件发送(需审批)", "整理汇报邮件并发送给领导，发送前需确认",
         "邮件/审批", ["email.draft", "email.send"]),
    ]
    specs: list[TaskSpec] = []
    dparts = ["周一", "周二", "本周一", "上周", "昨日"]
    qparts = ["企业数字员工", "AI办公", "自动化", "知识管理", "OKR管理"]
    for i in range(100):
        t = templates[i % len(templates)]
        instr = t[1].format(d=dparts[i % 5], n=3, q=qparts[i % 5])
        specs.append(TaskSpec(
            task_id=f"bench_{i:03d}",
            instruction=instr,
            category=t[2],
            expected_tools=list(t[3]),
            requires_approval=("需审批" in t[0]),
            expected_success=True,
        ))
    return specs[:limit] if limit else specs


# ------------------------------------------------------------------ 执行
@dataclass
class RunMetrics:
    mode: str
    task_success_rate: float
    avg_steps: float
    total_token_cost: int
    total_execution_ms: int
    tool_success_rate: float
    human_handoff_rate: float
    per_task: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "per_task"}


def _run_multi_agent(task: TaskSpec) -> dict:
    from .engine.orchestrator import get_orchestrator
    orch = get_orchestrator()
    t0 = time.perf_counter()
    tr = Tracer(task.task_id + "_ma")
    wrk = orch.create_task("bench", task.instruction)
    out = orch.run(wrk, tracer=tr, deliverable="markdown")
    # 若挂起审批（评测里自动批准，避免卡住）
    status = out.status.value
    auto_approved = False
    if status == TaskStatus.WAITING_APPROVAL.value and out.pending_approval:
        out2 = orch.approve_and_continue(
            out.task, out.pending_approval, True, tr, deliverable="markdown")
        status = out2.status.value
        auto_approved = True
    elapsed = int((time.perf_counter() - t0) * 1000)
    events = tr.events
    steps = len({e.step_id for e in events if e.event_type in
                 {"TOOL_CALL"}}) or (len(out.task.plan.steps) if out.task.plan else 0)
    tool_calls = [e for e in events if e.tool_name and e.event_type == "TOOL_CALL"]
    tool_ok = [e for e in tool_calls
               if not any(x.error and x.tool_name == e.tool_name
                          for x in events)]
    tokens = sum(e.token_usage.get("total", 0) for e in events if e.token_usage)
    success = status == TaskStatus.COMPLETED.value
    return {
        "task_id": task.task_id, "success": success, "status": status,
        "steps": steps, "tokens": tokens, "elapsed_ms": elapsed,
        "tool_calls": len(tool_calls), "tool_success": len(tool_ok),
        "handoff": status == TaskStatus.HUMAN_HANDOFF.value,
        "auto_approved": auto_approved,
    }


def _run_single_agent(task: TaskSpec) -> dict:
    """Single-Agent 基线：无规划器/评估器/恢复，顺序跑期望工具。
    用于对比，故意比 Multi-Agent 弱（无 retry/replan/eval）。"""
    from .tools.registry import ensure_tools
    g = ensure_tools()
    t0 = time.perf_counter()
    ok = 0
    n = len(task.expected_tools)
    for tool in task.expected_tools:
        tres = g.execute(tool, {}, task_id=task.task_id, step_id="single")
        ok += 1 if tres.ok else 0
    elapsed = int((time.perf_counter() - t0) * 1000)
    # 单代理：缺少评估与恢复，工具部分失败即判失败
    success = (ok == n) and not task.requires_approval
    return {
        "task_id": task.task_id, "success": success,
        "steps": n, "tokens": 0, "elapsed_ms": elapsed,
        "tool_calls": n, "tool_success": ok,
        "handoff": task.requires_approval,  # 单代理无法处理审批 → handoff
        "auto_approved": False,
    }


def _aggregate(mode: str, rows: list[dict]) -> dict:
    total = len(rows)
    succ = sum(1 for r in rows if r["success"])
    handoff = sum(1 for r in rows if r["handoff"])
    tc = sum(r["tool_calls"] for r in rows)
    tsuccess = sum(r["tool_success"] for r in rows)
    return {
        "mode": mode,
        "task_success_rate": round(succ / total, 4) if total else 0.0,
        "avg_steps": round(sum(r["steps"] for r in rows) / total, 2) if total else 0,
        "total_token_cost": sum(r["tokens"] for r in rows),
        "total_execution_ms": sum(r["elapsed_ms"] for r in rows),
        "tool_success_rate": round(tsuccess / tc, 4) if tc else 0.0,
        "human_handoff_rate": round(handoff / total, 4) if total else 0.0,
        "per_task": rows,
    }


def run_benchmark(n: int | None = 5, sample: bool = True) -> dict:
    """跑评测。n 限制任务数；sample=True 时离线只抽 n 个（控制耗时）。"""
    specs = load_taskset(n)
    ma_rows, sa_rows = [], []
    for spec in specs:
        ma_rows.append(_run_multi_agent(spec))
        sa_rows.append(_run_single_agent(spec))
    ma = _aggregate("multi_agent", ma_rows)
    sa = _aggregate("single_agent", sa_rows)
    return {
        "n_tasks": len(specs),
        "single_agent": sa,
        "multi_agent": ma,
        "note": "离线（规则式 Planner）评测结果，供对比流程；接真实 LLM 后重跑。",
    }
