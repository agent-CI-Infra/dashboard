#!/usr/bin/env python3
"""Aggregate one environment's cloud-side request metrics from its agent log.

Runs inside a DigitalMate environment (uploaded and executed by
scripts/collect-cloud-request-metrics.py), not on the GitHub runner.

Reads /data/state/workswarm/process.log and prints one JSON document with the
raw per-day samples for every Asia/Shanghai day in the log. Percentiles are
computed by the caller after samples from every environment are pooled, so
this script must not summarise them itself. Timestamps in the log are UTC;
day bucketing is UTC+8.

Optional environment:
  DM_LOG    log path (default /data/state/workswarm/process.log)
  DM_UNTIL  ISO-8601 UTC cut-off, for reproducing a historical snapshot

Tool durations come from tool-calls-*.jsonl files in the log's directory. The
product adapter writes one JSON record per finished tool call there, next to
process.log, so this script does not depend on the gateway's log level.
"""
import datetime
import glob
import json
import math
import os
import re
import sys

LOG = os.environ.get("DM_LOG", "/data/state/workswarm/process.log")
TOOL_GLOB = os.path.join(os.path.dirname(LOG), "tool-calls-*.jsonl")
CST = datetime.timezone(datetime.timedelta(hours=8))
UTC = datetime.timezone.utc

_until = os.environ.get("DM_UNTIL", "").strip()
UNTIL = None
if _until:
    UNTIL = datetime.datetime.fromisoformat(_until.replace("Z", "+00:00")).timestamp()

REQ = re.compile(r"request_id=")
# The gateway logs the head of every stream and the first chunk of every event
# type, which is where the cloud side's first visible token and first answer
# text show up. Logs written before that change only carry the first three
# chunks, so a stream whose answer starts later contributes no answer sample.
CHUNK = re.compile(r"\[MessageHandler\] process_stream chunk #(\d+): request_id=\S+ event_type=(\S*)")
# Event types that carry text the user can see; the first one of these marks the
# cloud side's first token (chat.reasoning is the model's visible thinking).
VISIBLE = {"chat.delta", "chat.reasoning"}
# Answer text only. A stream usually opens with status/thinking chunks and only
# starts emitting chat.delta once the ReAct loop has produced its reply, so the
# gap between the two markers is the thinking/tool phase.
ANSWER = {"chat.delta"}
TTFT = re.compile(r"'ttft_ms':\s*([0-9.]+)")
TPOT = re.compile(r"'tpot_ms':\s*([0-9.]+)")
TOK_IN = re.compile(r"'input_tokens':\s*(\d+)")
TOK_OUT = re.compile(r"'output_tokens':\s*(\d+)")
TOK_ALL = re.compile(r"'total_tokens':\s*(\d+)")
MODEL = re.compile(r"'model_name':\s*'([^']+)'")
# Provider codes must be whole numbers; a request id or timestamp containing
# 429 or 81011 is not an error code.
CODE_429 = re.compile(r"\b429\b")
CODE_CONTENT_SAFETY = re.compile(r"\b81011\b")
CODE_INVALID_REQUEST = re.compile(r"\b81001\b")


def stamp(line):
    """Millisecond timestamp at the start of a log line, in UTC seconds."""
    s = line[:23]
    if len(s) < 23 or s[4] != "-" or s[19] != ".":
        return None
    try:
        return datetime.datetime(
            int(s[0:4]), int(s[5:7]), int(s[8:10]),
            int(s[11:13]), int(s[14:16]), int(s[17:19]),
            tzinfo=UTC,
        ).timestamp() + int(s[20:23]) / 1000.0
    except ValueError:
        return None


def day_of(ts):
    return datetime.datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d")


def error_category(line):
    """Classify one llm_call_error line without keeping its content."""
    text = line.lower()
    if "timeout" in text or "timed out" in text or "deadline exceeded" in text:
        return "timeout"
    if "budget" in text and "exceed" in text:
        return "budget"
    if "rate_limit" in text or "rate limit" in text or CODE_429.search(text):
        return "rate_limit"
    if ("sensitive" in text or CODE_CONTENT_SAFETY.search(text) or "content_policy" in text
            or "content filter" in text or "content_filter" in text):
        return "content_blocked"
    if "invalid_request" in text or "invalid request" in text or CODE_INVALID_REQUEST.search(text):
        return "invalid_request"
        return "invalid_request"
    if "permission_denied" in text or "permission denied" in text:
        return "permission_denied"
    return "other"


days = {}
requests = {}
first_ts = None
noise = 0


def bucket(day):
    return days.setdefault(day, {
        "gateway": [], "workswarm": [], "overhead": [],
        "ttft": [], "tpot": [], "first_token": [], "answer_first": [], "react": [],
        "tool": [], "tool_failed": 0,
        # Per-request phase values, aligned in one row so the collector can
        # identify which phase makes a request an outlier. Row layout:
        # [gateway, first_token, answer_first, react, workswarm, request_id].
        "samples": [],
        # Failed calls grouped by cause, plus the ids of timed-out requests so
        # the board can point an operator at the exact request.
        "error_categories": {}, "timeout_rids": [],
        # Client-observed one-way network samples arrive with a later gateway
        # release; keep the buckets so the collector contract is already in
        # place when the log markers are added.
        "uplink_send": [], "downlink_first_text": [], "downlink_terminal": [],
        "input": 0, "output": 0, "total": 0,
        "success": 0, "failed": 0, "models": {},
    })


with open(LOG, encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        ts = stamp(line)
        if ts is None:
            continue
        if UNTIL is not None and ts > UNTIL:
            continue
        if first_ts is None:
            first_ts = ts
        rec = bucket(day_of(ts))

        at = REQ.search(line)
        rid = None
        if at:
            rid = line[at.end():].split()[0].strip(",;")
            if rid.startswith("digitalmate-"):
                # dispatch, stream end, agent entry, agent exit, first text
                # chunk, first answer chunk
                spans = requests.setdefault(rid, [None, None, None, None, None, None])
                if "[MessageHandler] dispatch:" in line and spans[0] is None:
                    spans[0] = ts
                elif "[MessageHandler] Stream " in line and "total_chunks=" in line and spans[1] is None:
                    spans[1] = ts
                elif "[E2A][in]" in line and spans[2] is None:
                    spans[2] = ts
                elif "run_stream_task finished" in line and spans[3] is None:
                    spans[3] = ts
                chunk = CHUNK.search(line)
                if chunk and spans[4] is None and chunk.group(2) in VISIBLE:
                    spans[4] = ts
                if chunk and spans[5] is None and chunk.group(2) in ANSWER:
                    spans[5] = ts

        if "llm_usage" in line:
            if "llm_usage chunk: type='llm_usage'" not in line:
                noise += 1
                continue
            rec["success"] += 1
            model = MODEL.search(line)
            if model:
                name = model.group(1)
                rec["models"][name] = rec["models"].get(name, 0) + 1
            tin = TOK_IN.search(line)
            tout = TOK_OUT.search(line)
            tall = TOK_ALL.search(line)
            if tin:
                rec["input"] += int(tin.group(1))
            if tout:
                rec["output"] += int(tout.group(1))
            if tall:
                rec["total"] += int(tall.group(1))
            ttft = TTFT.search(line)
            if ttft:
                rec["ttft"].append(round(float(ttft.group(1)), 2))
            tpot = TPOT.search(line)
            if tpot and tout and int(tout.group(1)) > 1:
                rec["tpot"].append([round(float(tpot.group(1)), 2), int(tout.group(1)) - 1])

        if "llm_call_error" in line:
            rec["failed"] += 1
            category = error_category(line)
            rec["error_categories"][category] = rec["error_categories"].get(category, 0) + 1
            if category == "timeout" and rid and rid.startswith("digitalmate-"):
                rec["timeout_rids"].append(rid)

# Tool durations are one JSON record per line, written by the product adapter.
# A record can be half-written while the process is appending; parse failures
# are skipped, and the next run picks the complete record up.
for path in sorted(glob.glob(TOOL_GLOB)):
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        continue
    with fh:
        for line in fh:
            try:
                item = json.loads(line)
                t = float(item["t"])
                ms = float(item["ms"])
            except (ValueError, TypeError, KeyError):
                continue
            if not math.isfinite(t) or not math.isfinite(ms) or ms < 0:
                continue
            if UNTIL is not None and t > UNTIL:
                continue
            if first_ts is None or t < first_ts:
                first_ts = t
            rec = bucket(day_of(t))
            rec["tool"].append(round(ms, 1))
            if item.get("status") == "error":
                rec["tool_failed"] += 1

# Requests belong to the day their gateway dispatch happened, so a request that
# crosses midnight is counted once instead of being split by marker day.
for rid, (g0, g1, w0, w1, t0, a1) in requests.items():
    if g0 is None:
        continue
    rec = bucket(day_of(g0))
    rec.setdefault("requests", set()).add(rid)
    if g1 is not None:
        rec["gateway"].append(round((g1 - g0) * 1000.0, 1))
    # Cloud first-token latency: from the gateway accepting the request to the
    # first chunk that carries visible text. Independent of the model call's own
    # ttft_ms: it also covers routing, agent wake-up and pre-token work.
    if t0 is not None:
        rec["first_token"].append(round((t0 - g0) * 1000.0, 1))
    # Answer first token: the same span, but the answer text only — a request
    # that opens with thinking/status chunks reaches this marker later.
    if a1 is not None:
        rec["answer_first"].append(round((a1 - g0) * 1000.0, 1))
    # ReAct phase: first visible text -> first answer text, i.e. the thinking
    # and tool-calling the agent does before it starts writing the answer. Zero
    # when the reply opens straight with the answer.
    if t0 is not None and a1 is not None and a1 >= t0:
        rec["react"].append(round((a1 - t0) * 1000.0, 1))
    if w0 is not None and w1 is not None:
        rec["workswarm"].append(round((w1 - w0) * 1000.0, 1))
        if g1 is not None:
            rec["overhead"].append(round((g1 - g0 - (w1 - w0)) * 1000.0, 1))
    # One aligned row per request for the collector's outlier analysis. The
    # separate arrays above remain the source for the daily percentiles; this
    # row is the only place where a request's phases are joined, and it is
    # reduced to anomaly counts before anything is published.
    sample = [
        round((g1 - g0) * 1000.0, 1) if g1 is not None else None,
        round((t0 - g0) * 1000.0, 1) if t0 is not None else None,
        round((a1 - g0) * 1000.0, 1) if a1 is not None else None,
        round((a1 - t0) * 1000.0, 1) if t0 is not None and a1 is not None and a1 >= t0 else None,
        round((w1 - w0) * 1000.0, 1) if w0 is not None and w1 is not None else None,
        rid,
    ]
    if any(value is not None for value in sample[:5]):
        rec["samples"].append(sample)

out = []
for day in sorted(days):
    rec = days[day]
    out.append({
        "date": day,
        "rids": sorted(rec.get("requests", ())),
        "gateway": rec["gateway"],
        "workswarm": rec["workswarm"],
        "overhead": rec["overhead"],
        "ttft": rec["ttft"],
        "tpot": rec["tpot"],
        "first_token": rec["first_token"],
        "answer_first": rec["answer_first"],
        "react": rec["react"],
        "samples": rec["samples"],
        "error_categories": rec["error_categories"],
        "timeout_rids": sorted(set(rec["timeout_rids"])),
        "uplink_send": rec["uplink_send"],
        "downlink_first_text": rec["downlink_first_text"],
        "downlink_terminal": rec["downlink_terminal"],
        "tool": rec["tool"],
        "tool_failed": rec["tool_failed"],
        "tokens": {"input": rec["input"], "output": rec["output"], "total": rec["total"]},
        "calls": {
            "total": rec["success"] + rec["failed"],
            "success": rec["success"],
            "failed": rec["failed"],
        },
        "models": rec["models"],
    })

json.dump({"days": out, "noise": noise, "first_ts": first_ts}, sys.stdout, ensure_ascii=False)
