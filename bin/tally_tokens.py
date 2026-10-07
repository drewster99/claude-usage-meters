"""Tally one Claude Code session's non-cache-read tokens (uncached input + cache writes + output),
main thread plus its subagents, for today (local midnight onward) and the last 60 minutes.

The session's transcript files are the source of truth. They can exceed what a plugin hook may read
in one call and in its 1.5 s budget, so each file is consumed incrementally from a byte offset
remembered in a cache file; a run only parses lines appended since the previous run.

Usage: tally_tokens.py <session-id>
Prints one JSON object: {"transcriptWritten": true, "today": int, "lastHour": int, "requestsToday": int},
or {"transcriptWritten": false} for a session that has not written its transcript yet.
"""

import datetime
import fcntl
import glob
import json
import os
import sys
import time

CACHE_DIRECTORY = os.path.expanduser("~/Library/Caches/claude-usage-meters")
LAST_HOUR_SECONDS = 3600


def claude_config_directory():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return configured if configured else os.path.expanduser("~/.claude")


class TranscriptNotWrittenYet(RuntimeError):
    """A new session creates its transcript file only once the first message is sent."""


def find_main_transcript(session_id):
    pattern = os.path.join(claude_config_directory(), "projects", "*", session_id + ".jsonl")
    matches = glob.glob(pattern)
    if not matches:
        raise TranscriptNotWrittenYet("no transcript matching %s yet" % pattern)
    if len(matches) > 1:
        raise RuntimeError("expected exactly one transcript matching %s, found %d" % (pattern, len(matches)))
    return matches[0]


def transcript_files(main_transcript, session_id):
    subagent_pattern = os.path.join(os.path.dirname(main_transcript), session_id, "subagents", "*.jsonl")
    return [main_transcript] + sorted(glob.glob(subagent_pattern))


def parse_timestamp(text):
    # Python 3.9's fromisoformat rejects the trailing "Z".
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def record_line(line, requests):
    # Most lines carry no usage; skipping them before json.loads keeps a large backlog cheap.
    if '"usage"' not in line:
        return
    entry = json.loads(line)
    if entry.get("type") != "assistant":
        return
    message = entry.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return
    # One response is written as several lines (one per content block), each repeating its usage,
    # so the request is the unit of counting, not the line.
    request_key = entry.get("requestId") or message.get("id")
    if not request_key:
        raise RuntimeError("assistant usage line has neither requestId nor message id")
    tokens = (usage.get("input_tokens", 0)
              + usage.get("cache_creation_input_tokens", 0)
              + usage.get("output_tokens", 0))
    timestamp = parse_timestamp(entry["timestamp"])
    previous = requests.get(request_key)
    # Earlier lines of a streamed response can carry a partial output count; the largest is final.
    if previous is None or tokens > previous[1]:
        requests[request_key] = [timestamp, tokens]


def consume_appended_lines(path, file_state, requests):
    size = os.path.getsize(path)
    offset = file_state.get("offset", 0)
    if size < offset:
        raise TranscriptRewritten(path)
    if size == offset:
        return
    with open(path, "rb") as transcript:
        transcript.seek(offset)
        appended = transcript.read(size - offset)
    # A line still being written has no newline yet; leave it for the next run.
    last_newline = appended.rfind(b"\n")
    if last_newline < 0:
        return
    for raw_line in appended[:last_newline].split(b"\n"):
        if raw_line.strip():
            record_line(raw_line.decode("utf-8"), requests)
    file_state["offset"] = offset + last_newline + 1


class TranscriptRewritten(Exception):
    pass


def local_midnight(now):
    today = datetime.datetime.fromtimestamp(now).date()
    return datetime.datetime.combine(today, datetime.time()).timestamp()


def load_cache(cache_path):
    if not os.path.exists(cache_path):
        return {"files": {}, "requests": {}}
    with open(cache_path) as cache_file:
        return json.load(cache_file)


def save_cache(cache_path, cache):
    temporary_path = cache_path + ".tmp"
    with open(temporary_path, "w") as cache_file:
        json.dump(cache, cache_file)
    os.replace(temporary_path, cache_path)


def update_cache(cache, files):
    for path in files:
        consume_appended_lines(path, cache["files"].setdefault(path, {}), cache["requests"])


def tally(session_id):
    main_transcript = find_main_transcript(session_id)
    files = transcript_files(main_transcript, session_id)
    os.makedirs(CACHE_DIRECTORY, exist_ok=True)
    cache_path = os.path.join(CACHE_DIRECTORY, session_id + ".json")
    # The status line's timer and its per-response refresh can run this concurrently.
    with open(cache_path + ".lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        cache = load_cache(cache_path)
        try:
            update_cache(cache, files)
        except TranscriptRewritten:
            cache = {"files": {}, "requests": {}}
            update_cache(cache, files)
        now = time.time()
        today_start = local_midnight(now)
        hour_start = now - LAST_HOUR_SECONDS
        # Nothing before both windows can count again, so the cache stays bounded to about a day.
        oldest_needed = min(today_start, hour_start)
        cache["requests"] = {key: value for key, value in cache["requests"].items() if value[0] >= oldest_needed}
        save_cache(cache_path, cache)
    today_values = [tokens for timestamp, tokens in cache["requests"].values() if timestamp >= today_start]
    last_hour = sum(tokens for timestamp, tokens in cache["requests"].values() if timestamp >= hour_start)
    return {"today": sum(today_values), "lastHour": last_hour, "requestsToday": len(today_values)}


def report(session_id):
    try:
        counts = tally(session_id)
    except TranscriptNotWrittenYet:
        return {"transcriptWritten": False}
    return dict({"transcriptWritten": True}, **counts)


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: tally_tokens.py <session-id>\n")
        return 2
    print(json.dumps(report(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
