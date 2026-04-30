from __future__ import annotations

import re
from typing import Any, Dict, List

from hermes_cli.workflow import _ensure_dirs, _load_yaml, _update_workflow_metadata, DEFS_DIR


def recommend_workflows_for_text(message: str, limit: int = 3) -> List[Dict[str, Any]]:
    _ensure_dirs()
    query_terms = {tok for tok in re.findall(r'[a-zA-Z0-9_*.\-/]+', str(message or '').lower()) if len(tok) >= 3}
    if not query_terms:
        return []
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for path in sorted(DEFS_DIR.glob('*.yaml')):
        try:
            data = _load_yaml(path)
        except Exception:
            continue
        haystack = '\n'.join([
            str(data.get('name') or ''),
            str(data.get('description') or ''),
            str(data.get('prompt_template') or ''),
            ' '.join(data.get('inputs', {}).get('globs') or []),
            str(data.get('outputs', {}).get('write_to') or ''),
        ]).lower()
        score = 0
        for term in query_terms:
            if term in haystack:
                score += 3 if len(term) > 5 else 1
        if score > 0:
            ranked.append((score, {
                'name': data.get('name') or path.stem,
                'description': data.get('description') or '',
                'prompt_template': data.get('prompt_template') or '',
                'watch': data.get('watch') or {},
                'outputs': data.get('outputs') or {},
                'score': score,
                'metadata': data.get('metadata') or {},
                'category': (data.get('metadata') or {}).get('category', ''),
                'priority': int((data.get('metadata') or {}).get('priority', 0) or 0),
            }))
    ranked.sort(key=lambda item: (-item[0], item[1]['name']))
    return [item[1] for item in ranked[:limit]]


def _record_workflow_match(name: str) -> None:
    def _mutate(metadata: Dict[str, Any]) -> None:
        count = int(metadata.get("auto_match_count", 0) or 0) + 1
        metadata["auto_match_count"] = count
        metadata["last_auto_matched_at"] = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
        if metadata.get("auto_draft") and count >= 3:
            metadata["recommended"] = True
            metadata["promotion_reason"] = "auto_match_threshold"
    try:
        _update_workflow_metadata(name, _mutate)
    except Exception:
        pass


def build_workflow_recommendation_note(message: str, limit: int = 2) -> str:
    matches = recommend_workflows_for_text(message, limit=limit)
    if not matches:
        return ''
    lines = [
        "[SYSTEM: Hermes detected existing reusable workflows relevant to this request. Prefer reusing these patterns if they fit the user's intent.]",
        '',
    ]
    for match in matches:
        _record_workflow_match(match["name"])
        metadata = match.get('metadata') or {}
        badge = []
        if metadata.get('recommended'):
            badge.append('recommended')
        if metadata.get('auto_draft'):
            badge.append('auto-draft')
        suffix = f" ({', '.join(badge)})" if badge else ''
        lines.append(f"- Workflow `{match['name']}`{suffix}")
        if match.get('category'):
            lines.append(f"  Category: {match['category']}")
        if match.get('priority'):
            lines.append(f"  Priority: {match['priority']}/5")
        if match.get('description'):
            lines.append(f"  Description: {match['description']}")
        outputs = match.get('outputs') or {}
        if outputs.get('write_to'):
            lines.append(f"  Default output: {outputs['write_to']}")
        watch = match.get('watch') or {}
        if watch.get('enabled') and watch.get('path'):
            lines.append(f"  Watched path: {watch['path']}")
        prompt_template = str(match.get('prompt_template') or '').strip()
        if prompt_template:
            lines.append(f"  Template: {prompt_template[:220]}")
        lines.append('')
    return '\n'.join(lines).strip()
