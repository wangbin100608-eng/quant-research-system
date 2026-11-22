"""v0.3.0 P1-2：AI 研究智能体模块（8 个 Agent）。

职责边界（强制）：
  * LLM 只负责：提出假设、解释事件、整理研究报告。
  * 结构化预测、模型评分、规则升级由可复现程序完成。
  * Agent 不得直接把未验证假设写入 validated 规则；只能写 pending。

LLM 接口可注入（llm_call 函数），无 API key 时使用确定性模板 stub，保证实验可复现。
每个 Agent 调用记录：cutoff_date、snapshot_id、输入摘要、输出、时间。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable

from .knowledge_v03 import KnowledgeSystemV03, HypothesisStatus
from .logging_util import log


def deterministic_llm(prompt: str, context: dict | None = None) -> str:
    """无外部 LLM 时的确定性 stub：按 prompt 类型返回结构化文本。"""
    c = context or {}
    if "假设" in prompt or "hypothesis" in prompt.lower():
        return json.dumps({
            "hypothesis": "近20日动量与未来收益存在正相关（需样本外验证）",
            "rationale": "基于动量效应的公开研究结论；本系统必须独立验证",
            "falsification": "purged walk-forward 的 Rank IC <= 0 或方向准确率 < 0.5",
            "method": "purged_walk_forward",
            "generated_by": "deterministic_stub",
        }, ensure_ascii=False)
    if "解释" in prompt or "event" in prompt.lower():
        n = c.get("n_events", 0)
        return f"共观察到 {n} 条事件；事件影响需由程序化回测验证，LLM 仅作定性整理。"
    return json.dumps({"summary": "研究报告摘要（stub）", "context_keys": sorted(c.keys())},
                      ensure_ascii=False)


class ResearchAgent:
    def __init__(self, name: str, ks: KnowledgeSystemV03,
                 llm: Callable[[str, dict], str] = deterministic_llm):
        self.name = name
        self.ks = ks
        self.llm = llm
        self.call_log: list[dict[str, Any]] = []

    def _log(self, cutoff: date, snapshot_id: str, inp: dict, out: str):
        self.call_log.append({"agent": self.name, "cutoff": cutoff.isoformat(),
                              "snapshot_id": snapshot_id,
                              "input_summary": {k: str(v)[:80] for k, v in inp.items()},
                              "output": out[:500], "ts": datetime.utcnow().isoformat()})

    # ---- LLM 职责：假设 ----
    def propose_hypothesis(self, cutoff: date, snapshot_id: str,
                           context: dict, hid: str) -> dict:
        raw = self.llm(f"提出一个可证伪的研究假设（中文）: {self.name}", context)
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"hypothesis": raw[:200], "falsification": "IC<=0", "method": "walk_forward"}
        self.ks.create_hypothesis(
            hid, parsed.get("hypothesis", ""), cutoff,
            {"falsification": parsed.get("falsification"),
             "method": parsed.get("method"), "proposed_by": self.name},
            parsed.get("model", "linear"), snapshot_id)
        self._log(cutoff, snapshot_id, context, raw)
        return parsed

    # ---- 事件解释 ----
    def explain(self, cutoff: date, snapshot_id: str, context: dict) -> str:
        out = self.llm(f"解释以下事件并整理为研究意见（不得给出未经验证的交易建议）: {self.name}",
                       context)
        self._log(cutoff, snapshot_id, context, out)
        return out

    # ---- 报告整理 ----
    def report(self, cutoff: date, snapshot_id: str, context: dict) -> str:
        out = self.llm(f"整理研究报告摘要: {self.name}", context)
        self._log(cutoff, snapshot_id, context, out)
        return out

    # ---- 程序化评分（不由 LLM 决定）----
    def evaluate(self, cutoff: date, snapshot_id: str, metrics: dict) -> dict:
        from .validation import compute_metrics
        scored = compute_metrics(
            metrics.get("pred"), metrics.get("actual"),
            lower=metrics.get("lower"), upper=metrics.get("upper"),
            prob=metrics.get("prob"), groups=metrics.get("groups"))
        self.ks.add_structured(f"eval_{self.name}_{cutoff}", snapshot_id, "program:validation",
                               cutoff.isoformat(), scored)
        self._log(cutoff, snapshot_id, {"metrics_keys": list(metrics)}, json.dumps(scored))
        return scored


def build_agents(ks: KnowledgeSystemV03, llm=None) -> dict[str, ResearchAgent]:
    names = ["data_analysis", "news_event", "fundamental", "macro",
             "factor_research", "model_evaluation", "research_critic", "knowledge_manager"]
    return {n: ResearchAgent(n, ks, llm or deterministic_llm) for n in names}
