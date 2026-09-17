"""最小可运行主链路 + 各模块的 pytest 冒烟测试。

运行：python -m pytest tests -q
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_tools_registered():
    from app.tools.registry import ensure_tools
    g = ensure_tools()
    names = g.names()
    assert "file.read" in names and "email.send" in names
    assert g.requires_approval("email.send") is True
    assert g.requires_approval("file.read") is False


def test_scenario1_full_chain():
    from app.demo.run_demo import SCENARIOS
    from app.engine.orchestrator import get_orchestrator
    from app.trace import Tracer
    from app.tools.registry import ensure_tools
    ensure_tools()
    orch = get_orchestrator()
    sc = SCENARIOS["1"]
    task = orch.create_task("t", sc["instruction"])
    tr = Tracer(task.task_id)
    out = orch.run(task, tracer=tr, deliverable="markdown")
    if out.status.value == "WAITING_APPROVAL" and out.pending_approval:
        out = orch.approve_and_continue(task, out.pending_approval, True, tr,
                                        deliverable="markdown")
    assert out.status.value in {"COMPLETED", "HUMAN_HANDOFF"}, out.status
    assert task.artifacts or out.final_result


def test_artifact_store_roundtrip():
    from app.artifacts import get_artifact_store
    from app.models import ArtifactType
    s = get_artifact_store()
    a = s.save_text("task_x", "step_1", "hello", ArtifactType.DOCUMENT,
                    summary="test", ext="txt")
    assert s.get(a.result_id).summary == "test"
    assert s.load_content(a.result_id) == "hello"


def test_plan_topological_order():
    from app.models import Plan, PlanStep
    p = Plan(task_id="t", goal="g", steps=[
        PlanStep.new("a"),
        PlanStep.new("b", depends_on=[]),
    ])
    order = p.topological_order()
    assert order[0] == "a"


def test_evaluator_pass_and_fail():
    from app.agents.evaluator import get_evaluator
    from app.models import TaskRecord
    e = get_evaluator()
    t = TaskRecord(task_id="t", instruction="i")
    class _R:
        def __init__(self, step, ok, error=""):
            self.step = step; self.ok = ok; self.error = error
    from app.models import PlanStep
    ok = _R(PlanStep.new("x"), True)
    fail = _R(PlanStep.new("x"), False, "tool boom")
    ev_pass = e._rule_eval(t, [ok])
    ev_fail = e._rule_eval(t, [fail])
    assert ev_pass.passed is True
    assert ev_fail.passed is False and ev_fail.suggested_action.value == "RETRY_STEP"


def test_benchmark_framework():
    from app.evaluation import run_benchmark
    r = run_benchmark(n=3)
    assert "single_agent" in r and "multi_agent" in r
    assert r["multi_agent"]["task_success_rate"] is not None


def test_api_builds():
    from app.main import create_app
    app = create_app()
    assert any("/tasks" in p for p in app.routes and [getattr(r, "path", "") for r in app.routes])
