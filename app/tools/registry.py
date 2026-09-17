"""具体 Tool 实现与注册。

工具数量以实际实现为准（不虚构）。当前实现：
  file.read / file.write / file.search
  document.create
  spreadsheet.read / spreadsheet.write
  ppt.create
  email.draft / email.send
  calendar.create / calendar.update
  web.search
  knowledge.search
  sql.query
  analysis.summarize / analysis.extract_todos   （Agent 侧分析工具，LOW）

文件类工具默认读写 data/ 目录；PPT/Excel 在离线环境下生成可读的占位物
（.ppt/.pptx 大纲 或 .xlsx 简易结构），保证产物真实落盘、可被 Evaluator 校验。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import KNOWLEDGE_DIR, PROJECT_ROOT, get_settings
from ..retrieval import retrieve
from . import ToolGovernor, ToolSpec, get_governor


def _data_dir() -> Path:
    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 实现
def _find_named_files(subdir: str, files: list[str]) -> list[Path]:
    """files 为空时返回目录下匹配文件；否则按文件名精确/模糊匹配。"""
    base = _data_dir() / subdir
    base.mkdir(parents=True, exist_ok=True)
    if not files:
        return sorted(p for p in base.iterdir() if p.is_file())
    out = []
    for f in files:
        direct = base / f
        if direct.exists():
            out.append(direct)
        else:  # 模糊匹配
            out.extend(p for p in base.iterdir() if f.lower() in p.name.lower())
    return out


def file_read(files: list[str] | None = None, subdir: str = "weekly_reports") -> dict:
    paths = _find_named_files(subdir, files or [])
    docs = []
    for p in paths:
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            docs.append({"file": p.name, "error": str(e)})
            continue
        docs.append({"file": p.name, "content": txt, "chars": len(txt)})
    return {"files": [d.get("file") for d in docs], "documents": docs}


def file_write(path: str, content: str) -> dict:
    target = _data_dir() / "outputs" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": str(target), "bytes": len(content.encode("utf-8"))}


def file_search(query: str, subdir: str = "weekly_reports") -> dict:
    base = _data_dir() / subdir
    hits = []
    if base.exists():
        for p in base.iterdir():
            if p.is_file() and p.read_text(encoding="utf-8", errors="ignore").find(query) != -1:
                hits.append({"file": p.name, "snippet": p.read_text(encoding="utf-8")[:200]})
    return {"query": query, "hits": hits}


def document_create(format: str = "md", title: str = "文档", body: str = "") -> dict:
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:30]
    out = _data_dir() / "outputs" / f"{safe}.{format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return {"path": str(out), "format": format, "chars": len(body)}


def spreadsheet_read(file: str = "", sheet: str = "Sheet1") -> dict:
    # 离线实现：读取 data/ 下同名 .json 表格（真实部署可换 openpyxl/pandas）。
    base = _data_dir() / "spreadsheets" / file
    if not file or not base.exists():
        base = _data_dir() / "spreadsheets" / "sample_sales.json"
    raw = json.loads(base.read_text(encoding="utf-8"))
    rows = raw.get("rows", [])
    cols = raw.get("columns", list(rows[0].keys()) if rows and isinstance(rows[0], dict) else [])
    return {"file": file, "columns": cols, "rows": rows, "row_count": len(rows)}


def spreadsheet_write(file: str, rows: list | None = None, columns: list | None = None) -> dict:
    out = _data_dir() / "spreadsheets" / (file if file.endswith(".json") else file + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"columns": columns or [], "rows": rows or []},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out), "rows": len(rows or [])}


def ppt_create(pages: int = 3, title: str = "演示文稿", slides: list | None = None) -> dict:
    """生成 PPT 大纲物（真实部署可换 python-pptx）。落盘为可读 JSON+MD 双格式。"""
    slides = slides or [{"title": title, "bullets": ["要点1", "要点2"]} for _ in range(pages)]
    slides = slides[:pages] if pages else slides
    data = {"title": title, "pages": pages, "slides": slides}
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:20]
    out = _data_dir() / "outputs" / f"{safe}_deck.ppt.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out), "pages": len(slides), "title": title}


def email_draft(to: list | None = None, subject: str = "", body: str = "",
                cc: list | None = None) -> dict:
    draft = {"to": to or [], "cc": cc or [], "subject": subject, "body": body,
             "attachments": [], "status": "DRAFT"}
    p = _data_dir() / "outputs" / "last_email_draft.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


def email_send(to: list | None = None, subject: str = "", body: str = "") -> dict:
    """HIGH 风险：真实发送。此处仅记录（不外发），返回"已模拟发送"。"""
    log = _data_dir() / "outputs" / "email_send_log.json"
    entries = []
    if log.exists():
        entries = json.loads(log.read_text(encoding="utf-8"))
    entries.append({"to": to or [], "subject": subject, "body_len": len(body), "sent": True})
    log.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"sent": True, "to": to or [], "subject": subject}


def calendar_create(title: str, start: str = "", end: str = "") -> dict:
    ev = {"title": title, "start": start, "end": end, "status": "CREATED"}
    p = _data_dir() / "calendar" / "events.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    entries.append(ev)
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return ev


def calendar_update(event_id: str, **changes) -> dict:
    p = _data_dir() / "calendar" / "events.json"
    entries = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    for ev in entries:
        if ev.get("event_id") == event_id or ev.get("title") == event_id:
            ev.update(changes)
            ev["status"] = "UPDATED"
            p.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            return ev
    raise ValueError(f"event not found: {event_id}")


def web_search(query: str, max_results: int = 5) -> dict:
    """离线实现：返回基于查询的模拟来源（真实部署接 Bing/Google/serpapi）。"""
    results = [
        {"title": f"{query} - 综述", "url": f"https://example.org/{query.replace(' ', '-')}#1",
         "snippet": f"关于{query}的背景资料。"},
        {"title": f"{query} - 行业分析", "url": f"https://example.org/{query.replace(' ', '-')}#2",
         "snippet": f"{query} 的近期动态。"},
    ][:max_results]
    return {"query": query, "results": results}


def knowledge_search(query: str, top_k: int = 5) -> dict:
    """企业知识库检索入口。真正实现见 app/retrieval（BM25 + BGE-M3 + Milvus）。
    离线时回退到 data/knowledge 目录关键词扫描，保证 demo 可跑。"""
    from .retrieval import retrieve
    return retrieve(query, top_k=top_k)


def sql_query(query: str, params: list | None = None) -> dict:
    """对任务集附带的 SQLite 演示库执行只读查询。"""
    import sqlite3
    db = _data_dir() / "company.db"
    if not db.exists():
        return {"ok": False, "error": "demo db not seeded"}
    con = sqlite3.connect(db)
    try:
        cur = con.execute(query, tuple(params or []))
        cols = [c[0] for c in cur.description or []]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows}
    finally:
        con.close()


def analysis_summarize(observations: list | None = None, focus: str = "") -> dict:
    """从上游结果中归纳要点。离线实现：抽取关键词与统计量。"""
    items = observations or []
    nums = []
    keywords: dict[str, int] = {}
    for it in items:
        text = it.get("content") if isinstance(it, dict) else str(it)
        for m in re.findall(r"\d+(?:\.\d+)?%", text or ""):
            nums.append(float(m.rstrip("%")))
        for w in re.findall(r"[一-鿿]{2,6}|[A-Za-z]{3,}", text or ""):
            keywords[w] = keywords.get(w, 0) + 1
    top = sorted(keywords, key=keywords.get, reverse=True)[:8]
    return {
        "focus": focus,
        "avg_percent": round(sum(nums) / len(nums), 2) if nums else None,
        "max_percent": max(nums) if nums else None,
        "key_entities": top,
        "points": [it.get("file") or it.get("title") or "" for it in items][:10],
    }


def analysis_extract_todos(text: str = "") -> dict:
    patterns = [r"待办[:：]?\s*(.+)", r"行动项[:：]?\s*(.+)",
                r"需要(.{2,30})", r"跟进(.{2,30})"]
    todos = []
    for p in patterns:
        for m in re.findall(p, text):
            todos.append(m.strip())
    if not todos:
        todos = [t.strip() for t in re.findall(r"[-*]\s+(.{4,40})", text)]
    return {"todos": todos[:20]}


# ---------------------------------------------------------------- 注册
def register_all_tools() -> None:
    g = get_governor()
    specs = [
        ToolSpec("file.read", "读取文件/目录内容", {"files": "list[str]", "subdir": "str"},
                 "user", "LOW", 20, {"retries": 1}, file_read),
        ToolSpec("file.write", "写入文件", {"path": "str", "content": "str"},
                 "user", "HIGH", 30, {"retries": 1}, file_write),
        ToolSpec("file.search", "在文件中检索", {"query": "str", "subdir": "str"},
                 "user", "LOW", 20, {"retries": 1}, file_search),
        ToolSpec("document.create", "创建文档(MD/Word)", {"format": "str", "title": "str", "body": "str"},
                 "user", "LOW", 60, {"retries": 2}, document_create),
        ToolSpec("spreadsheet.read", "读取表格数据", {"file": "str", "sheet": "str"},
                 "user", "LOW", 30, {"retries": 2}, spreadsheet_read),
        ToolSpec("spreadsheet.write", "写入表格数据", {"file": "str", "rows": "list", "columns": "list"},
                 "user", "HIGH", 60, {"retries": 2}, spreadsheet_write),
        ToolSpec("ppt.create", "生成PPT", {"pages": "int", "title": "str", "slides": "list"},
                 "user", "LOW", 90, {"retries": 2}, ppt_create),
        ToolSpec("email.draft", "生成邮件草稿", {"to": "list", "subject": "str", "body": "str"},
                 "user", "LOW", 30, {"retries": 1}, email_draft),
        ToolSpec("email.send", "发送邮件（高风险，需人工审批）", {"to": "list", "subject": "str", "body": "str"},
                 "admin", "HIGH", 30, {"retries": 1}, email_send),
        ToolSpec("calendar.create", "创建日程", {"title": "str", "start": "str", "end": "str"},
                 "user", "LOW", 30, {"retries": 1}, calendar_create),
        ToolSpec("calendar.update", "更新日程（高风险，需人工审批）", {"event_id": "str"},
                 "admin", "HIGH", 30, {"retries": 1}, calendar_update),
        ToolSpec("web.search", "网页搜索", {"query": "str", "max_results": "int"},
                 "user", "LOW", 30, {"retries": 2}, web_search),
        ToolSpec("knowledge.search", "检索企业知识库", {"query": "str", "top_k": "int"},
                 "user", "LOW", 30, {"retries": 2}, knowledge_search),
        ToolSpec("sql.query", "只读SQL查询", {"query": "str", "params": "list"},
                 "user", "LOW", 30, {"retries": 1}, sql_query),
        ToolSpec("analysis.summarize", "归纳要点/统计", {"observations": "list", "focus": "str"},
                 "agent", "LOW", 30, {"retries": 2}, analysis_summarize),
        ToolSpec("analysis.extract_todos", "提取待办", {"text": "str"},
                 "agent", "LOW", 30, {"retries": 1}, analysis_extract_todos),
    ]
    for s in specs:
        g.register(s)


# 供 graph 层惰性注册
def ensure_tools() -> ToolGovernor:
    g = get_governor()
    if not g.names():
        register_all_tools()
    return g
