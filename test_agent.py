"""Tests for the agent loop that do not need Ollama.

A scripted fake model replaces ask_model, so each test knows exactly what the
"model" will do and can check how the loop reacts.

Run:  python -m unittest test_agent -v
"""

import json
import unittest
from unittest import mock

import agent


def scripted(*decisions):
    """Return a fake ask_model that replies with these decisions in order."""
    replies = iter(json.dumps(d) for d in decisions)
    return lambda messages: next(replies)


SEARCH = {"thought": "look it up", "action": "search_docs",
          "args": {"query": "Neptune moons", "planet": "neptune"}}
GIVE_UP = {"thought": "cannot find the cost", "action": "give_up", "args": {"reason": "no cost info"}}
ANSWER = {"thought": "done", "action": "final_answer",
          "args": {"answer": "16 known moons; the pages do not give the cost.",
                   "sources": ["https://science.nasa.gov/neptune/facts/"]}}


def quiet(*args):
    pass


class AgentLoopTests(unittest.TestCase):

    def test_v3_refuses_give_up_after_finding_passages(self):
        with mock.patch.object(agent, "ask_model", scripted(SEARCH, GIVE_UP, ANSWER)):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["outcome"]["status"], "answered")
        self.assertEqual(result["steps"][1]["result"]["note"], "give_up refused once: answer the part you found.")
        self.assertEqual(result["outcome"]["sources"], ["https://science.nasa.gov/neptune/facts/"])

    def test_v2_accepts_give_up(self):
        with mock.patch.object(agent, "ask_model", scripted(SEARCH, GIVE_UP)):
            result = agent.run_agent("task", log=quiet, partial_answer_guard=False)
        self.assertEqual(result["outcome"]["status"], "gave_up")

    def test_give_up_without_any_passages_is_allowed(self):
        with mock.patch.object(agent, "ask_model", scripted(GIVE_UP)):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["outcome"]["status"], "gave_up")

    def test_step_limit_stops_the_loop(self):
        searches = [{"thought": "again", "action": "search_docs", "args": {"query": f"moons {i}"}}
                    for i in range(agent.MAX_STEPS + 5)]
        with mock.patch.object(agent, "ask_model", scripted(*searches)):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["outcome"]["status"], "stopped_step_limit")
        self.assertEqual(len(result["steps"]), agent.MAX_STEPS)

    def test_repeat_switches_to_answer_now(self):
        with mock.patch.object(agent, "ask_model", scripted(SEARCH, SEARCH, ANSWER)):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["steps"][1]["answer_now"], "you repeated a call you already made")
        self.assertEqual(result["outcome"]["status"], "answered")

    def test_bad_json_is_retried_then_stops(self):
        with mock.patch.object(agent, "ask_model", lambda messages: "not json"):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["outcome"]["status"], "stopped_bad_format")
        self.assertEqual(len(result["steps"][0]["format_problems"]), agent.MAX_FORMAT_RETRIES + 1)

    def test_uncited_source_is_flagged(self):
        fake = dict(ANSWER, args={"answer": "x", "sources": ["https://example.com/made-up"]})
        with mock.patch.object(agent, "ask_model", scripted(SEARCH, fake)):
            result = agent.run_agent("task", log=quiet)
        self.assertEqual(result["outcome"]["sources"], [])
        self.assertEqual(result["outcome"]["unverified_sources"], ["https://example.com/made-up"])

    def test_context_stays_bounded(self):
        searches = [{"thought": "more", "action": "search_docs", "args": {"query": f"planet facts {i}"}}
                    for i in range(agent.MAX_STEPS)]
        with mock.patch.object(agent, "ask_model", scripted(*searches)):
            result = agent.run_agent("task", log=quiet, finish_guard=False)
        sizes = [s["context_chars"] for s in result["steps"]]
        self.assertLess(max(sizes), 12000)
        self.assertLessEqual(max(s["notes_in_context"] for s in result["steps"]), agent.MAX_NOTES)


if __name__ == "__main__":
    unittest.main()
