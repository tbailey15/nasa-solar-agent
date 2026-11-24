"""Run every task in tasks/tasks.json and save the full trajectories.

Usage:  python run_tasks.py v1
Writes logs/<version>/<task id>.json (full trajectory) and
logs/<version>/summary.md (readable transcript of every step).
"""

import json
import sys
from pathlib import Path

from agent import run_agent

FOLDER = Path(__file__).parent


def transcript(task, trajectory):
    """Readable Markdown version of one trajectory."""
    out = [f"## {task['id']} ({task['type']})", f"**Task:** {task['task']}",
           f"**Expected:** {task['expected']}", ""]
    for s in trajectory["steps"]:
        d = s.get("decision") or {}
        out.append(f"**Step {s['step']}** (context {s['context_chars']} chars, {s['notes_in_context']} notes)")
        if s["format_problems"]:
            out.append(f"- format retries: {s['format_problems']}")
        out.append(f"- thought: {d.get('thought', '')}")
        out.append(f"- action: `{d.get('action')}` `{json.dumps(d.get('args', {}), ensure_ascii=False)}`")
        if "result" in s:
            result = json.dumps(s["result"], ensure_ascii=False)
            out.append(f"- result: {result[:400]}{'...' if len(result) > 400 else ''}")
        if s.get("check", {}).get("cited_but_not_retrieved"):
            out.append(f"- WARNING cited but never retrieved: {s['check']['cited_but_not_retrieved']}")
        out.append("")
    o = trajectory["outcome"]
    out += [f"**Outcome:** {o['status']} in {len(trajectory['steps'])} steps, {trajectory['seconds']} s",
            f"**Answer:** {o['answer']}", f"**Sources:** {', '.join(o['sources']) or 'none'}", "", "---", ""]
    return "\n".join(out)


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "run"
    only = sys.argv[2:]  # optional task ids
    out_dir = FOLDER / "logs" / version
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((FOLDER / "tasks" / "tasks.json").read_text())
    summary = [f"# Trajectories: {version}", ""]
    for task in tasks:
        if only and task["id"] not in only:
            continue
        print(f"\n=== {task['id']}: {task['task']}")
        trajectory = run_agent(task["task"], finish_guard=(version != "v1"),
                               partial_answer_guard=(version not in ("v1", "v2")))
        trajectory["task_id"] = task["id"]
        (out_dir / f"{task['id']}.json").write_text(json.dumps(trajectory, indent=2, ensure_ascii=False))
        summary.append(transcript(task, trajectory))
        o = trajectory["outcome"]
        print(f"=> {o['status']}: {o['answer']}\n   sources: {o['sources']}")
    (out_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
