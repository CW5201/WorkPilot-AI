"""LLM Client（OpenAI 兼容）。

- 有 API key 时走真实 OpenAI 兼容端点（可指向 vLLM / DashScope / OpenAI）。
- 无 key 时，若 llm_fallback_rule_based=True，提供离线规则式实现，保证 demo 可跑通。
统一返回 LlmResult，调用方无需感知后端。
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from ..config import get_settings
from ..models import LlmResult


class LLMClient:
    def __init__(self) -> None:
        self.s = get_settings()
        self._client: OpenAI | None = None
        if self.s.llm_api_key:
            self._client = OpenAI(base_url=self.s.llm_base_url,
                                  api_key=self.s.llm_api_key,
                                  timeout=self.s.llm_timeout)

    # -- 基础调用 -----------------------------------------------------------
    def chat(self, messages: list[dict], temperature: float | None = None,
             response_json: bool = False) -> LlmResult:
        temp = self.s.llm_temperature if temperature is None else temperature
        if self._client is not None:
            return self._chat_openai(messages, temp, response_json)
        if self.s.llm_fallback_rule_based:
            return self._chat_rule(messages, response_json)
        raise RuntimeError(
            "LLM API key 未配置且未启用规则式降级。请设置 LLM_API_KEY，"
            "或将 LLM_FALLBACK_RULE_BASED 置为 True。"
        )

    def _chat_openai(self, messages, temperature, response_json) -> LlmResult:
        kwargs: dict[str, Any] = dict(
            model=self.s.llm_model, messages=messages, temperature=temperature,
        )
        if response_json:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        tokens = {}
        if usage is not None:
            tokens = {"prompt": getattr(usage, "prompt_tokens", 0),
                      "completion": getattr(usage, "completion_tokens", 0),
                      "total": getattr(usage, "total_tokens", 0)}
        text = resp.choices[0].message.content or ""
        return LlmResult(text=text, token_usage=tokens)

    # -- 规则式降级（离线可跑通）------------------------------------------
    def _chat_rule(self, messages, response_json) -> LlmResult:
        """极简：把最后一条 user 消息作为输入，按 intent 生成结构化/文本输出。

        仅用于无 key 时演示流程；真实语义能力需接 LLM API。
        """
        user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user = m.get("content", "")
                break
        if response_json:
            return LlmResult(text=self._rule_json(user),
                             token_usage={"prompt": len(user), "completion": 200,
                                          "total": len(user) + 200})
        return LlmResult(text=self._rule_text(user),
                         token_usage={"prompt": len(user), "completion": 120,
                                      "total": len(user) + 120})

    def _rule_json(self, user: str) -> str:
        # Planner：按关键词拆出步骤
        intent = self._detect_intent(user)
        steps = self._rule_plan_steps(intent, user)
        return json.dumps({"intent": intent, "steps": steps,
                           "clarification_needed": []}, ensure_ascii=False)

    def _rule_plan_steps(self, intent: str, user: str) -> list[dict]:
        # 返回 [{action, tool, input, depends_on, description}]
        wants_send = ("发送" in user or "发给" in user or "send" in user.lower())
        to_addr = self._extract_email(user)
        base: list[dict] = []
        if "周报" in user or "汇总" in user or "report" in user.lower():
            base = [
                {"action": "读取部门周报", "tool": "file.read",
                 "input": {"files": []}, "depends_on": [],
                 "description": "读取 data 目录下的周报文件"},
                {"action": "汇总OKR完成情况", "tool": "analysis.summarize",
                 "input": {}, "depends_on": ["${prev}"],
                 "description": "从周报中提取各部门OKR完成度并汇总"},
                {"action": "生成PPT", "tool": "ppt.create",
                 "input": {"pages": 3}, "depends_on": ["${prev}"],
                 "description": "按汇总结果生成3页PPT"},
                {"action": "生成邮件草稿", "tool": "email.draft",
                 "input": {"to": [to_addr] if to_addr else [],
                           "subject": "部门周报汇总", "body": ""},
                 "depends_on": ["${prev}"],
                 "description": "基于PPT要点撰写汇报邮件"},
            ]
            if wants_send:
                base.append({"action": "发送汇报邮件（需人工确认）", "tool": "email.send",
                             "input": {"to": [to_addr] if to_addr else [],
                                       "subject": "部门周报汇总", "body": ""},
                             "depends_on": ["${prev}"],
                             "description": "高风险：发送前必须 HITL 审批"})
        elif "纪要" in user or "待办" in user or "日程" in user:
            base = [
                {"action": "读取会议纪要", "tool": "file.read",
                 "input": {"files": ["meeting_notes.md"], "subdir": "weekly_reports"},
                 "depends_on": [], "description": "读取会议纪要文件"},
                {"action": "提取待办事项", "tool": "analysis.extract_todos",
                 "input": {"text": "会议纪要"}, "depends_on": ["${prev}"],
                 "description": "抽取行动项"},
                {"action": "创建日程", "tool": "calendar.create",
                 "input": {"title": "会议待办跟进", "start": "", "end": ""},
                 "depends_on": ["${prev}"], "description": "为待办创建日程"},
                {"action": "生成跟进邮件", "tool": "email.draft",
                 "input": {"to": [], "subject": "会议待办跟进",
                           "body": "以下为本次会议待办清单："},
                 "depends_on": ["${prev}"], "description": "撰写跟进邮件"},
            ]
        elif "excel" in user.lower() or "数据" in user or "分析" in user:
            base = [
                {"action": "读取Excel", "tool": "spreadsheet.read",
                 "input": {"file": ""}, "depends_on": [],
                 "description": "读取表格数据"},
                {"action": "分析数据", "tool": "analysis.summarize",
                 "input": {}, "depends_on": ["${prev}"],
                 "description": "统计与洞察"},
                {"action": "输出报告", "tool": "document.create",
                 "input": {"format": "md"}, "depends_on": ["${prev}"],
                 "description": "生成Markdown报告"},
            ]
        elif "知识" in user or "规范" in user or "SOP" in user:
            base = [
                {"action": "检索企业知识库", "tool": "knowledge.search",
                 "input": {"query": user}, "depends_on": [],
                 "description": "BM25+向量检索"},
                {"action": "按规范生成文档", "tool": "document.create",
                 "input": {"format": "md"}, "depends_on": ["${prev}"],
                 "description": "依检索结果成文"},
                {"action": "保存文件", "tool": "file.write",
                 "input": {}, "depends_on": ["${prev}"],
                 "description": "落盘保存"},
            ]
        elif "搜索" in user or "网页" in user or "research" in user.lower() or "研究" in user:
            base = [
                {"action": "网页搜索", "tool": "web.search",
                 "input": {"query": user}, "depends_on": [],
                 "description": "检索资料"},
                {"action": "汇总资料", "tool": "analysis.summarize",
                 "input": {}, "depends_on": ["${prev}"],
                 "description": "归纳要点"},
                {"action": "生成研究简报", "tool": "document.create",
                 "input": {"format": "md"}, "depends_on": ["${prev}"],
                 "description": "输出简报"},
            ]
        else:
            base = [
                {"action": "读取相关资料", "tool": "file.read",
                 "input": {"files": []}, "depends_on": [],
                 "description": "读取任务涉及文件"},
                {"action": "汇总生成结果", "tool": "analysis.summarize",
                 "input": {}, "depends_on": ["${prev}"],
                 "description": "生成结果"},
            ]
        # 把 ${prev} 占位符替换为真实 step 依赖（由 graph 层补全）
        return base

    @staticmethod
    def _extract_email(text: str) -> str:
        import re
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        return m.group(0) if m else ""

    @staticmethod
    def _detect_intent(user: str) -> str:
        for key, name in [("周报", "weekly_report"), ("纪要", "meeting"),
                          ("excel", "excel"), ("数据", "data"), ("分析", "analysis"),
                          ("知识", "knowledge"), ("规范", "knowledge"),
                          ("搜索", "web"), ("网页", "web"), ("研究", "research")]:
            if key in user.lower():
                return name
        return "generic"

    def _rule_text(self, user: str) -> str:
        intent = self._detect_intent(user)
        return f"[规则式降级] 意图={intent}。针对『{user}』生成的草稿。" \
               f"（配置 LLM_API_KEY 后可获得真实语义生成）"


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
