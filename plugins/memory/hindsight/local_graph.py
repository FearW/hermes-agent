import json
import math
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)


def _now_utc() -> float:
    return time.time()


def _stable_key(text: str) -> str:
    t = (text or "").strip()
    return sha1(t.encode("utf-8")).hexdigest()


def _tokenize(text: str, *, max_tokens: int = 200) -> List[str]:
    if not text:
        return []
    toks = [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]
    toks = [t for t in toks if len(t) >= 2]
    if len(toks) > max_tokens:
        toks = toks[:max_tokens]
    return toks


def _tf(text: str) -> Dict[str, float]:
    toks = _tokenize(text)
    if not toks:
        return {}
    c = Counter(toks)
    # L2-normalized term frequency vector
    norm = math.sqrt(sum(v * v for v in c.values())) or 1.0
    return {k: float(v) / norm for k, v in c.items()}


def _cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    s = 0.0
    for k, av in a.items():
        bv = b.get(k)
        if bv is not None:
            s += av * bv
    # Both vectors are L2-normalized.
    if s < 0.0:
        return 0.0
    if s > 1.0:
        return 1.0
    return s


@dataclass(frozen=True)
class RankedLine:
    key: str
    text: str
    score: float
    community_id: int


class LocalRecallGraph:
    """Lightweight local graph for recall routing + PPR reranking.

    This is intentionally dependency-free (no embedding APIs required):
    - Nodes are recall lines (or retained snippets) keyed by sha1(text).
    - Edges are co-occurrence weights within the same recall result set.
    - Communities are connected components over sufficiently-strong edges.
    - Community "summaries" are keyword bags derived from node text.
    - Routing matches query ↔ community summary using sparse cosine over TF.
    - Ranking uses Personalized PageRank over the candidate subgraph.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        min_edge_weight: float = 2.0,
        max_pair_edges_per_ingest: int = 40,
        community_rebuild_every_ingests: int = 15,
    ) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._min_edge_weight = float(min_edge_weight)
        self._max_pair_edges = int(max_pair_edges_per_ingest)
        self._rebuild_every = int(max(1, community_rebuild_every_ingests))
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
              key TEXT PRIMARY KEY,
              text TEXT NOT NULL,
              tf_json TEXT NOT NULL,
              community_id INTEGER NOT NULL DEFAULT 0,
              seen_count INTEGER NOT NULL DEFAULT 0,
              updated_at REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
              src TEXT NOT NULL,
              dst TEXT NOT NULL,
              weight REAL NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (src, dst)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS communities (
              id INTEGER PRIMARY KEY,
              summary TEXT NOT NULL,
              tf_json TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _meta_get_int(self, key: str, default: int = 0) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return int(default)
        try:
            return int(row["value"])
        except Exception:
            return int(default)

    def _meta_set_int(self, key: str, value: int) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(int(value))),
        )
        self._conn.commit()

    def ingest_recall_lines(self, lines: Sequence[str]) -> None:
        """Upsert nodes + add co-occurrence edges from one recall batch."""
        cleaned = [str(l).strip() for l in lines if l and str(l).strip()]
        if not cleaned:
            return
        # Keep only first N to limit O(n^2) edge updates.
        cleaned = cleaned[: min(len(cleaned), 25)]
        now = _now_utc()
        keys = []
        cur = self._conn.cursor()
        for text in cleaned:
            k = _stable_key(text)
            keys.append(k)
            tfj = json.dumps(_tf(text), ensure_ascii=False, sort_keys=True)
            cur.execute(
                """
                INSERT INTO nodes(key, text, tf_json, community_id, seen_count, updated_at)
                VALUES(?, ?, ?, 0, 1, ?)
                ON CONFLICT(key) DO UPDATE SET
                  text=excluded.text,
                  tf_json=excluded.tf_json,
                  seen_count=nodes.seen_count+1,
                  updated_at=excluded.updated_at
                """,
                (k, text, tfj, now),
            )
        # Co-occurrence edges (undirected stored as both directions).
        pair_budget = self._max_pair_edges
        for i in range(len(keys)):
            if pair_budget <= 0:
                break
            for j in range(i + 1, len(keys)):
                if pair_budget <= 0:
                    break
                a = keys[i]
                b = keys[j]
                if a == b:
                    continue
                pair_budget -= 1
                for src, dst in ((a, b), (b, a)):
                    cur.execute(
                        """
                        INSERT INTO edges(src, dst, weight, updated_at)
                        VALUES(?, ?, 1.0, ?)
                        ON CONFLICT(src, dst) DO UPDATE SET
                          weight=edges.weight+1.0,
                          updated_at=excluded.updated_at
                        """,
                        (src, dst, now),
                    )
        self._conn.commit()
        ingests = self._meta_get_int("ingest_count", 0) + 1
        self._meta_set_int("ingest_count", ingests)
        if ingests % self._rebuild_every == 0:
            self.rebuild_communities()

    def rebuild_communities(self) -> None:
        """Recompute communities as components over strong edges, then refresh summaries."""
        # Build adjacency from edges above threshold.
        rows = self._conn.execute(
            "SELECT src, dst, weight FROM edges WHERE weight >= ?",
            (self._min_edge_weight,),
        ).fetchall()
        if not rows:
            return
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            p = parent.get(x, x)
            if p != x:
                p = find(p)
                parent[x] = p
            return p

        def union(a: str, b: str) -> None:
            ra = find(a)
            rb = find(b)
            if ra != rb:
                parent[rb] = ra

        for r in rows:
            union(r["src"], r["dst"])

        # Map roots to compact community IDs.
        roots = {}
        next_id = 1
        for k in parent.keys():
            root = find(k)
            if root not in roots:
                roots[root] = next_id
                next_id += 1

        cur = self._conn.cursor()
        for k in parent.keys():
            cid = roots.get(find(k), 0)
            cur.execute("UPDATE nodes SET community_id=? WHERE key=?", (cid, k))
        self._conn.commit()
        self._refresh_community_summaries()

    def _refresh_community_summaries(self) -> None:
        # For each community, aggregate term frequencies across its nodes and keep top terms.
        rows = self._conn.execute(
            """
            SELECT community_id, tf_json, text
            FROM nodes
            WHERE community_id > 0
            """
        ).fetchall()
        if not rows:
            return
        agg: Dict[int, Counter] = {}
        exemplars: Dict[int, List[str]] = {}
        for r in rows:
            cid = int(r["community_id"])
            try:
                tfv = json.loads(r["tf_json"] or "{}")
            except Exception:
                tfv = {}
            c = agg.setdefault(cid, Counter())
            for k, v in tfv.items():
                try:
                    c[k] += float(v)
                except Exception:
                    continue
            ex = exemplars.setdefault(cid, [])
            if len(ex) < 4:
                ex.append((r["text"] or "")[:120])

        now = _now_utc()
        cur = self._conn.cursor()
        for cid, c in agg.items():
            # Remove generic glue tokens; keep a small keyword bag.
            for stop in ("the", "and", "with", "from", "this", "that", "you", "your", "for", "are", "was"):
                if stop in c:
                    del c[stop]
            keywords = [k for k, _ in c.most_common(18)]
            tfj = json.dumps(_tf(" ".join(keywords)), ensure_ascii=False, sort_keys=True)
            ex = exemplars.get(cid) or []
            summary = "keywords: " + ", ".join(keywords[:12])
            if ex:
                summary += " | exemplars: " + " / ".join(ex[:3])
            cur.execute(
                """
                INSERT INTO communities(id, summary, tf_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  summary=excluded.summary,
                  tf_json=excluded.tf_json,
                  updated_at=excluded.updated_at
                """,
                (cid, summary, tfj, now),
            )
        self._conn.commit()

    def rank_lines_ppr(
        self,
        *,
        query: str,
        candidate_lines: Sequence[str],
        max_return: int,
        community_top_k: int = 3,
        candidate_limit: int = 40,
        ppr_steps: int = 20,
        alpha: float = 0.15,
    ) -> List[str]:
        """Community routing + PPR reranking over the local co-occurrence graph.

        Returns a reordered subset of *candidate_lines* (max_return).
        """
        cleaned = [str(l).strip() for l in candidate_lines if l and str(l).strip()]
        if not cleaned:
            return []
        cleaned = cleaned[: min(len(cleaned), candidate_limit)]

        # 1) Pick communities via query ↔ community summary cosine.
        qtf = _tf(query or "")
        comm_rows = self._conn.execute("SELECT id, tf_json FROM communities").fetchall()
        comm_scores: List[Tuple[int, float]] = []
        for r in comm_rows:
            try:
                ctf = json.loads(r["tf_json"] or "{}")
            except Exception:
                ctf = {}
            comm_scores.append((int(r["id"]), _cosine_sparse(qtf, ctf)))
        comm_scores.sort(key=lambda x: x[1], reverse=True)
        picked = [cid for cid, sc in comm_scores[:community_top_k] if sc > 0.0]

        # 2) Seed nodes: candidates + community members
        cand_keys = {_stable_key(t): t for t in cleaned}
        if picked:
            q = ",".join("?" for _ in picked)
            rows = self._conn.execute(
                f"SELECT key, text, community_id FROM nodes WHERE community_id IN ({q}) ORDER BY seen_count DESC LIMIT 80",
                tuple(picked),
            ).fetchall()
            for r in rows:
                k = str(r["key"])
                if k not in cand_keys:
                    cand_keys[k] = str(r["text"])

        # 3) Build subgraph adjacency for candidate keys.
        keys = list(cand_keys.keys())
        if not keys:
            return cleaned[:max_return]
        q = ",".join("?" for _ in keys)
        edge_rows = self._conn.execute(
            f"SELECT src, dst, weight FROM edges WHERE src IN ({q}) AND dst IN ({q})",
            tuple(keys) + tuple(keys),
        ).fetchall()
        adj: Dict[str, List[Tuple[str, float]]] = {k: [] for k in keys}
        for r in edge_rows:
            src = str(r["src"])
            dst = str(r["dst"])
            w = float(r["weight"] or 0.0)
            if src in adj and dst in adj and w > 0.0:
                adj[src].append((dst, w))
        # Normalize outgoing weights.
        out_norm: Dict[str, float] = {}
        for k, nbrs in adj.items():
            s = sum(w for _, w in nbrs) or 0.0
            out_norm[k] = s

        # 4) Personalization vector: cosine(query, node_text) + bonus for community-picked.
        pers: Dict[str, float] = {}
        for k, text in cand_keys.items():
            ptf = _tf(text[:800])
            base = _cosine_sparse(qtf, ptf)
            pers[k] = base
        # If everything is zero (query empty or token mismatch), fall back to uniform over original candidates.
        if max(pers.values() or [0.0]) <= 0.0:
            pers = {k: (1.0 if k in cand_keys else 0.0) for k in keys}

        # Normalize personalization.
        psum = sum(pers.values()) or 1.0
        for k in pers:
            pers[k] /= psum

        # 5) Run PPR power iteration.
        pr = dict(pers)
        for _ in range(max(1, int(ppr_steps))):
            new_pr = {k: alpha * pers.get(k, 0.0) for k in keys}
            for src in keys:
                src_pr = pr.get(src, 0.0)
                denom = out_norm.get(src, 0.0)
                if denom <= 0.0:
                    # Dangling: distribute to personalization (restart distribution)
                    for k in keys:
                        new_pr[k] += (1.0 - alpha) * src_pr * pers.get(k, 0.0)
                    continue
                for dst, w in adj.get(src, []):
                    new_pr[dst] += (1.0 - alpha) * src_pr * (w / denom)
            pr = new_pr

        # 6) Produce ranked list, biasing toward original candidates.
        node_rows = self._conn.execute(
            f"SELECT key, community_id FROM nodes WHERE key IN ({q})",
            tuple(keys),
        ).fetchall()
        k2c = {str(r["key"]): int(r["community_id"] or 0) for r in node_rows}
        ranked: List[RankedLine] = []
        for k, text in cand_keys.items():
            ranked.append(
                RankedLine(
                    key=k,
                    text=text,
                    score=float(pr.get(k, 0.0)),
                    community_id=int(k2c.get(k, 0)),
                )
            )
        ranked.sort(key=lambda r: (r.score, r.community_id, len(r.text)), reverse=True)
        # Return only lines we actually saw from recall (avoid surprising injection growth).
        original_keys = {_stable_key(t) for t in cleaned}
        out: List[str] = [r.text for r in ranked if r.key in original_keys]
        if not out:
            out = cleaned
        return out[: max(1, int(max_return))]

