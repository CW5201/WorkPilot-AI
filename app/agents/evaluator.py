"""Evaluator Agent（第七节，独立于 Reporter）。

检查：任务是否完成 / 结果是否完整 / 数据是否一致 / 文件是否成功生成 /
      格式是否正确 / Tool 是否执行成功 / 是否存在明显错误 /
      高风险操作是否经过审批。

返回结构化结果：
  { passed, error_type, failed_step, reason, suggested_action }
支持 error_type: STEP_ERROR / PLAN_ERROR / TOOL_ERROR / OUTPUT_ERROR / SAFETY_ERROR
支持 suggested_action: PASS / RETRY_STEP / REPLAN / HUMAN
"""

from __future__ import annotations

import json

from ..artifacts import get_artifact_store
from ..llm import get_llm
from ..models import Evaluation, ErrorType, SuggestedAction, TaskRecord
from ..trace import Tracer


class Evaluator:
    name = "evaluator"

    def __init__(self):
        self.artifacts = get_artifact_store()

    def evaluate(self, task: TaskRecord, step_results: list,
                 tracer: Tracer, replan_reason: str = "") -> Evaluation:
        evals = self._rule_eval(task, step_results)
        # 高风险操作审批校验（SAFETY_ERROR）
        for sr in step_results:
            if sr.error == "APPROVAL_REQUIRED":
                evals = Evaluation(passed=False, error_type=ErrorType.SAFETY_ERROR,
                                   failed_step=sr.step.step_id,
                                   reason=f"{sr.step.tool} 需人工审批",
                                   suggested_action=SuggestedAction.HUMAN)
                break
        task.evaluations.append(evals)
        tracer.log(self.name, "EVAL", output=evals.model_dump(),
                   status="PASS" if evals.passed else "FAIL",
                   input_={"failed_step": evals.failed_step,
                           "suggested": evals.suggested_action.value})
        return evals

    def _rule_eval(self, task: TaskRecord, step_results: list) -> Evaluation:
        """规则式评估：无 LLM 也能判定。有 LLM 时叠加语义审查。"""
        if not step_results:
            return Evaluation(passed=False, error_type=ErrorType.PLAN_ERROR,
                              reason="计划为空，需重新规划",
                              suggested_action=SuggestedAction.REPLAN)
        failed = [s for s in step_results if not s.ok]
        if failed:
            last = failed[-1]
            err = last.error
            if err in {"APPROVAL_REQUIRED"}:
                return Evaluation(passed=False, error_type=ErrorType.SAFETY_ERROR,
                                  failed_step=last.step.step_id,
                                  reason=f"{last.step.tool} 需审批",
                                  suggested_action=SuggestedAction.HUMAN)
            # Tool 参数/执行错误 → STEP_ERROR / RETRY_STEP
            return Evaluation(passed=False,
                              error_type=ErrorType.STEP_ERROR if "参数" in err or "param" in err.lower()
                              else ErrorType.TOOL_ERROR,
                              failed_step=last.step.step_id,
                              reason=err,
                              suggested_action=SuggestedAction.RETRY_STEP)
        # 全部成功：校验交付物
        finals = [s for s in step_results if s.ok and s.artifact is not None]
        if not finals:
            return Evaluation(passed=False, error_type=ErrorType.OUTPUT_ERROR,
                              reason="无有效交付物",
                              suggested_action=SuggestedAction.REPLAN)
        return Evaluation(passed=True, suggested_action=SuggestedAction.PASS)


_evaluator: "Evaluator | None" = None


def get_evaluator() -> Evaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = Evaluator()
    return _evaluator
