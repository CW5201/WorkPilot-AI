"""端到端 Demo 运行器（不依赖 LLM key 也能跑通最小主链路）。

用法：
  python -m app.demo.run_demo            # 跑场景1（周报→PPT→邮件草稿）
  python -m app.demo.run_demo --scenario 2   # 场景2
  python -m app.demo.run_demo --list      # 列出可用场景
"""

from __future__ import annotations

import argparse
import json
import sys

from ..engine.orchestrator import get_orchestrator
from ..trace import Tracer
from ..tools.registry import ensure_tools

SCENARIOS = {
    "1": {
        "name": "周报汇总→PPT→邮件草稿（含人工审批）",
        "instruction": "汇总5份部门周报，提取OKR完成情况，生成3页PPT，"
                       "整理一封汇报邮件，发送前让我确认",
        "deliverable": "markdown",
    },
    "2": {
        "name": "会议纪要→待办→日程→跟进邮件",
        "instruction": "读取会议纪要，提取待办事项，为每条待办创建日程，"
                       "并生成一封跟进邮件草稿",
        "deliverable": "email",
    },
    "3": {
        "name": "读Excel→分析→统计报告",
        "instruction": "读取销售数据Excel，分析各地区各产品的达成率，"
                       "生成统计结果并输出Markdown报告",
        "deliverable": "markdown",
    },
    "4": {
        "name": "企业知识库→按规范生成文档→保存",
        "instruction": "查询企业知识库里的周报汇总SOP，按公司规范生成一份"
                       "周报汇总文档并保存",
        "deliverable": "markdown",
    },
    "5": {
        "name": "网页搜索→汇总→研究简报",
        "instruction": "网页搜索'企业数字员工'相关趋势，汇总资料并生成一份"
                       "研究简报",
        "deliverable": "markdown",
    },
}


def _print_plan(plan) -> None:
    print(f"\n[PLAN] 共 {len(plan.steps)} 步：")
    for s in plan.steps:
        deps = f" (依赖 {s.depends_on})" if s.depends_on else ""
        print(f"   - {s.step_id} {s.action} -> {s.tool}{deps}")
    if plan.clarification_needed:
        print(f"   [需澄清] {plan.clarification_needed}")


def _run(scenario: str, auto_approve: bool = True) -> dict:
    ensure_tools()
    orch = get_orchestrator()
    sc = SCENARIOS[scenario]
    task = orch.create_task("demo_user", sc["instruction"])
    tracer = Tracer(task.task_id)

    print(f"=== 场景{scenario}：{sc['name']} ===")
    print(f"指令：{sc['instruction']}\n")
    outcome = orch.run(task, tracer=tracer, deliverable=sc["deliverable"])

    if task.plan:
        _print_plan(task.plan)

    print(f"\n[状态] {outcome.status.value}")
    if outcome.status.value == "WAITING_APPROVAL":
        ap = outcome.pending_approval
        print(f"\n--- 待审批（HITL）---")
        print(f"工具: {ap.tool}  风险: {ap.risk_level}")
        print(json.dumps(ap.payload, ensure_ascii=False, indent=2)[:400])
        if auto_approve:
            print(">>> 自动批准（演示）")
            outcome = orch.approve_and_continue(task, ap, True, tracer,
                                                deliverable=sc["deliverable"])
            print(f"[审批后状态] {outcome.status.value}")
        else:
            print(">>> 演示停止于审批（--no-approve 可跳过自动批准）")
    elif outcome.status.value == "COMPLETED":
        fr = outcome.final_result
        if fr:
            print(f"\n[最终交付] {fr.get('deliverable')} -> {fr.get('path')}")

    print("\n--- Trace Timeline ---")
    for ev in tracer.timeline():
        tok = f" tokens={ev['tokens']}" if ev["tokens"] else ""
        rt = f" retry={ev['retry']}" if ev["retry"] else ""
        print(f"   {ev['agent']:12} {ev['event']:18} {ev['tool']:14} "
              f"{ev['status']:4} {ev['latency_ms']}ms{tok}{rt} "
              f"{'ERR:'+ev['error'] if ev['error'] else ''}")
    return {"task_id": task.task_id, "status": outcome.status.value,
            "timeline": tracer.timeline()}


def main() -> None:
    p = argparse.ArgumentParser(description="WorkPilot Demo")
    p.add_argument("--scenario", default="1", choices=list(SCENARIOS.keys()))
    p.add_argument("--no-approve", action="store_true",
                   help="高风险步骤挂起在审批，不自动批准")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        for k, v in SCENARIOS.items():
            print(f"  场景{k}：{v['name']}")
        return
    _run(args.scenario, auto_approve=not args.no_approve)


if __name__ == "__main__":
    main()
