"""Retriever Agent（第二节，按需检索 / Agentic Retrieval）。

不是主流程必经节点：由 Executor 在执行中判断"是否需要知识/历史上下文"才调用，
避免简单任务产生无意义检索、Token 与延迟。

检索：BM25 + BGE-M3（混合），向量存 Milvus；离线回退到本地 BM25。
负责：企业知识库 / SOP / 历史任务 / 文档模板 / 用户偏好 / 历史办公资料。
"""

from __future__ import annotations

from ..retrieval import get_retriever
from ..trace import Tracer


class RetrieverAgent:
    name = "retriever"

    def fetch_context(self, task_id: str, query: str, top_k: int = 5,
                      tracer: Tracer | None = None) -> dict:
        """按需拉取知识/历史上下文。返回结构化结果，供 Executor 注入 prompt。"""
        tracer and tracer.log(self.name, "RETRIEVE", input_=query)
        r = get_retriever().retrieve_summary(query, top_k=top_k)
        tracer and tracer.log(self.name, "RETRIEVE_DONE",
                              output={"hit": r["hit"], "n": len(r["results"]),
                                      "mode": r["mode"]},
                              status="HIT" if r["hit"] else "MISS")
        return r

    def needs_retrieval(self, step_tool: str, step_input: dict) -> bool:
        """Executor 依据该判断决定是否触发按需检索。"""
        if step_tool in {"knowledge.search", "sql.query"}:
            return False  # 本身就是检索/查询类，不需要再套一层检索
        # 生成类/汇总类步骤通常需要知识背景
        return step_tool in {"document.create", "ppt.create", "email.draft",
                             "analysis.summarize", "spreadsheet.write"} \
            and not step_input.get("_no_retrieve")


_agent: "RetrieverAgent | None" = None


def get_retriever_agent() -> RetrieverAgent:
    global _agent
    if _agent is None:
        _agent = RetrieverAgent()
    return _agent
