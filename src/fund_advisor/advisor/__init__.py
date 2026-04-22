"""决策层：融合规则信号 + LLM 综合（阶段 4 接入）。"""

from .advisor import build_summary, run_stage1_diagnosis

__all__ = ["build_summary", "run_stage1_diagnosis"]
