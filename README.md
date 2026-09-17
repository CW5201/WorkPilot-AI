# WorkPilot 企业办公数字员工 · Multi-Agent 任务系统

面向企业办公场景的 **具体 Multi-Agent 应用**（不是 AgentOS / 通用 Agent 平台）：
员工用自然语言提交一个复杂办公任务，系统自动完成
「理解任务 → 自动规划 → 工具执行 → 结果验证 → 人工审批 → 最终交付 → 全链路 Trace」。

## 架构（5 个核心 Agent）

```
用户自然语言任务
  → Coordinator/Planner（拆任务 / 生成结构化 Plan / 缺参澄清）
  → Executor（按步骤调用 Tool；按需触发 Retriever）
  → Retriever（Agentic Retrieval，BM25 + BGE-M3，非必经节点）
  → Tool Registry / Governance（权限 → 风险 → HITL → 执行）
  → Artifact Store（小上下文传摘要+result_id，大结果外置落盘）
  → Reporter（基于 Artifact 摘要生成交付物，不重跑 Tool）
  → Evaluator（独立校验，PASS/RETRY_STEP/REPLAN/HUMAN）
  → 分级错误恢复（局部重试 ≤2 / 全局重规划 ≤2 / 超限转人工）
  → HITL（缺参澄清 + 高风险审批）
  → 最终交付 + Trace Timeline
```

## 目录

```
app/
  agents/          coordinator / executor / retriever / reporter / evaluator
  engine/          orchestrator（主链路编排 + 分级恢复 + HITL 闭环）
  tools/           Tool Registry + ToolGovernor + 具体工具实现
  retrieval/       Retriever（BM25 离线 + Milvus/BGE-M3 预留）
  artifacts/       Artifact Store（外置结果 + 内存/文件索引）
  memory/          Session / User / Task / Enterprise Knowledge
  trace/           Trace（jsonl 落盘 + Timeline）
  persistence/     任务状态持久化 + checkpoint + 重启恢复
  llm/             OpenAI 兼容客户端 + 离线规则式降级
  evaluation.py    评测框架（100 任务集，Multi vs Single Agent 对比）
  demo/run_demo.py 5 个端到端 Demo 场景
  api/ + main.py   FastAPI（任务创建/执行/审批/Trace/评测）
data/
  weekly_reports/  5 份部门周报（Demo 数据）
  meeting_notes/   会议纪要
  spreadsheets/    销售 Excel（JSON 表示，离线可读）
  knowledge/       企业知识库（SOP / 文档规范 / 审批规范）
  company.db       SQLite 演示库（sql.query 用）
```

## 快速开始

```bash
pip install -r requirements.txt

# 无 LLM key 也能跑通（规则式 Planner/Reporter 降级）：
python -m app.demo.run_demo --scenario 1          # 周报→PPT→邮件草稿（含审批）
python -m app.demo.run_demo --scenario 2          # 纪要→待办→日程→跟进邮件
python -m app.demo.run_demo --list                # 查看全部 5 个场景

# 启动 API（8000）
uvicorn app.main:app --reload --port 8000
curl -X POST localhost:8000/tasks -H 'Content-Type: application/json' \
     -d '{"instruction":"汇总本周5份部门周报并生成汇报邮件"}'
# 高风险任务挂起在 WAITING_APPROVAL 后：
curl -X POST localhost:8000/tasks/<id>/approve -d '{"approved":true}'

# 评测
python -c "from app.evaluation import run_benchmark; print(run_benchmark(n=10))"
```

## 接真实 LLM

`.env`：
```
LLM_BASE_URL=https://...  LLM_API_KEY=sk-...  LLM_MODEL=gpt-4o
```
未配置 key 时默认 `LLM_FALLBACK_RULE_BASED=true`，保证离线 demo 可跑通。

## 部署（Docker Compose）

```
docker compose up -d
```
起 FastAPI + Redis + PostgreSQL + Milvus（后三者按需要，离线 demo 不依赖外部服务）。

## 状态机

```
CREATED → PLANNING → READY → EXECUTING → EVALUATING
  ↳ WAITING_CLARIFICATION（缺参）
  ↳ WAITING_APPROVAL（高风险 HITL）
  ↳ RETRYING（局部，≤2）→ REPLANNING（全局，≤2）→ HUMAN_HANDOFF
  → COMPLETED / FAILED / HUMAN_HANDOFF
```

## 评测

`app/evaluation.py`：100 个固定任务（模板化生成），对比 Multi-Agent 与
Single-Agent（无规划/评估/恢复的顺序执行基线），统计
Task Success Rate / Avg Steps / Token Cost / Execution Time /
Tool Success Rate / Human Handoff Rate。**离线结果仅为流程验证，接真实 LLM 后重跑。**
