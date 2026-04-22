"""LLM 层：DeepSeek 客户端 + prompts + 诊断综合 + 候选分析。"""

from .client import DeepSeekClient, UsageRecord
from .synthesizer import analyze_candidate, synthesize_diagnosis

__all__ = [
    "DeepSeekClient",
    "UsageRecord",
    "analyze_candidate",
    "synthesize_diagnosis",
]
