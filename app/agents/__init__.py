"""企业办公数字员工 Multi-Agent：5 个核心 Agent。

- coordinator.py  Coordinator / Planner
- retriever.py    Retriever（按需）
- executor.py     Executor
- reporter.py     Reporter
- evaluator.py    Evaluator
"""

from .coordinator import get_coordinator
from .executor import get_executor
from .retriever import get_retriever_agent
from .reporter import get_reporter
from .evaluator import get_evaluator

__all__ = [
    "get_coordinator", "get_executor", "get_retriever_agent",
    "get_reporter", "get_evaluator",
]
