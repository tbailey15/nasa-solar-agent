"""A hand-written agent loop for answering solar system questions from NASA pages.

No agent framework is used. Each step of the loop:

  1. OBSERVE  - build the current state: the task, compact notes from older
                steps, and the full result of only the most recent steps.
  2. DECIDE   - ask the model (qwen3:4b through Ollama) for one JSON action.
  3. ACT      - check the action and run the tool, or finish / give up.
  4. RECORD   - save the observation, decision, tool call and result in the
                trajectory log.

The loop ends when the model gives a final answer, gives up, or a limit is hit.
"""

import json
import time
import urllib.request

from tools import TOOLS, ToolError

MODEL = "qwen3:4b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

# ---- limits so the agent can never run forever
MAX_STEPS = 8            # most decisions for one task
MAX_FORMAT_RETRIES = 2   # times we re-ask when the reply is not valid JSON
MAX_TOOL_ERRORS = 3      # tool errors allowed before stopping
MAX_REPEATS = 2          # identical tool calls allowed before stopping
MODEL_TIMEOUT = 180      # seconds per model call

# ---- context limits
RECENT_STEPS_IN_FULL = 2     # last N tool results are shown word for word
NOTE_CHARS = 220             # older results are shrunk to a note this long
MAX_NOTES = 8                # oldest notes are dropped after this

SYSTEM_PROMPT = """You are a research assistant for NASA's education outreach team.
You answer questions about the solar system using ONLY a small collection of NASA
Science fact pages. You work in steps. At every step reply with ONE JSON object:

{"thought": "<one short sentence about what you need next>",
 "action": "<tool name, final_answer, or give_up>",
 "args": {...}}

Tools:
%s

Finish with:
  final_answer {"answer": str, "sources": [list of NASA page URLs you used]}
  give_up      {"reason": str}   - only if the task cannot be started at all

Rules:
- Look up every number in the NASA pages with search_docs before using it.
- Never do arithmetic in your head: use calculate or convert_units.
- Cite only URLs that appeared in your search results.
- If the pages do not contain part of the answer, answer the rest and say
  clearly which part the NASA collection does not cover. Do not guess.
- Keep answers short."""


def tool_menu():
    return "\n".join("  " + description for _, description in TOOLS.values())


class AgentState:
    """Everything the agent knows about the current task."""

    def __init__(self, task):
        self.task = task
        self.steps = []          # full record of every step (for the log)
        self.notes = []          # short summaries of older tool results
        self.sources_seen = {}   # url -> page title, from search results
        self.tool_errors = 0
        self.call_counts = {}
        self.established = []    # v2: calculate/convert results, never shortened
        self.answer_now = None   # v2: reason the loop is asking for a final answer

    def recent_results(self):
        tool_steps = [s for s in self.steps if s.get("tool")]
        return tool_steps[-RECENT_STEPS_IN_FULL:]

    def add_note(self, step):
        """Shrink a finished tool result to one line and keep it as a note."""
        result = json.dumps(step["result"], ensure_ascii=False)
        note = f"step {step['step']}: {step['tool']}({json.dumps(step['args'])}) -> {result[:NOTE_CHARS]}"
        self.notes.append(note)
        self.notes = self.notes[-MAX_NOTES:]


def build_context(state):
    """OBSERVE: turn the state into the messages sent to the model."""
    parts = [f"TASK: {state.task}"]
    if state.notes:
        parts.append("NOTES FROM EARLIER STEPS (shortened):\n" + "\n".join(state.notes))
    recent = state.recent_results()
    if recent:
        parts.append("MOST RECENT RESULTS (full):\n" + "\n".join(
            f"step {s['step']}: {s['tool']}({json.dumps(s['args'])}) ->\n{json.dumps(s['result'], ensure_ascii=False)}"
            for s in recent))
    if state.established:
        parts.append("ESTABLISHED RESULTS (already computed, do not redo):\n" + "\n".join(state.established))
    if state.sources_seen:
        parts.append("SOURCES YOU HAVE SEEN:\n" + "\n".join(f"- {u} ({t})" for u, t in state.sources_seen.items()))
    if state.answer_now:
        parts.append(f"ANSWER NOW ({state.answer_now}). Your next action MUST be final_answer. "
                     "Use the facts and results above. For any part of the task the NASA pages "
                     "did not cover, say so plainly instead of guessing.")
    parts.append(f"You are on step {len(state.steps) + 1} of at most {MAX_STEPS}. Reply with one JSON object.")
    return [{"role": "system", "content": SYSTEM_PROMPT % tool_menu()},
            {"role": "user", "content": "\n\n".join(parts)}]


def ask_model(messages):
    """DECIDE: one call to the local model. Returns the raw reply text."""
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False,
                       "format": "json", "think": False,
                       "options": {"temperature": 0.2, "num_ctx": 8192}}).encode()
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=MODEL_TIMEOUT) as response:
        return json.loads(response.read())["message"]["content"]


def parse_decision(text):
    """Check the model's reply has the shape we asked for."""
    decision = json.loads(text)
    if not isinstance(decision, dict) or "action" not in decision:
        raise ValueError("reply must be a JSON object with an 'action' key")
    decision.setdefault("args", {})
    if not isinstance(decision["args"], dict):
        raise ValueError("'args' must be an object")
    return decision


def run_agent(task, log=print, finish_guard=True, partial_answer_guard=True):
    """Run one task to completion and return the trajectory.

    finish_guard=False reproduces version 1 (no answer-now mode, no
    established-results list). partial_answer_guard=False reproduces
    version 2 (give_up is accepted even after useful passages were found).
    """
    state = AgentState(task)
    refused_give_up = False
    started = time.time()
    outcome = {"status": "stopped_step_limit", "answer": None, "sources": []}

    while len(state.steps) < MAX_STEPS:
        step_no = len(state.steps) + 1
        messages = build_context(state)
        context_chars = sum(len(m["content"]) for m in messages)

        # DECIDE (with retries for malformed replies)
        decision, raw, problems = None, None, []
        for attempt in range(MAX_FORMAT_RETRIES + 1):
            try:
                raw = ask_model(messages)
                decision = parse_decision(raw)
                break
            except (ValueError, json.JSONDecodeError) as error:
                problems.append(f"attempt {attempt + 1}: {error}")
                messages = messages + [{"role": "assistant", "content": raw or ""},
                                       {"role": "user", "content": f"That was not valid. {error}. Reply with one JSON object only."}]
        step = {"step": step_no, "context_chars": context_chars, "notes_in_context": len(state.notes),
                "raw_reply": raw, "format_problems": problems, "decision": decision}
        if decision is None:
            outcome = {"status": "stopped_bad_format", "answer": None, "sources": []}
            state.steps.append(step)
            log(f"  [{step_no}] could not get valid JSON, stopping")
            break

        action, args = decision["action"], decision["args"]
        log(f"  [{step_no}] {decision.get('thought', '')}\n        -> {action} {json.dumps(args)}")

        # ACT: finishing actions
        if action == "final_answer":
            sources = [s for s in args.get("sources", []) if isinstance(s, str)]
            unseen = [s for s in sources if s not in state.sources_seen]
            step["check"] = {"cited_but_not_retrieved": unseen}
            outcome = {"status": "answered", "answer": args.get("answer"),
                       "sources": [s for s in sources if s in state.sources_seen],
                       "unverified_sources": unseen}
            state.steps.append(step)
            break
        if action == "give_up" and partial_answer_guard and state.sources_seen and not refused_give_up:
            # v3: passages were found, so a partial answer is more useful than giving up
            refused_give_up = True
            state.answer_now = ("you found relevant NASA passages, so give a partial answer and "
                                "say which part the collection does not cover")
            step["result"] = {"note": "give_up refused once: answer the part you found."}
            step["answer_now"] = state.answer_now
            state.steps.append(step)
            log("        <- give_up refused, asking for a partial answer")
            continue
        if action == "give_up":
            outcome = {"status": "gave_up", "answer": args.get("reason"), "sources": []}
            state.steps.append(step)
            break

        # ACT: tool calls
        step["tool"], step["args"] = action, args
        key = action + json.dumps(args, sort_keys=True)
        state.call_counts[key] = state.call_counts.get(key, 0) + 1
        if action not in TOOLS:
            step["result"] = {"error": f"Unknown action '{action}'. Use a tool name, final_answer or give_up."}
            state.tool_errors += 1
        elif finish_guard and state.call_counts[key] > 1:
            # v2: a repeated call means the agent is stuck, so ask for the answer instead
            step["result"] = {"note": "Repeated call skipped. You already have this result."}
            state.answer_now = "you repeated a call you already made"
        elif state.call_counts[key] > MAX_REPEATS:
            step["result"] = {"error": "You already made this exact call. Use its result or try something else."}
            state.tool_errors += 1
        else:
            try:
                step["result"] = TOOLS[action][0](args)
                for hit in step["result"].get("results", []) if isinstance(step["result"], dict) else []:
                    state.sources_seen[hit["url"]] = hit["page"]
                if "url" in step["result"]:
                    state.sources_seen[step["result"]["url"]] = step["result"]["page"]
            except ToolError as error:
                step["result"] = {"error": str(error)}
                state.tool_errors += 1

        # v2: computed numbers stay in view for the rest of the task
        if finish_guard and action in ("calculate", "convert_units") and "result" in step.get("result", {}):
            state.established.append(f"{action}({json.dumps(args)}) = {step['result']['result']}")
        if finish_guard and len(state.steps) + 1 >= MAX_STEPS - 1 and not state.answer_now:
            state.answer_now = "only the last steps are left"

        # RECORD
        step["answer_now"] = state.answer_now
        state.steps.append(step)
        brief = json.dumps(step["result"], ensure_ascii=False)
        log(f"        <- {brief[:200]}{'...' if len(brief) > 200 else ''}")

        # keep the context small: anything older than the recent window becomes a note
        tool_steps = [s for s in state.steps if s.get("tool")]
        if len(tool_steps) > RECENT_STEPS_IN_FULL:
            state.add_note(tool_steps[-RECENT_STEPS_IN_FULL - 1])

        if state.tool_errors >= MAX_TOOL_ERRORS:
            outcome = {"status": "stopped_tool_errors", "answer": None, "sources": []}
            break

    return {"task": task, "model": MODEL, "outcome": outcome, "steps": state.steps,
            "seconds": round(time.time() - started, 1),
            "limits": {"max_steps": MAX_STEPS, "max_format_retries": MAX_FORMAT_RETRIES,
                       "max_tool_errors": MAX_TOOL_ERRORS, "max_repeats": MAX_REPEATS,
                       "recent_steps_in_full": RECENT_STEPS_IN_FULL, "max_notes": MAX_NOTES}}


if __name__ == "__main__":
    question = input("Ask about the solar system: ")
    result = run_agent(question)
    print("\nSTATUS:", result["outcome"]["status"])
    print("ANSWER:", result["outcome"]["answer"])
    print("SOURCES:", ", ".join(result["outcome"]["sources"]) or "none")
