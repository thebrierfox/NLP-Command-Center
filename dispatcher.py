#!/usr/bin/env python3
"""
NLP-Command-Center Dispatcher
Translates a natural language directive into a task JSON and executes it.

Usage:
    python dispatcher.py "open Replit for a new Python project"
    python dispatcher.py --dry-run "check if Figma is reachable"
    echo "launch Notion" | python dispatcher.py -

Routing:
    Tier 0: Ollama local (qwen3:14b or qwen2.5:7b) — zero API cost
    Tier 1: Anthropic Haiku — used when Ollama unavailable
    Tier 2: Anthropic Sonnet — explicit --tier=2 only
"""

import argparse
import json
import os
import sys
import uuid
import subprocess
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLBOX_PATH = os.path.join(BASE_DIR, "toolbox.json")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")

SUPPORTED_ACTIONS = ["launchTool", "callAPI", "runScript"]

SYSTEM_PROMPT = """You convert natural language directives into task JSON for the NLP-Command-Center.

Output ONLY a single valid JSON object — no explanation, no markdown, no code fences.

Required fields (every response must include ALL of these):
  taskID    - string (use the exact value provided in the user message)
  action    - one of: "launchTool", "callAPI", "runScript"
  toolName  - exact tool name from the list (empty string if action is callAPI with an explicit URL)
  parameters - object (keys depend on action: for launchTool: {projectName, language}; for callAPI: {url, method}; for runScript: {script})
  priority  - one of: "high", "medium", "low"
  trigger   - "manual"
  status    - "pending"

Example output for "check if Replit is reachable":
{"taskID":"task_abc123","action":"launchTool","toolName":"Replit","parameters":{"projectName":"test","language":"Python"},"priority":"medium","trigger":"manual","status":"pending"}"""


def load_toolbox():
    if not os.path.isfile(TOOLBOX_PATH):
        return {}
    with open(TOOLBOX_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    tools = {}
    for category in data.get("Toolbox", []):
        for tool in category.get("tools", []):
            name = tool.get("name", "")
            if name:
                tools[name] = {
                    "url": tool.get("url", ""),
                    "description": tool.get("description", ""),
                    "category": category.get("category", ""),
                }
    return tools


def tools_summary(tools):
    lines = []
    for name, info in list(tools.items())[:40]:
        lines.append(f"- {name}: {info['description'][:70]}")
    return "\n".join(lines)


def call_ollama(directive, tools, model="qwen2.5:7b"):
    tools_text = tools_summary(tools)
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    user_msg = f"taskID to use: {task_id}\n\nAvailable tools:\n{tools_text}\n\nDirective: {directive}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
            return result["message"]["content"].strip()
    except Exception:
        return None


def call_anthropic(directive, tools, model="claude-haiku-4-5-20251001"):
    try:
        import anthropic
    except ImportError:
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    tools_text = tools_summary(tools)
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    user_msg = f"taskID to use: {task_id}\n\nAvailable tools:\n{tools_text}\n\nDirective: {directive}"

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return msg.content[0].text.strip()


def extract_json(raw):
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
    # Find first { ... }
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None


def generate_task(directive, tools, tier=0):
    raw = None
    if tier == 0:
        # Try Ollama models in order
        for model in ("qwen2.5:7b", "qwen2.5:3b"):
            raw = call_ollama(directive, tools, model=model)
            if raw:
                break
        if not raw:
            print("[dispatcher] Ollama unavailable, escalating to Tier 1", file=sys.stderr)
            tier = 1

    if tier == 1 or (tier == 0 and not raw):
        raw = call_anthropic(directive, tools, model="claude-haiku-4-5-20251001")

    if tier == 2:
        raw = call_anthropic(directive, tools, model="claude-sonnet-4-6")

    if not raw:
        print("[dispatcher] ERROR: all inference tiers failed", file=sys.stderr)
        return None

    task = extract_json(raw)
    if not task:
        print(f"[dispatcher] ERROR: could not parse JSON from response:\n{raw}", file=sys.stderr)
        return None

    # Validate required fields; fill defaults
    task.setdefault("status", "pending")
    task.setdefault("trigger", "manual")
    task.setdefault("priority", "medium")
    if "taskID" not in task:
        task["taskID"] = f"task_{uuid.uuid4().hex[:8]}"
    if "action" not in task or task["action"] not in SUPPORTED_ACTIONS:
        task["action"] = "launchTool"
    if "toolName" not in task:
        task["toolName"] = ""
    if "parameters" not in task:
        task["parameters"] = {}

    return task


def run_task(task_path):
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "orchestrator.py"), "--task", task_path],
        capture_output=False,
    )
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Natural-language dispatcher for NLP-Command-Center tasks."
    )
    parser.add_argument(
        "directive",
        nargs="?",
        help="Natural language task directive. Use '-' to read from stdin.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate task JSON but do not execute.")
    parser.add_argument("--tier", type=int, default=0, choices=[0, 1, 2], help="Force inference tier.")
    args = parser.parse_args()

    if not args.directive:
        parser.print_help()
        sys.exit(1)

    if args.directive == "-":
        directive = sys.stdin.read().strip()
    else:
        directive = args.directive

    if not directive:
        print("[dispatcher] ERROR: empty directive", file=sys.stderr)
        sys.exit(1)

    tools = load_toolbox()
    if not tools:
        print("[dispatcher] WARNING: toolbox.json empty or missing — proceeding without tool context", file=sys.stderr)

    print(f"[dispatcher] Directive: {directive}")
    print(f"[dispatcher] Inference tier: {args.tier}")

    task = generate_task(directive, tools, tier=args.tier)
    if not task:
        sys.exit(1)

    print("[dispatcher] Generated task:")
    print(json.dumps(task, indent=2))

    if args.dry_run:
        print("[dispatcher] Dry run — task not written or executed.")
        return

    # Write task file
    os.makedirs(TASKS_DIR, exist_ok=True)
    task_path = os.path.join(TASKS_DIR, f"{task['taskID']}.json")
    with open(task_path, "w", encoding="utf-8") as fh:
        json.dump(task, fh, indent=2)
        fh.write("\n")
    print(f"[dispatcher] Task written to {task_path}")

    # Execute
    code = run_task(task_path)
    if code == 0:
        with open(task_path, encoding="utf-8") as fh:
            final = json.load(fh)
        print(f"[dispatcher] Result: {final.get('status')} | {final.get('notes', '')}")
    else:
        print(f"[dispatcher] Orchestrator exited with code {code}", file=sys.stderr)
        sys.exit(code)


if __name__ == "__main__":
    main()
