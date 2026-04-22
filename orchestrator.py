#!/usr/bin/env python3
"""
NLP-Command-Center Orchestrator
Reads task_*.json files, resolves tools from toolbox.json, executes actions,
updates task status, and logs all results to logs/execution_log.txt.

Usage:
    python orchestrator.py                             # run all pending tasks
    python orchestrator.py --task tasks/task_001.json  # run one specific task
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Paths (resolved relative to this file's directory)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
TOOLBOX_PATH = os.path.join(BASE_DIR, "toolbox.json")
LOG_PATH = os.path.join(BASE_DIR, "logs", "execution_log.txt")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
ACTIONS_DIR = os.path.join(BASE_DIR, "actions")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message, print_to_stdout=True):
    """Append a timestamped entry to the execution log and optionally print it."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "[{}] {}".format(ts, message)
    if print_to_stdout:
        print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------------------
# Toolbox
# ---------------------------------------------------------------------------

def load_toolbox():
    """
    Load toolbox.json and return a flat dict keyed by tool name (case-insensitive).
    Returns an empty dict if the file is missing or invalid.
    """
    if not os.path.isfile(TOOLBOX_PATH):
        log("WARNING: toolbox.json not found at {}".format(TOOLBOX_PATH))
        return {}
    try:
        with open(TOOLBOX_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        log("ERROR: toolbox.json is not valid JSON: {}".format(exc))
        return {}

    flat = {}
    for category in data.get("Toolbox", []):
        for tool in category.get("tools", []):
            name = tool.get("name", "")
            if name:
                flat[name.lower()] = tool
    return flat


# ---------------------------------------------------------------------------
# Task I/O
# ---------------------------------------------------------------------------

def load_task(path):
    """
    Load and parse a task JSON file.
    Returns the parsed dict on success, or None (with a log entry) on failure.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            task = json.load(fh)
        return task
    except json.JSONDecodeError as exc:
        log("SYNTAX ERROR in {}: {} -- skipping".format(path, exc))
        return None
    except OSError as exc:
        log("IO ERROR reading {}: {} -- skipping".format(path, exc))
        return None


def save_task(path, task):
    """Write the updated task dict back to its file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(task, fh, indent=2)
        fh.write("\n")


def collect_task_files(specific=None):
    """
    Return a list of task file paths to process.
    If `specific` is given, validate and return just that one file.
    Otherwise return all tasks/task_*.json files (excluding any template).
    """
    if specific:
        path = os.path.abspath(specific)
        if not os.path.isfile(path):
            log("ERROR: specified task file not found: {}".format(path))
            sys.exit(1)
        return [path]

    pattern = os.path.join(TASKS_DIR, "task_*.json")
    paths = sorted(glob.glob(pattern))
    # Exclude any file whose basename contains "template"
    paths = [p for p in paths if "template" not in os.path.basename(p).lower()]
    return paths


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def action_launch_tool(task, tool_info):
    """
    launchTool: make an HTTP HEAD request to the tool URL to verify reachability.
    Returns (status, notes).
    """
    tool_name = task.get("toolName", "unknown")
    url = tool_info.get("url", "")
    if not url:
        return "failed", "No URL found for tool '{}' in toolbox.json".format(tool_name)

    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "NLP-Command-Center-Orchestrator/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
        notes = "HEAD {} -> HTTP {}".format(url, code)
        status = "completed" if 200 <= code < 400 else "failed"
    except urllib.error.HTTPError as exc:
        # Many sites reject HEAD with 405/403 but the tool is still reachable
        if exc.code in (405, 403):
            notes = "HEAD {} -> HTTP {} (tool reachable, HEAD not allowed)".format(url, exc.code)
            status = "completed"
        else:
            notes = "HEAD {} -> HTTP {} {}".format(url, exc.code, exc.reason)
            status = "failed"
    except urllib.error.URLError as exc:
        notes = "HEAD {} -> network error: {}".format(url, exc.reason)
        status = "failed"
    except Exception as exc:
        notes = "HEAD {} -> unexpected error: {}".format(url, exc)
        status = "failed"

    return status, notes


def action_call_api(task, tool_info):
    """
    callAPI: make an HTTP GET (or POST if body provided) to the URL specified
    in task parameters.  Falls back to the toolbox URL if none provided.
    Returns (status, notes).
    """
    params = task.get("parameters", {})
    url = params.get("url") or tool_info.get("url", "")
    method = params.get("method", "GET").upper()
    body = params.get("body")

    if not url:
        return "failed", "callAPI: no URL specified in task parameters or toolbox"

    try:
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "NLP-Command-Center-Orchestrator/1.0")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.status
            response_snippet = resp.read(200).decode("utf-8", errors="replace")
        notes = "{} {} -> HTTP {} | response: {}".format(
            method, url, code, response_snippet[:120]
        )
        status = "completed" if 200 <= code < 400 else "failed"
    except urllib.error.HTTPError as exc:
        notes = "{} {} -> HTTP {} {}".format(method, url, exc.code, exc.reason)
        status = "failed"
    except urllib.error.URLError as exc:
        notes = "{} {} -> network error: {}".format(method, url, exc.reason)
        status = "failed"
    except Exception as exc:
        notes = "{} {} -> unexpected error: {}".format(method, url, exc)
        status = "failed"

    return status, notes


def action_run_script(task, tool_info):
    """
    runScript: locate the referenced script in scripts/ or actions/ and run it.
    Script path can be specified as task parameters["script"], or derived from
    the toolName.
    Returns (status, notes).
    """
    params = task.get("parameters", {})
    script_ref = params.get("script", "")

    # Search order: explicit param, scripts/, actions/
    candidates = []
    if script_ref:
        candidates.append(os.path.join(BASE_DIR, script_ref))
        candidates.append(os.path.join(SCRIPTS_DIR, script_ref))
        candidates.append(os.path.join(ACTIONS_DIR, script_ref))
    else:
        tool_name = task.get("toolName", "").lower().replace(" ", "_")
        for d in (SCRIPTS_DIR, ACTIONS_DIR):
            for ext in (".py", ".js", ".sh"):
                candidates.append(os.path.join(d, tool_name + ext))
                candidates.append(os.path.join(d, "action_" + tool_name + ext))

    script_path = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            script_path = candidate
            break

    if not script_path:
        searched = ", ".join(candidates[:6])
        return "failed", "runScript: no script found. Searched: {}".format(searched)

    ext = os.path.splitext(script_path)[1].lower()
    if ext == ".py":
        cmd = [sys.executable, script_path]
    elif ext == ".js":
        cmd = ["node", script_path]
    elif ext == ".sh":
        cmd = ["bash", script_path]
    else:
        cmd = [script_path]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=BASE_DIR,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            notes = "runScript {} -> exit 0 | stdout: {}".format(
                os.path.basename(script_path), stdout[:120]
            )
            status = "completed"
        else:
            notes = "runScript {} -> exit {} | stderr: {}".format(
                os.path.basename(script_path), result.returncode, stderr[:120]
            )
            status = "failed"
    except subprocess.TimeoutExpired:
        notes = "runScript {} -> timed out after 30s".format(os.path.basename(script_path))
        status = "failed"
    except FileNotFoundError as exc:
        notes = "runScript: interpreter not found: {}".format(exc)
        status = "failed"
    except Exception as exc:
        notes = "runScript: unexpected error: {}".format(exc)
        status = "failed"

    return status, notes


# ---------------------------------------------------------------------------
# Core dispatcher
# ---------------------------------------------------------------------------

def execute_task(task_path, toolbox):
    """Load, validate, execute, and update a single task file."""
    task = load_task(task_path)
    if task is None:
        return  # error already logged in load_task

    task_id = task.get("taskID", os.path.basename(task_path))
    action = task.get("action", "").strip()
    tool_name = task.get("toolName", "").strip()
    current_status = task.get("status", "pending")

    # Skip tasks already in a terminal state; reset to 'pending' to re-run.
    if current_status not in ("pending", "", None):
        log("{}: status is '{}' -- skipping (reset to 'pending' to re-run)".format(
            task_id, current_status
        ))
        return

    # Resolve tool from toolbox
    tool_info = toolbox.get(tool_name.lower(), {})
    if not tool_info and action not in ("callAPI", "runScript"):
        log("{}: WARNING -- tool '{}' not found in toolbox.json".format(task_id, tool_name))

    log("{}: starting action='{}' tool='{}'".format(task_id, action, tool_name))

    # Dispatch
    action_lower = action.lower()
    if action_lower == "launchtool":
        status, notes = action_launch_tool(task, tool_info)
    elif action_lower == "callapi":
        status, notes = action_call_api(task, tool_info)
    elif action_lower == "runscript":
        status, notes = action_run_script(task, tool_info)
    else:
        status = "unsupported"
        notes = "Unsupported action '{}' -- add a handler in orchestrator.py".format(action)

    # Update task file
    task["status"] = status
    task["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task["notes"] = notes
    save_task(task_path, task)

    log("{}: status={} | {}".format(task_id, status, notes))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NLP-Command-Center orchestrator -- executes task_*.json files."
    )
    parser.add_argument(
        "--task",
        metavar="TASK_FILE",
        help="Run a single specific task file instead of all pending tasks.",
    )
    args = parser.parse_args()

    log("=" * 60)
    log("Orchestrator starting")

    toolbox = load_toolbox()
    log("Toolbox loaded: {} tools".format(len(toolbox)))

    task_files = collect_task_files(specific=args.task)
    if not task_files:
        log("No task files found -- nothing to do.")
        return

    log("Tasks to process: {}".format(len(task_files)))
    for task_path in task_files:
        execute_task(task_path, toolbox)

    log("Orchestrator finished")
    log("=" * 60)


if __name__ == "__main__":
    main()
