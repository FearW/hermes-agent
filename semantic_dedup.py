"""Semantic deduplication layer for search-like tool calls.

Sits above ``ToolExecutionCache``. Exact-hash caching misses when the
same topic is queried with different phrasings (e.g. ``怀化鹤城区今天天气``
vs ``怀化 天气 4月22日``). This layer normalizes queries and uses character
n-gram Jaccard similarity to detect near-duplicates within a session, plus
a per-topic call budget that forces the agent to stop repeating and
synthesize from prior results.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)


_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "for", "to", "is", "are",
    "and", "or", "with", "about", "now", "today", "current",
    "的", "了", "是", "在", "和", "与", "今天", "现在", "当前", "目前",
    "查询", "搜索", "查找", "获取", "一下", "请",
})


SEARCH_LIKE_TOOLS = frozenset({
    "web_search",
    "web_extract",
    "session_search",
    "mcp_minimax_search",
    "mcp_minim_search",
})


QUERY_ARG_BY_TOOL = {
    "web_search": "query",
    "web_extract": None,
    "session_search": "query",
    "mcp_minimax_search": "query",
    "mcp_minim_search": "query",
}


class SemanticDedupLayer:
    def __init__(
        self,
        similarity_threshold: float = 0.6,
        topic_budget: int = 4,
        ngram_n: int = 2,
        history_per_session: int = 50,
    ):
        self.similarity_threshold = similarity_threshold
        self.topic_budget = topic_budget
        self.ngram_n = ngram_n
        self.history_per_session = history_per_session
        self._history: dict[str, list[tuple[str, str, str]]] = {}
        self._topic_counts: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text).lower()
        tokens = [t for t in text.replace("\t", " ").split() if t and t not in _STOPWORDS]
        return " ".join(tokens)

    def _ngrams(self, text: str) -> set[str]:
        t = self._normalize(text).replace(" ", "")
        if len(t) < self.ngram_n:
            return {t} if t else set()
        return {t[i : i + self.ngram_n] for i in range(len(t) - self.ngram_n + 1)}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _extract_query(self, tool_name: str, args: dict) -> Optional[str]:
        arg_name = QUERY_ARG_BY_TOOL.get(tool_name, "query")
        if arg_name is None:
            return None
        val = args.get(arg_name)
        if isinstance(val, str) and val.strip():
            return val
        for fallback in ("query", "q", "search", "term", "prompt"):
            v = args.get(fallback)
            if isinstance(v, str) and v.strip():
                return v
        return None

    def _topic_key(self, query: str) -> str:
        tokens = [t for t in self._normalize(query).split() if t]
        return " ".join(sorted(tokens[:3])) if tokens else ""

    def check(
        self, session_id: Optional[str], tool_name: str, args: dict
    ) -> Optional[str]:
        if tool_name not in SEARCH_LIKE_TOOLS:
            return None
        query = self._extract_query(tool_name, args)
        if not query:
            return None
        sid = session_id or "_"
        with self._lock:
            topic = self._topic_key(query)
            counts = self._topic_counts.get(sid, {})
            if topic and counts.get(topic, 0) >= self.topic_budget:
                return self._synthesize_budget_response(sid, tool_name, topic)
            q_ngrams = self._ngrams(query)
            for past_tool, past_query, past_result in self._history.get(sid, []):
                if past_tool != tool_name:
                    continue
                sim = self._jaccard(q_ngrams, self._ngrams(past_query))
                if sim >= self.similarity_threshold:
                    logger.info(
                        "semantic_dedup HIT tool=%s sim=%.2f prev=%r cur=%r",
                        tool_name, sim, past_query, query,
                    )
                    return (
                        f"[semantic-dedup] A near-duplicate query "
                        f"(similarity {sim:.2f}) already ran in this session.\n"
                        f"Previous query: {past_query!r}\n\n"
                        f"Reusing prior result below — do NOT repeat this search "
                        f"with minor rephrasing. If you need more, pick a different "
                        f"angle or tool.\n\n{past_result}"
                    )
        return None

    def record(
        self, session_id: Optional[str], tool_name: str, args: dict, result: str
    ) -> None:
        if tool_name not in SEARCH_LIKE_TOOLS:
            return
        query = self._extract_query(tool_name, args)
        if not query:
            return
        sid = session_id or "_"
        with self._lock:
            history = self._history.setdefault(sid, [])
            history.append((tool_name, query, result))
            if len(history) > self.history_per_session:
                history.pop(0)
            topic = self._topic_key(query)
            if topic:
                counts = self._topic_counts.setdefault(sid, {})
                counts[topic] = counts.get(topic, 0) + 1

    def _synthesize_budget_response(
        self, sid: str, tool_name: str, topic: str
    ) -> str:
        items = [
            (q, (r or "")[:240])
            for t, q, r in self._history.get(sid, [])
            if t == tool_name and self._topic_key(q) == topic
        ]
        last = items[-self.topic_budget :]
        summary = "\n".join(f"- {q!r}: {preview}" for q, preview in last)
        logger.info(
            "semantic_dedup BUDGET topic=%r tool=%s count>=%d",
            topic, tool_name, self.topic_budget,
        )
        return (
            f"[semantic-dedup-budget] Topic {topic!r} has been searched "
            f"{self.topic_budget}+ times this session. STOP repeating. "
            f"Synthesize from the prior results below OR switch strategy "
            f"(different tool, different angle, ask the user).\n\n"
            f"Prior queries (last {len(last)}):\n{summary}"
        )

    def get_statistics(self) -> dict:
        with self._lock:
            return {
                "sessions_tracked": len(self._history),
                "total_records": sum(len(h) for h in self._history.values()),
                "topic_budget": self.topic_budget,
                "similarity_threshold": self.similarity_threshold,
            }
