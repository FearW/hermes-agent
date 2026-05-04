"""Workflow management for Hermes CLI.

Workflows are reusable task definitions that can be saved from prior sessions,
run on demand, attached to folder watchers, or handed off to cron for managed
execution. They reuse the cron/runtime stack so delivery and output behavior
stay consistent with the rest of Hermes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.colors import Colors, color
from hermes_constants import get_hermes_home


HERMES_HOME = get_hermes_home().resolve()
WORKFLOW_DIR = HERMES_HOME / "workflows"
DEFS_DIR = WORKFLOW_DIR / "definitions"
RUNS_DIR = WORKFLOW_DIR / "runs"
WATCHERS_FILE = WORKFLOW_DIR / "watchers.json"
WATCH_STATE_DIR = WORKFLOW_DIR / "watch_state"

_PATH_RE = re.compile(r"(?P<path>(?:~?/|/)?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._*?-]+)+)")
_FILE_HINT_RE = re.compile(r"(?P<file>[A-Za-z0-9._/-]+\.(?:md|markdown|txt|json|docx|csv|html))", re.IGNORECASE)
_GLOB_CHARS = set("*?[")


def _ensure_dirs() -> None:
    for path in (WORKFLOW_DIR, DEFS_DIR, RUNS_DIR, WATCH_STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return text or f"workflow-{uuid.uuid4().hex[:8]}"


def _workflow_path(name: str) -> Path:
    return DEFS_DIR / f"{_slugify(name)}.yaml"


def _state_path(name: str) -> Path:
    return WATCH_STATE_DIR / f"{_slugify(name)}.json"


def _load_yaml(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Workflow file must contain a mapping: {path}")
    return data


def _dump_yaml(data: Dict[str, Any], path: Path) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _update_workflow_metadata(name: str, mutator) -> Dict[str, Any]:
    path = _workflow_path(name)
    data = _load_yaml(path)
    metadata = data.setdefault("metadata", {})
    mutator(metadata)
    _dump_yaml(data, path)
    return data


def _read_watchers() -> List[Dict[str, Any]]:
    if not WATCHERS_FILE.exists():
        return []
    data = json.loads(WATCHERS_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _write_watchers(watchers: List[Dict[str, Any]]) -> None:
    WATCHERS_FILE.write_text(json.dumps(watchers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_list(values: Optional[Iterable[str]]) -> List[str]:
    result: List[str] = []
    if not values:
        return result
    for value in values:
        for part in str(value or "").split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def _session_messages(session_id: str) -> List[Dict[str, Any]]:
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        payload = db.export_session(session_id)
        if not payload:
            raise ValueError(f"Session not found: {session_id}")
        msgs = payload.get("messages") or []
        return msgs if isinstance(msgs, list) else []
    finally:
        db.close()


def _session_user_messages(messages: List[Dict[str, Any]]) -> List[str]:
    user_messages = [str(m.get("content") or "").strip() for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    return [m for m in user_messages if m]


def _extract_prompt_from_session(messages: List[Dict[str, Any]]) -> str:
    user_messages = _session_user_messages(messages)
    if not user_messages:
        raise ValueError("Session has no user messages to build a workflow from")
    if len(user_messages) == 1:
        return user_messages[0]
    joined = []
    for idx, msg in enumerate(user_messages, 1):
        joined.append(f"Step {idx}: {msg}")
    return "\n\n".join(joined)


def _infer_inputs_outputs(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    paths: List[str] = []
    globs: List[str] = []
    output_file: Optional[str] = None
    watch_path: Optional[str] = None
    watch_patterns: List[str] = []
    variables: List[Dict[str, str]] = []

    for msg in _session_user_messages(messages):
        lower = msg.lower()
        output_candidates: List[str] = []
        if any(token in lower for token in ("save", "write", "export", "generate")):
            for match in _FILE_HINT_RE.finditer(msg):
                candidate = match.group("file").strip().strip("`\"'(),")
                if candidate:
                    output_candidates.append(candidate)
                    if output_file is None:
                        output_file = candidate

        for match in _PATH_RE.finditer(msg):
            candidate = match.group("path").strip().strip("`\"'(),")
            if not candidate or candidate in output_candidates:
                continue
            if any(ch in candidate for ch in _GLOB_CHARS):
                if candidate not in globs:
                    globs.append(candidate)
                parent = str(Path(candidate).parent)
                if parent not in (".", "") and watch_path is None:
                    watch_path = parent
                pattern = Path(candidate).name
                if pattern and pattern not in watch_patterns:
                    watch_patterns.append(pattern)
            else:
                if candidate not in paths:
                    paths.append(candidate)

    for idx, _candidate in enumerate(paths, 1):
        variables.append({"name": f"input_{idx}", "description": f"Captured input path {idx}"})
    if globs and not any(v["name"] == "input_paths" for v in variables):
        variables.append({"name": "input_paths", "description": "Newline-separated input paths"})
    if not variables:
        variables.append({"name": "input_paths", "description": "Newline-separated input paths"})

    fallback_patterns = [Path(g).name for g in globs if Path(g).name] if globs else ["*"]
    return {
        "paths": paths,
        "globs": globs,
        "write_to": output_file,
        "watch_path": watch_path,
        "watch_patterns": watch_patterns or fallback_patterns,
        "variables": variables,
    }


def _apply_capture_template(prompt: str, inferred: Dict[str, Any]) -> str:
    updated = prompt
    for idx, path in enumerate(inferred.get("paths") or [], 1):
        updated = updated.replace(path, f"{{{{input_{idx}}}}}")
    for glob in inferred.get("globs") or []:
        updated = updated.replace(glob, "{{input_paths}}")
    return updated


def _build_workflow_from_session(name: str, session_id: str) -> Dict[str, Any]:
    messages = _session_messages(session_id)
    prompt = _extract_prompt_from_session(messages)
    inferred = _infer_inputs_outputs(messages)
    templated_prompt = _apply_capture_template(prompt, inferred)
    return {
        "name": name,
        "description": f"Saved from session {session_id}",
        "prompt_template": templated_prompt,
        "inputs": {"paths": inferred["paths"], "globs": inferred["globs"]},
        "outputs": {"format": "markdown", "write_to": inferred["write_to"], "also_save_run_copy": True},
        "skills": [],
        "provider": None,
        "model": None,
        "deliver": "local",
        "watch": {
            "enabled": bool(inferred["watch_path"]),
            "path": inferred["watch_path"],
            "patterns": inferred["watch_patterns"],
            "recursive": False,
            "settle_seconds": 3,
        },
        "managed": {"enabled": False, "schedule": None, "job_id": None},
        "metadata": {"source_session_id": session_id, "variables": inferred["variables"]},
    }


def _load_workflow(name: str) -> tuple[Path, Dict[str, Any]]:
    path = _workflow_path(name)
    if not path.exists():
        raise ValueError(f"Workflow not found: {name}")
    data = _load_yaml(path)
    return path, data


def _save_workflow(name: str, data: Dict[str, Any]) -> Path:
    _ensure_dirs()
    data = dict(data)
    data["name"] = name
    path = _workflow_path(name)
    _dump_yaml(data, path)
    return path


def _render_prompt(template: str, variables: Dict[str, Any]) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def _build_run_variables(workflow: Dict[str, Any], args) -> Dict[str, Any]:
    input_paths = [str(Path(p).expanduser().resolve()) for p in _normalize_list(getattr(args, "input", None) or workflow.get("inputs", {}).get("paths"))]
    variables = {
        "input_paths": "\n".join(input_paths),
        "input_count": len(input_paths),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for idx, path in enumerate(input_paths, 1):
        variables[f"input_{idx}"] = path
    if getattr(args, "vars", None):
        for raw in args.vars:
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            variables[key.strip()] = value.strip()
    return variables


def _workflow_output_path(workflow: Dict[str, Any], run_id: str, override: Optional[str]) -> Path:
    configured = override if override is not None else workflow.get("outputs", {}).get("write_to")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = HERMES_HOME / path
        return path
    stem = _slugify(workflow.get("name", "workflow"))
    return RUNS_DIR / f"{stem}_{run_id}.md"


def _execute_with_agent(prompt: str, workflow: Dict[str, Any], session_source: str) -> Dict[str, Any]:
    from cron.scheduler import _build_job_prompt
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from dotenv import load_dotenv
    from hermes_cli.config import load_config
    from hermes_constants import apply_ipv4_preference, parse_reasoning_effort
    from hermes_cli.runtime_provider import resolve_runtime_provider, format_runtime_provider_error
    from agent.smart_model_routing import resolve_turn_route, resolve_turn_toolsets

    cfg = load_config() or {}
    try:
        load_dotenv(str(HERMES_HOME / ".env"), override=True, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(str(HERMES_HOME / ".env"), override=True, encoding="latin-1")

    if isinstance(cfg.get("network"), dict) and cfg.get("network", {}).get("force_ipv4"):
        apply_ipv4_preference(force=True)

    effort = str(cfg.get("agent", {}).get("reasoning_effort", "")).strip()
    reasoning_config = parse_reasoning_effort(effort)
    pr = cfg.get("provider_routing", {})
    smart_routing = cfg.get("smart_model_routing", {}) or {}

    runtime_kwargs = {"requested": "cliproxyapi"}
    if workflow.get("base_url"):
        runtime_kwargs["explicit_base_url"] = workflow.get("base_url")
    try:
        runtime = resolve_runtime_provider(**runtime_kwargs)
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    model_cfg = cfg.get("model", {})
    default_model = model_cfg.get("default") if isinstance(model_cfg, dict) else model_cfg
    model = workflow.get("model") or default_model or os.getenv("HERMES_MODEL") or ""
    route = resolve_turn_route(
        prompt,
        smart_routing,
        {
            "model": model,
            "api_key": runtime.get("api_key"),
            "base_url": runtime.get("base_url"),
            "provider": runtime.get("provider"),
            "api_mode": runtime.get("api_mode"),
            "command": runtime.get("command"),
            "args": list(runtime.get("args") or []),
        },
    )
    route_toolsets = resolve_turn_toolsets(route, ["all"])

    pre_job = {"prompt": prompt, "skills": workflow.get("skills") or [], "skill": (workflow.get("skills") or [None])[0], "script": workflow.get("script")}
    effective_prompt = _build_job_prompt(pre_job)

    session_id = f"workflow_{_slugify(workflow.get('name', 'workflow'))}_{time.strftime('%Y%m%d_%H%M%S')}"
    session_db = SessionDB()
    try:
        agent = AIAgent(
            model=route["model"],
            api_key=route["runtime"].get("api_key"),
            base_url=route["runtime"].get("base_url"),
            provider=route["runtime"].get("provider"),
            api_mode=route["runtime"].get("api_mode"),
            acp_command=route["runtime"].get("command"),
            acp_args=route["runtime"].get("args"),
            max_iterations=cfg.get("agent", {}).get("max_turns") or cfg.get("max_turns") or 60,
            reasoning_config=reasoning_config,
            fallback_model=cfg.get("fallback_providers") or cfg.get("fallback_model") or None,
            providers_allowed=pr.get("only"),
            providers_ignored=pr.get("ignore"),
            providers_order=pr.get("order"),
            provider_sort=pr.get("sort"),
            enabled_toolsets=route_toolsets,
            task_mode=route.get("task_mode"),
            disabled_toolsets=["clarify"] if route_toolsets != [] else None,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            platform="workflow",
            session_id=session_id,
            session_db=session_db,
        )
        result = agent.run_conversation(effective_prompt)
        return {"session_id": session_id, "result": result, "effective_prompt": effective_prompt}
    finally:
        try:
            session_db.end_session(session_id, session_source)
        except Exception:
            pass
        session_db.close()


def _write_result(output_path: Path, workflow: Dict[str, Any], prompt: str, response: str, session_id: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = f"# Workflow: {workflow.get('name')}\n\n## Prompt\n\n{prompt}\n\n## Response\n\n{response}\n\n## Session\n\n- session_id: {session_id}\n"
    output_path.write_text(body, encoding="utf-8")


def workflow_list(args) -> int:
    _ensure_dirs()
    items = sorted(DEFS_DIR.glob("*.yaml"))
    if not items:
        print(color("No workflows saved.", Colors.DIM))
        return 0
    watchers = {entry.get("workflow"): entry for entry in _read_watchers()}
    for path in items:
        data = _load_yaml(path)
        name = data.get("name") or path.stem
        managed = data.get("managed", {}) or {}
        watch = watchers.get(name) or data.get("watch") or {}
        status = []
        if managed.get("enabled") and managed.get("job_id"):
            status.append(f"cron:{managed.get('job_id')}")
        if watch.get("enabled"):
            status.append(f"watch:{watch.get('path')}")
        desc = data.get("description") or ""
        print(f"{name:<28} {desc}")
        if status:
            print(f"  {'; '.join(status)}")
    return 0


def workflow_create(args) -> int:
    data = {
        "name": args.name,
        "description": args.description or "",
        "prompt_template": args.prompt,
        "inputs": {"paths": _normalize_list(getattr(args, "input", None)), "globs": _normalize_list(getattr(args, "glob", None))},
        "outputs": {"format": args.output_format or "markdown", "write_to": args.write_to, "also_save_run_copy": True},
        "skills": _normalize_list(getattr(args, "skills", None)),
        "provider": "cliproxyapi",
        "model": getattr(args, "model", None),
        "deliver": getattr(args, "deliver", None) or "local",
        "watch": {"enabled": False, "path": None, "patterns": ["*"], "recursive": False, "settle_seconds": 3},
        "managed": {"enabled": False, "schedule": None, "job_id": None},
        "metadata": {},
    }
    path = _save_workflow(args.name, data)
    print(color(f"Saved workflow: {args.name}", Colors.GREEN))
    print(f"  File: {path}")
    return 0


def workflow_capture(args) -> int:
    workflow = _build_workflow_from_session(args.name, args.session_id)
    path = _save_workflow(args.name, workflow)
    print(color(f"Captured workflow: {args.name}", Colors.GREEN))
    print(f"  File: {path}")
    return 0


def workflow_show(args) -> int:
    path, data = _load_workflow(args.name)
    print(f"# {data.get('name', path.stem)}")
    print(f"file: {path}")
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=False))
    return 0


def workflow_run(args) -> int:
    _, workflow = _load_workflow(args.name)
    variables = _build_run_variables(workflow, args)
    prompt = _render_prompt(str(workflow.get("prompt_template") or ""), variables)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    executed = _execute_with_agent(prompt, workflow, session_source="workflow_complete")
    result = executed["result"]
    response = str(result.get("final_response") or "")
    output_path = _workflow_output_path(workflow, run_id, getattr(args, "write_to", None))
    _write_result(output_path, workflow, executed["effective_prompt"], response, executed["session_id"])
    print(color(f"Workflow completed: {workflow.get('name')}", Colors.GREEN))
    print(f"  Session: {executed['session_id']}")
    print(f"  Output:  {output_path}")
    if response.strip():
        print()
        print(response)
    return 0


def workflow_delete(args) -> int:
    path = _workflow_path(args.name)
    if not path.exists():
        print(color(f"Workflow not found: {args.name}", Colors.RED))
        return 1
    path.unlink()
    watchers = [w for w in _read_watchers() if w.get("workflow") != args.name]
    _write_watchers(watchers)
    print(color(f"Deleted workflow: {args.name}", Colors.GREEN))
    return 0


def workflow_watch_set(args) -> int:
    path, data = _load_workflow(args.name)
    watch_cfg = data.get("watch", {}) or {}
    watch_cfg.update({
        "enabled": True,
        "path": str(Path(args.path).expanduser().resolve()),
        "patterns": _normalize_list(args.pattern) or ["*"],
        "recursive": bool(args.recursive),
        "settle_seconds": int(args.settle_seconds),
    })
    data["watch"] = watch_cfg
    _dump_yaml(data, path)
    watchers = [w for w in _read_watchers() if w.get("workflow") != args.name]
    watchers.append({"workflow": args.name, **watch_cfg})
    _write_watchers(watchers)
    print(color(f"Watcher configured for workflow: {args.name}", Colors.GREEN))
    print(f"  Path: {watch_cfg['path']}")
    print(f"  Patterns: {', '.join(watch_cfg['patterns'])}")
    return 0


def workflow_watch_list(args) -> int:
    watchers = _read_watchers()
    if not watchers:
        print(color("No workflow watchers configured.", Colors.DIM))
        return 0
    for item in watchers:
        print(f"{item.get('workflow'):<28} {item.get('path')}  patterns={','.join(item.get('patterns') or ['*'])}")
    return 0


def workflow_watch_run(args) -> int:
    watchers = _read_watchers()
    executed = 0
    for watcher in watchers:
        name = watcher.get("workflow")
        if getattr(args, "name", None) and args.name != name:
            continue
        _, workflow = _load_workflow(name)
        watch_path = Path(watcher["path"]).expanduser()
        if not watch_path.exists():
            continue
        state_path = _state_path(name)
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"seen": {}}
        seen = state.get("seen") or {}
        changed = False
        patterns = watcher.get("patterns") or ["*"]
        for pattern in patterns:
            iterator = watch_path.rglob(pattern) if watcher.get("recursive") else watch_path.glob(pattern)
            for item in iterator:
                if not item.is_file():
                    continue
                resolved = str(item.resolve())
                mtime = item.stat().st_mtime
                if seen.get(resolved) == mtime:
                    continue
                seen[resolved] = mtime
                changed = True
                run_args = argparse.Namespace(input=[resolved], vars=[], write_to=None)
                variables = _build_run_variables(workflow, run_args)
                prompt = _render_prompt(str(workflow.get("prompt_template") or ""), variables)
                run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
                executed_result = _execute_with_agent(prompt, workflow, session_source="workflow_watch")
                response = str(executed_result["result"].get("final_response") or "")
                output_path = _workflow_output_path(workflow, run_id, None)
                _write_result(output_path, workflow, executed_result["effective_prompt"], response, executed_result["session_id"])
                print(color(f"Processed {resolved} with workflow {name}", Colors.GREEN))
                print(f"  Output: {output_path}")
                executed += 1
        if changed:
            state_path.write_text(json.dumps({"seen": seen}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if executed == 0:
        print(color("No new files matched workflow watchers.", Colors.DIM))
    return 0


def workflow_schedule(args) -> int:
    from cron.jobs import create_job

    path, data = _load_workflow(args.name)
    schedule = " ".join(args.schedule) if isinstance(args.schedule, list) else args.schedule
    deliver = args.deliver or data.get("deliver") or "local"
    scripts_dir = HERMES_HOME / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_name = f"workflow_watch_{_slugify(args.name)}.py"
    runner_path = scripts_dir / script_name
    runner_path.write_text(
        "from hermes_cli.workflow import workflow_watch_run\n"
        "import argparse\n"
        f"args = argparse.Namespace(name={args.name!r})\n"
        "workflow_watch_run(args)\n",
        encoding="utf-8",
    )
    job = create_job(
        schedule=schedule,
        prompt=f"Execute managed workflow watcher for {args.name}",
        name=f"workflow:{args.name}",
        deliver=deliver,
        script=script_name,
        skills=None,
    )
    data.setdefault("managed", {})
    data["managed"].update({"enabled": True, "schedule": schedule, "job_id": job.get("id")})
    _dump_yaml(data, path)
    print(color(f"Scheduled workflow: {args.name}", Colors.GREEN))
    print(f"  Job ID: {job.get('id')}")
    print(f"  Schedule: {job.get('schedule_display')}")
    return 0


def workflow_unschedule(args) -> int:
    from cron.jobs import remove_job

    path, data = _load_workflow(args.name)
    managed = data.get("managed", {}) or {}
    job_id = managed.get("job_id")
    if not job_id:
        print(color("Workflow is not scheduled.", Colors.YELLOW))
        return 0
    remove_job(job_id)
    managed.update({"enabled": False, "job_id": None, "schedule": None})
    data["managed"] = managed
    _dump_yaml(data, path)
    print(color(f"Unscheduled workflow: {args.name}", Colors.GREEN))
    print(f"  Removed job: {job_id}")
    return 0


def build_workflow_parser(subparsers) -> None:
    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Reusable workflows, watchers, and managed jobs",
        description="Create, run, capture, watch, and schedule reusable workflows",
    )
    workflow_sub = workflow_parser.add_subparsers(dest="workflow_action")

    p_list = workflow_sub.add_parser("list", help="List saved workflows")
    p_list.set_defaults(func=workflow_list)

    p_create = workflow_sub.add_parser("create", help="Create a workflow from prompt text")
    p_create.add_argument("name")
    p_create.add_argument("prompt")
    p_create.add_argument("--description")
    p_create.add_argument("--input", action="append")
    p_create.add_argument("--glob", action="append")
    p_create.add_argument("--write-to")
    p_create.add_argument("--output-format", default="markdown")
    p_create.add_argument("--skills", action="append")
    p_create.add_argument("--model")
    p_create.add_argument("--deliver")
    p_create.set_defaults(func=workflow_create)

    p_capture = workflow_sub.add_parser("capture", help="Save a reusable workflow from an existing session")
    p_capture.add_argument("name")
    p_capture.add_argument("session_id")
    p_capture.set_defaults(func=workflow_capture)

    p_show = workflow_sub.add_parser("show", help="Show a workflow definition")
    p_show.add_argument("name")
    p_show.set_defaults(func=workflow_show)

    p_run = workflow_sub.add_parser("run", help="Run a saved workflow now")
    p_run.add_argument("name")
    p_run.add_argument("--input", action="append")
    p_run.add_argument("--var", dest="vars", action="append")
    p_run.add_argument("--write-to")
    p_run.set_defaults(func=workflow_run)

    p_delete = workflow_sub.add_parser("delete", help="Delete a saved workflow")
    p_delete.add_argument("name")
    p_delete.set_defaults(func=workflow_delete)

    p_watch = workflow_sub.add_parser("watch", help="Configure or run file watchers")
    p_watch_sub = p_watch.add_subparsers(dest="workflow_watch_action")

    p_watch_set = p_watch_sub.add_parser("set", help="Attach a directory watcher to a workflow")
    p_watch_set.add_argument("name")
    p_watch_set.add_argument("path")
    p_watch_set.add_argument("--pattern", action="append")
    p_watch_set.add_argument("--recursive", action="store_true")
    p_watch_set.add_argument("--settle-seconds", type=int, default=3)
    p_watch_set.set_defaults(func=workflow_watch_set)

    p_watch_list = p_watch_sub.add_parser("list", help="List configured watchers")
    p_watch_list.set_defaults(func=workflow_watch_list)

    p_watch_run = p_watch_sub.add_parser("run", help="Poll watchers once and process new files")
    p_watch_run.add_argument("--name")
    p_watch_run.set_defaults(func=workflow_watch_run)

    p_sched = workflow_sub.add_parser("schedule", help="Schedule a workflow as a managed cron job")
    p_sched.add_argument("name")
    p_sched.add_argument("schedule", nargs="+")
    p_sched.add_argument("--deliver")
    p_sched.set_defaults(func=workflow_schedule)

    p_unsched = workflow_sub.add_parser("unschedule", help="Remove a workflow's managed cron job")
    p_unsched.add_argument("name")
    p_unsched.set_defaults(func=workflow_unschedule)
