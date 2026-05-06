from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.memory_manager import load_last_prefetch_snapshot
from hermes_constants import display_hermes_home, get_hermes_home
from tools.memory_tool import ENTRY_DELIMITER, _compact_memory_entries


@dataclass
class StoreHealth:
    name: str
    path: Path
    entries: int
    chars: int
    limit: int
    compacted_entries: int
    compacted_chars: int
    removed: int
    merged: int

    @property
    def pct(self) -> int:
        return min(100, int((self.chars / self.limit) * 100)) if self.limit > 0 else 0

    @property
    def status(self) -> str:
        if self.pct >= 90:
            return "needs attention"
        if self.pct >= 70 or self.removed or self.merged:
            return "can slim"
        return "healthy"

    @property
    def saved_chars(self) -> int:
        return max(0, self.chars - self.compacted_chars)


@dataclass
class L4Health:
    rows: int
    low_value_rows: int
    db_bytes: int
    status: str


def _read_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not raw.strip():
        return []
    return [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]


def _entry_chars(entries: list[str]) -> int:
    return len(ENTRY_DELIMITER.join(entries)) if entries else 0


def _store_health(name: str, path: Path, limit: int) -> StoreHealth:
    entries = _read_entries(path)
    compacted, stats = _compact_memory_entries(entries, include_similar=True)
    return StoreHealth(
        name=name,
        path=path,
        entries=len(entries),
        chars=_entry_chars(entries),
        limit=limit,
        compacted_entries=len(compacted),
        compacted_chars=_entry_chars(compacted),
        removed=int(stats.get("removed") or 0),
        merged=int(stats.get("merged") or 0),
    )


def _l4_health(home: Path) -> L4Health:
    db_path = home / "memory_l4" / "archive.db"
    if not db_path.exists():
        return L4Health(rows=0, low_value_rows=0, db_bytes=0, status="healthy")

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = int(conn.execute("SELECT COUNT(*) FROM l4_archive").fetchone()[0])
            low_value = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM l4_archive
                    WHERE priority < 4
                      AND unresolved = 0
                      AND conflict_flag = 0
                      AND COALESCE(linked_workflow, '') = ''
                      AND COALESCE(linked_skill, '') = ''
                      AND COALESCE(workflow_candidate, '') = ''
                      AND COALESCE(skill_candidate, '') = ''
                    """
                ).fetchone()[0]
            )
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return L4Health(rows=0, low_value_rows=0, db_bytes=db_path.stat().st_size, status="check failed")

    db_bytes = db_path.stat().st_size
    if rows >= 2000 or low_value >= 500:
        status = "can compact"
    else:
        status = "healthy"
    return L4Health(rows=rows, low_value_rows=low_value, db_bytes=db_bytes, status=status)


def analyze_memory_health() -> dict[str, Any]:
    home = get_hermes_home()
    mem_dir = home / "memories"
    memory = _store_health("MEMORY.md", mem_dir / "MEMORY.md", 2200)
    user = _store_health("USER.md", mem_dir / "USER.md", 1375)
    l4 = _l4_health(home)
    needs_action = any(store.status != "healthy" for store in (memory, user)) or l4.status != "healthy"
    return {"memory": memory, "user": user, "l4": l4, "needs_action": needs_action}


def _write_compacted(store: StoreHealth) -> bool:
    entries = _read_entries(store.path)
    compacted, _ = _compact_memory_entries(entries, include_similar=True)
    if compacted == entries:
        return False
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(ENTRY_DELIMITER.join(compacted), encoding="utf-8")
    return True


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _print_store(store: StoreHealth) -> None:
    print(f"- {store.name}: {store.entries} entries, {store.chars:,} / {store.limit:,} chars, {store.status}")
    if store.removed or store.merged:
        print(
            f"  可瘦身: {store.entries} → {store.compacted_entries} entries, "
            f"节省约 {store.saved_chars:,} chars ({store.removed} removed, {store.merged} merged)"
        )


def _print_prefetch_snapshot(home: Path) -> None:
    """Show last API-time memory injection stats (English field names in JSON file)."""
    data = load_last_prefetch_snapshot(home)
    print("\n上次记忆注入（本轮预取 → 模型用户消息，非持久化）")
    if not data:
        print(
            "  （尚无快照：需已配置外部 memory provider 并完成至少一轮带预取的对话；"
            "也可检查 memory.prefetch_snapshot 是否为 true）"
        )
        return
    print(f"  时间(UTC): {data.get('timestamp_utc', '?')}")
    if data.get("session_id"):
        print(f"  session_id: {data.get('session_id')}")
    if data.get("user_turn") is not None:
        print(f"  user_turn: {data.get('user_turn')}")
    print(f"  query_chars: {data.get('query_chars', '?')}")
    mc = data.get("max_chars")
    print(f"  prefetch_char_limit: {mc if mc is not None else 'unlimited'}")
    if data.get("truncated_by_provider_budget"):
        print(
            f"  截断: 是（provider 合并后超限 raw={data.get('raw_merged_chars')} "
            f"→ final_provider={data.get('provider_merged_chars')}）"
        )
    else:
        print("  截断(provider 预算): 否")
    prov = data.get("providers") or []
    if prov:
        parts = [f"{p.get('name')}:{p.get('chars')}" for p in prov if isinstance(p, dict)]
        print(f"  各 provider 原始长度: {', '.join(parts)}")
    else:
        print("  各 provider 原始长度: （无预取文本）")
    if int(data.get("episodic_excerpt_chars") or 0) > 0:
        print(f"  episodic 摘录字符: {data.get('episodic_excerpt_chars')}")
        if data.get("provider_trimmed_after_episodic"):
            print("  合并 episodic 后裁剪了 provider 预取: 是")
    print(f"  最终注入字符数: {data.get('final_injection_chars', '?')}")
    preview = data.get("injection_preview") or ""
    if preview:
        plines = preview.splitlines()
        print("  注入预览（前缀）:")
        for line in plines[:12]:
            print(f"    {line}")
        if len(plines) > 12:
            print("    …")
    print(
        "  图检索/统计: 使用已启用 provider 的工具（如 hindsight_recall）；"
        "运维级可视化属可选能力。"
    )


def run_memory_doctor(args) -> None:
    health = analyze_memory_health()
    memory: StoreHealth = health["memory"]
    user: StoreHealth = health["user"]
    l4: L4Health = health["l4"]
    home = get_hermes_home()

    print("\nMemory Health")
    print(f"Home: {display_hermes_home()}")
    _print_store(memory)
    _print_store(user)
    print(
        f"- L4 archive: {l4.rows} rows, {l4.low_value_rows} low-value candidates, "
        f"{_format_bytes(l4.db_bytes)}, {l4.status}"
    )

    compact = bool(getattr(args, "compact", False))
    if compact:
        changed = []
        for store in (memory, user):
            if _write_compacted(store):
                changed.append(store.name)
        try:
            from agent.l4_archive import compact_archive

            l4_result = compact_archive()
        except Exception:
            l4_result = {"deleted": 0, "remaining": l4.rows}
        if changed or l4_result.get("deleted"):
            print(
                f"\n已执行安全瘦身: {', '.join(changed) if changed else 'built-in memory unchanged'}; "
                f"L4 deleted {int(l4_result.get('deleted') or 0)} row(s)."
            )
        else:
            print("\n无需瘦身：当前记忆已经很干净。")
    elif health["needs_action"]:
        print("\nRecommendation: run `hermes memory doctor --compact` to apply safe compaction.")
    else:
        print("\nRecommendation: no action needed.")

    if not getattr(args, "no_prefetch_snapshot", False):
        _print_prefetch_snapshot(home)
    print()
