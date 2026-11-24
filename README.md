# NASA Solar System Agent (Competency 7: Build Your Own Agent)

A prototype assistant for NASA's education outreach team. It answers
multi-step questions about the solar system from nine NASA Science fact
pages, does calculations and unit conversions with tools, cites the NASA
pages it used, and stops safely when it cannot finish a task.

The agent loop is written by hand in `agent.py`. No LangChain, CrewAI,
AutoGen or other agent framework is used. The model is only called through
Ollama's plain HTTP API.

- **Model:** `qwen3:4b` through Ollama (the same model as Competency 6; no fallback needed)
- **Versions:** Python 3.14.7, Ollama 0.40.17, beautifulsoup4 4.15.0 (only for building the documents)
- **Data:** the NASA pages in `docs/`, the test tasks in `tasks/tasks.json`, and the trajectory logs in `logs/`

## Files

| File | What it does |
|---|---|
| `prepare_docs.py` | Downloads the 9 NASA pages and saves the article text as Markdown in `docs/` |
| `retrieval.py` | Splits the documents into chunks and ranks them with BM25 |
| `tools.py` | The four tools: `search_docs`, `read_chunk`, `calculate`, `convert_units` |
| `agent.py` | The agent loop: observe, decide, act, record, with limits and context management |
| `run_tasks.py` | Runs every test task and saves the full trajectories |
| `test_agent.py` | Tests for the loop that use a scripted fake model (no Ollama needed) |
| `tasks/tasks.json` | The four multi-step test tasks with expected answers |
| `logs/v1`, `logs/v2`, `logs/v3` | Full trajectories (`*.json`) and readable transcripts (`summary.md`) for each version |

## Setup

Install Ollama from https://ollama.com and pull the model:

```bash
ollama pull qwen3:4b
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
python -m pip install -r requirements.txt
```

The agent itself only uses the Python standard library. `beautifulsoup4` is
only needed to rebuild the documents:

```bash
python prepare_docs.py
```

Ask one question:

```bash
python agent.py
```

Run all test tasks and save trajectories (use `v1`, `v2` or `v3` to pick the version):

```bash
python run_tasks.py v3
```

Run the loop tests:

```bash
python -m unittest test_agent -v
```

## The document collection

`prepare_docs.py` downloads these NASA Science pages (retrieved 2026-11-23):
Solar System Facts, Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus and
Neptune Facts. Each page is saved as one UTF-8 Markdown file with the page
title, source URL, publisher and retrieval date at the top. Menus, link lists
and the "Discover More Topics" footer are removed. Image captions are kept
because they carry photo credits, including third-party credits such as
"Enhanced image by Kevin M. Gill (CC-BY)" on the Jupiter page.

All facts come from NASA (science.nasa.gov). NASA content is generally not
copyrighted in the United States, but NASA is credited as the source here and
in every answer.

## How the agent works

### Retrieval

`retrieval.py` splits each document on its section headings, then into chunks
of about 700 characters with 120 characters of overlap, so a fact is not cut
in half. That gives 186 chunks. Every chunk keeps its page title, source URL
and section heading. Chunks are ranked with BM25, written out by hand.
`search_docs` can be limited to one planet's page.

### Tools (4)

| Tool | Input | Purpose |
|---|---|---|
| `search_docs` | `query`, optional `planet` | Top 3 passages with page, URL, section and text |
| `read_chunk` | `chunk_id` | Re-read one passage by its id |
| `calculate` | `expression` | Arithmetic only. It is parsed with `ast`, so no code can run (`__import__` is rejected) |
| `convert_units` | `value`, `from_unit`, `to_unit` | Length (miles, km, m, ft, AU), time (hours, days, years) and temperature (F, C, K) |

Every tool checks its input and raises `ToolError` with a clear message. The
loop shows that message to the model as an observation so it can try again.

### The loop

Each step in `run_agent`:

1. **Observe:** `build_context` builds the current state: the task, short
   notes from older steps, the full results of only the last two tool calls,
   the established results, and the list of sources seen.
2. **Decide:** the model replies with one JSON object:
   `{"thought": ..., "action": ..., "args": {...}}`.
3. **Act:** the loop checks the action. It runs a tool, accepts
   `final_answer`, or handles `give_up`.
4. **Record:** the step is saved with the context size, raw reply, decision,
   tool call, result and any warnings.

### Context and memory management

The prompt is rebuilt from the state at every step instead of growing as one
long chat:

- Only the last 2 tool results are shown in full (`RECENT_STEPS_IN_FULL`).
- Older results are shrunk to a 220-character note, and at most 8 notes are
  kept (`NOTE_CHARS`, `MAX_NOTES`).
- Calculation and conversion results are kept as "established results" so a
  computed number is never lost (added in v2).

In the logged runs the context never went over 7,329 characters, and a test
(`test_context_stays_bounded`) checks this.

### Limits and stopping

| Limit | Value | What happens |
|---|---|---|
| `MAX_STEPS` | 8 | Stops with `stopped_step_limit` |
| `MAX_FORMAT_RETRIES` | 2 | A reply that is not valid JSON is re-asked twice, then `stopped_bad_format` |
| `MAX_TOOL_ERRORS` | 3 | Stops with `stopped_tool_errors` |
| `MAX_REPEATS` | 2 | The same tool call with the same arguments is blocked |
| `MODEL_TIMEOUT` | 180 s | Per model call |

Sources are checked too. A URL the model cites that never appeared in a
search result is removed from the answer and logged as `unverified_sources`.

## Test tasks

| Id | Type | Task | Expected |
|---|---|---|---|
| T1 | Comparison + calculation | Is Jupiter or Saturn wider, and by how many times? | Jupiter, about 1.16x. NASA gives Jupiter's *radius* but Saturn's *diameter*, so this is a trap |
| T2 | Unit conversion | Venus's surface temperature in kelvin | 872 °F / 467 °C, about 740 K |
| T3 | Multi-step lookup + calculation | Which planet has the shortest day, and how many fit in 24 hours? | Jupiter, 9.9 h, about 2.4 |
| T4 | Partly unanswerable | How many moons does Neptune have, and what did Voyager 2 cost? | 16 moons; the cost is not in the collection, so the agent should say so |

## Results

| Version | Answered correctly | Total time | Notes |
|---|---|---|---|
| v1 (first loop) | **0 / 4** | 148 s | Every task stopped on a limit |
| v2 (answer-now fix) | **3 / 4** | 71 s | T4 gave up instead of answering the part it knew |
| v3 (partial-answer fix) | **4 / 4** | 75 s | T4: "Neptune has 16 known moons. The NASA pages do not provide information about the cost of the Voyager 2 mission." |

Full step-by-step transcripts are in `logs/<version>/summary.md`.

## Trajectory analysis: what went wrong in v1

From the customer's point of view, v1 was not usable: it never gave an answer.
The safety limits did their job (every run stopped and the context stayed
under about 7,300 characters), but the teachers asking questions got nothing.

| Task | What happened | Type of failure |
|---|---|---|
| T2 Venus | Found 872 °F / 467 °C and converted it to **740.15 K at step 2**, then kept "confirming" with more searches until the step limit. No answer. | **Agent loop:** nothing in the loop told the model it already had enough. The conversion result was also pushed out of the full-text window into a shortened note (**context**). |
| T3 Shortest day | Searched one planet at a time with the planet filter (Mercury, Venus, Earth, Mars...) instead of one unfiltered search, which returns "Jupiter has the shortest day" first. Ran out of steps. | **Inefficient tool use / model reasoning.** The loop let it keep going with no pressure to finish. |
| T1 Jupiter vs Saturn | Searched "Jupiter diameter", but the Jupiter page only says "radius", so BM25 returned a Magnetosphere passage. The model repeated the same query until the repeat limit stopped it. | **Retrieval** (word mismatch), then **agent loop** (no change of plan after a bad result). |
| T4 Neptune | Found "16 known moons" at step 1, then repeated the same "Voyager 2 cost" search until the tool-error limit. Never said what it found. | **Agent loop:** no way to finish with a partial answer. |

The shared root cause is in the loop, not the tools: the loop's only way to
stop was hitting a limit.

### Fix 1 (v2): answer-now mode

Changes in `agent.py` (`finish_guard=True`):

1. Results from `calculate` and `convert_units` go into a permanent
   "ESTABLISHED RESULTS" list in the context, so they are never shrunk away.
2. When the model repeats a call it already made, the loop skips the call and
   switches to **answer-now mode**. It does the same when only the last steps
   are left. In answer-now mode the context ends with: "Your next action MUST be
   final_answer... For any part the NASA pages did not cover, say so plainly."

Result: 3 of 4 answered, in about half the time. T2 now answers 740.15 K in 4
steps instead of failing in 8.

### Fix 2 (v3): no giving up after finding something

v2 showed a new problem. On T4 the model called `give_up` after it had already
found "16 known moons", and the loop accepted it. Now
(`partial_answer_guard=True`), if the agent tries to give up after finding
relevant passages, the loop refuses once and asks for a partial answer that
says what the collection does not cover. Giving up is still allowed when
nothing relevant was found. In the v3 run and in 3 extra runs of T4
(`logs/v3-repeat1` to `v3-repeat3`), the agent answered "16 known moons" and said
the cost is not in the NASA pages every time. Because the model did not try to
give up in those runs, `test_agent.py` also tests the rule directly with a
scripted model: `test_v3_refuses_give_up_after_finding_passages` passes, and
`test_v2_accepts_give_up` shows the old behavior.

### Problems that are still there

- **Arithmetic in the model's head (model reasoning):** in v2 and v3 the model
  often skipped `calculate` (T1's 1.16 and v2's T3). The numbers were right,
  but nothing forces the tool to be used. A next step would be for the loop to
  reject a final answer with numbers that no tool produced.
- **Backwards wording (model reasoning):** in v2, T3 said "2.43 Earth days fit
  into one Jupiter day", which is the wrong way around (it should be 2.4
  Jupiter days in one Earth day). v3 phrased it correctly, but that is the
  model varying from run to run, not a fix.
- **Planet-by-planet searching (inefficient tool use):** T3 still spends 5
  searches before it finds Jupiter. A better tool description, or a hint that
  `search_docs` without `planet` searches every page, would cut this down.
- **Word mismatch in retrieval:** "diameter" does not match "radius". Adding
  simple synonyms to the query (size, radius, diameter, wide) would help BM25.
