"""Run with: /usr/bin/python3 -m unittest discover -s <mod>/bin"""

import datetime
import json
import os
import tempfile
import time
import unittest

import tally_tokens

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def iso(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def assistant_line(request_id, timestamp, input_tokens=0, cache_creation=0, cache_read=0, output=0):
    return json.dumps({
        "type": "assistant",
        "requestId": request_id,
        "timestamp": iso(timestamp),
        "message": {"id": "msg_" + request_id, "usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output,
        }},
    }) + "\n"


class TallyTokensTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = self.temporary.name
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(root, "config")
        self.project = os.path.join(root, "config", "projects", "-some-project")
        os.makedirs(os.path.join(self.project, SESSION_ID, "subagents"))
        self.main = os.path.join(self.project, SESSION_ID + ".jsonl")
        tally_tokens.CACHE_DIRECTORY = os.path.join(root, "cache")
        self.now = time.time()

    def append(self, path, text):
        with open(path, "a") as transcript:
            transcript.write(text)

    def test_counts_input_cache_writes_and_output_but_not_cache_reads(self):
        self.append(self.main, assistant_line("a", self.now - 10, input_tokens=2, cache_creation=100, cache_read=50_000, output=30))
        self.assertEqual(tally_tokens.tally(SESSION_ID), {"today": 132, "lastHour": 132, "requestsToday": 1})

    def test_one_response_written_as_several_lines_counts_once_at_its_final_size(self):
        self.append(self.main, assistant_line("a", self.now - 10, output=5))
        self.append(self.main, assistant_line("a", self.now - 9, output=40))
        self.append(self.main, assistant_line("a", self.now - 8, output=40))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 40)

    def test_subagent_transcripts_are_included(self):
        self.append(self.main, assistant_line("main", self.now - 10, output=10))
        subagent = os.path.join(self.project, SESSION_ID, "subagents", "agent-x.jsonl")
        self.append(subagent, assistant_line("sub", self.now - 5, cache_creation=7))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 17)

    def test_non_usage_and_user_lines_are_ignored(self):
        self.append(self.main, json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        self.append(self.main, json.dumps({"type": "user", "toolUseResult": {"usage": {"output_tokens": 999}}}) + "\n")
        self.append(self.main, assistant_line("a", self.now - 10, output=3))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 3)

    def test_appended_lines_are_counted_on_the_next_run(self):
        self.append(self.main, assistant_line("a", self.now - 10, output=3))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 3)
        self.append(self.main, assistant_line("b", self.now - 5, output=4))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 7)

    def test_a_line_still_being_written_waits_for_its_newline(self):
        complete = assistant_line("a", self.now - 10, output=3)
        partial = assistant_line("b", self.now - 5, output=4)
        self.append(self.main, complete + partial[:20])
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 3)
        self.append(self.main, partial[20:])
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 7)

    def test_a_rewritten_shorter_transcript_is_rescanned_from_the_start(self):
        self.append(self.main, assistant_line("a", self.now - 10, output=3) + assistant_line("b", self.now - 9, output=4))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 7)
        with open(self.main, "w") as transcript:
            transcript.write(assistant_line("c", self.now - 5, output=5))
        self.assertEqual(tally_tokens.tally(SESSION_ID)["today"], 5)

    def test_windows_split_today_and_the_last_hour(self):
        midnight = tally_tokens.local_midnight(self.now)
        self.append(self.main, assistant_line("yesterday", midnight - 60, output=1_000))
        self.append(self.main, assistant_line("two-hours-ago", self.now - 7_200, output=100))
        self.append(self.main, assistant_line("recent", self.now - 60, output=10))
        result = tally_tokens.tally(SESSION_ID)
        # Before 02:00 local, "two hours ago" is yesterday; the expectation follows the real clock.
        expected_today = 10 + (100 if self.now - 7_200 >= midnight else 0)
        recent_yesterday = 1_000 if midnight - 60 >= self.now - 3_600 else 0
        self.assertEqual(result["today"], expected_today)
        self.assertEqual(result["lastHour"], 10 + recent_yesterday)

    def test_cache_drops_requests_outside_both_windows(self):
        self.append(self.main, assistant_line("old", self.now - 3 * 86_400, output=1))
        self.append(self.main, assistant_line("recent", self.now - 60, output=10))
        tally_tokens.tally(SESSION_ID)
        with open(os.path.join(tally_tokens.CACHE_DIRECTORY, SESSION_ID + ".json")) as cache_file:
            self.assertEqual(set(json.load(cache_file)["requests"]), {"recent"})

    def test_a_session_with_no_transcript_yet_reports_that_instead_of_failing(self):
        # A new session writes its transcript only once the first message is sent.
        self.assertEqual(tally_tokens.report(SESSION_ID), {"transcriptWritten": False})

    def test_report_carries_the_tally_once_the_transcript_exists(self):
        self.append(self.main, assistant_line("a", self.now - 10, output=3))
        self.assertEqual(
            tally_tokens.report(SESSION_ID),
            {"transcriptWritten": True, "today": 3, "lastHour": 3, "requestsToday": 1},
        )

    def test_two_transcripts_for_one_session_are_still_an_error(self):
        other_project = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-other-project")
        os.makedirs(other_project)
        self.append(self.main, assistant_line("a", self.now - 10, output=3))
        self.append(os.path.join(other_project, SESSION_ID + ".jsonl"), assistant_line("b", self.now - 10, output=3))
        with self.assertRaises(RuntimeError):
            tally_tokens.report(SESSION_ID)

    def test_missing_transcript_is_an_error_not_zero(self):
        with self.assertRaises(RuntimeError):
            tally_tokens.tally("00000000-0000-0000-0000-000000000000")


if __name__ == "__main__":
    unittest.main()
