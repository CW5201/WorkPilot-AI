"""Reporter Agent（第六节）：汇总中间结果 → 整理最终结果 → 生成交付物。

- 支持 Markdown/Word 报告、PPT 大纲/PPT、Excel/表格、邮件正文、任务总结。
- 不负责重新执行已完成的 Tool：只基于 Artifact Store 里的摘要 + result_id 汇总。
"""

from __future__ import annotations

import json
import re

from ..artifacts import get_artifact_store
from ..llm import get_llm
from ..models import TaskRecord, TaskStatus
from ..trace import Tracer


class Reporter:
    name = "reporter"

    def __init__(self):
        self.artifacts = get_artifact_store()

    def produce(self, task: TaskRecord, deliverable: str = "markdown",
                tracer: Tracer | None = None, extra: str = "") -> dict:
        """deliverable ∈ {markdown, ppt, excel, email, summary}。"""
        summaries = self.artifacts.all_summaries(task.task_id)
        payload = {"task": task.instruction, "deliverable": deliverable,
                   "artifacts": [s.model_dump() for s in summaries],
                   "extra": extra}
        content = self._render(task, deliverable, summaries)

        ext = {"markdown": "md", "ppt": "ppt.json", "excel": "json",
               "email": "email.json", "summary": "md"}.get(deliverable, "md")
        art_type = {"ppt": "PPT", "excel": "EXCEL", "email": "EMAIL",
                    "markdown": "FINAL_DELIVERABLE", "summary": "FINAL_DELIVERABLE"}
        from ..models import ArtifactType
        art = self.artifacts.save_text(
            task.task_id, "", content, ArtifactType(art_type.get(deliverable, "FINAL_DELIVERABLE")),
            summary=f"[{deliverable}] 交付物已生成", ext=ext)
        tracer and tracer.log(self.name, "REPORT_DONE",
                              output={"deliverable": deliverable, "result_id": art.result_id})
        return {"result_id": art.result_id, "deliverable": deliverable,
                "path": art.storage_path, "content": content}

    def _render(self, task: TaskRecord, deliverable: str, summaries) -> str:
        lines = [f"# 任务交付（{deliverable}）", "", f"目标：{task.instruction}", "", "## 中间结果"]
        for s in summaries:
            lines.append(f"- {s.summary}")
        llm = get_llm()
        # 真实 LLM 时可用 LLM 生成连贯正文；规则式直接结构化
        if llm._client is not None:
            res = llm.chat([
                {"role": "system", "content": "你是企业办公交付撰写者，基于下列结果生成一份规范的"
                                               f" {deliverable} 正文，Markdown 输出。"},
                {"role": "user", "content": json.dumps(
                    {"goal": task.instruction,
                     "results": [s.summary for s in summaries]},
                    ensure_ascii=False)}],
            temperature=0.3)
            lines += ["", "## 正文", "", res.text]
        return "\n".join(lines)


_reporter: "Reporter | None" = None


def get_reporter() -> Reporter:
    global _reporter
    if _reporter is None:
        _reporter = Reporter()
    return _reporter
