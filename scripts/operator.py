#!/usr/bin/env python3
"""NLP Command Center — Operator AI dispatcher.

Watches tasks/*.json for new or updated task files and routes them through
Aegis (Claude Code) for execution. This is the runtime orchestrator the
repository was scaffolded for but never had.

Usage:
  python3 scripts/operator.py              # daemon mode — watches tasks/
  python3 scripts/operator.py <task.json>  # one-shot — execute a single task

Environment:
  CLAUDE_CODE_PATH   path to claude binary (default: claude)
  INTUITEK_DIR       path to ~/intuitek/ for run_task.sh (default: ~/intuitek)
  OPERATOR_POLL_S    poll interval seconds in daemon mode (default: 30)
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
ACTIONS_DIR = REPO_ROOT / "actions"
LOGS_DIR = REPO_ROOT / "logs"
LOG_FILE = LOGS_DIR / "execution_log.txt"
TOOLBOX_FILE = REPO_ROOT / "toolbox.json"

CLAUDE_BIN = os.environ.get("CLAUDE_CODE_PATH", "claude")
INTUITEK = Path(os.environ.get("INTUITEK_DIR", Path.home() / "intuitek"))
RUN_TASK = INTUITEK / "run_task.sh"
POLL_S = int(os.environ.get("OPERATOR_POLL_S", "30"))

# Track processed tasks so we don't re-run them
_seen: dict[str, float] = {}


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_task(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log(f"INVALID JSON in {path.name}: {e} — skipping")
        return None


def find_action(task: dict) -> str | None:
    action = task.get("action", "")
    tool = task.get("toolName", "")
    # Check actions/ for a matching YAML
    for candidate in [action, tool]:
        if not candidate:
            continue
        slug = candidate.lower().replace(" ", "_")
        for suffix in [".yaml", ".yml", ".py"]:
            p = ACTIONS_DIR / (slug + suffix)
            if p.exists():
                return str(p)
    return None


def build_prompt(task: dict, action_path: str | None) -> str:
    task_id = task.get("taskID", "unknown")
    action = task.get("action", "")
    tool = task.get("toolName", "")
    params = task.get("parameters", {})
    priority = task.get("priority", "normal")

    prompt_parts = [
        f"NLP Command Center task: {task_id}",
        f"Action: {action}",
    ]
    if tool:
        prompt_parts.append(f"Tool: {tool}")
    if params:
        prompt_parts.append(f"Parameters: {json.dumps(params)}")
    if priority:
        prompt_parts.append(f"Priority: {priority}")
    if action_path:
        try:
            action_content = Path(action_path).read_text()
            prompt_parts.append(f"Action definition:\n{action_content}")
        except Exception:
            pass

    # Include toolbox context if relevant
    if TOOLBOX_FILE.exists() and tool:
        try:
            toolbox = json.loads(TOOLBOX_FILE.read_text())
            for category in toolbox.get("tools", []):
                for entry in (category.get("items") or [category]):
                    if tool.lower() in str(entry.get("name", "")).lower():
                        prompt_parts.append(f"Tool info: {json.dumps(entry)}")
        except Exception:
            pass

    prompt_parts.append(
        "Execute this task autonomously within your approval boundary. "
        "Log what you did to logs/execution_log.txt in this repo. "
        "If execution requires external credentials not available here, describe what's needed."
    )
    return "\n".join(prompt_parts)


def execute_task(path: Path) -> str:
    task = load_task(path)
    if task is None:
        return "SKIP_INVALID"

    task_id = task.get("taskID", path.stem)
    status = task.get("status", "pending")
    if status == "completed":
        return "SKIP_DONE"

    action_path = find_action(task)
    prompt = build_prompt(task, action_path)

    log(f"executing task {task_id} (action={task.get('action','?')} tool={task.get('toolName','?')})")

    # Route through Aegis if run_task.sh is available, else fall back to direct claude
    if RUN_TASK.exists():
        result = subprocess.run(
            ["bash", str(RUN_TASK), prompt],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout or result.stderr
        exit_code = result.returncode
    elif subprocess.run(["which", CLAUDE_BIN], capture_output=True).returncode == 0:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=300,
            cwd=str(REPO_ROOT)
        )
        output = result.stdout or result.stderr
        exit_code = result.returncode
    else:
        log(f"no Aegis runtime found (run_task.sh or claude) — task {task_id} queued for manual execution")
        log(f"prompt would be: {prompt[:200]}...")
        return "QUEUED"

    outcome = "SUCCESS" if exit_code == 0 else "FAILED"
    log(f"task {task_id} {outcome} (exit={exit_code})")
    if output.strip():
        log(f"output: {output.strip()[:500]}")

    # Mark task completed in its file
    task["status"] = "completed" if exit_code == 0 else "failed"
    task["timestamp"] = ts()
    path.write_text(json.dumps(task, indent=2))
    return outcome


def scan_and_run() -> int:
    processed = 0
    for task_file in sorted(TASKS_DIR.glob("*.json")):
        if task_file.name == "new_task_template.json":
            continue
        mtime = task_file.stat().st_mtime
        last = _seen.get(task_file.name, 0)
        if mtime <= last:
            continue
        _seen[task_file.name] = mtime
        result = execute_task(task_file)
        if result not in ("SKIP_INVALID", "SKIP_DONE"):
            processed += 1
    return processed


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.exists():
            path = TASKS_DIR / sys.argv[1]
        result = execute_task(path)
        return 0 if result in ("SUCCESS", "QUEUED", "SKIP_DONE") else 1

    log("operator daemon started — watching tasks/")
    while True:
        try:
            n = scan_and_run()
            if n > 0:
                log(f"processed {n} task(s)")
        except KeyboardInterrupt:
            log("operator daemon stopped")
            return 0
        except Exception as e:
            log(f"scan error: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
