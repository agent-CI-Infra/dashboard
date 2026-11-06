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
"""
import datetime
import json
import os
import re
import sys

LOG = os.environ.get("DM_LOG", "/data/state/workswarm/process.log")
CST = datetime.timezone(datetime.timedelta(hours=8))
UTC = datetime.timezone.utc

_until = os.environ.get("DM_UNTIL", "").strip()
UNTIL = None
if _until:
    UNTIL = datetime.datetime.fromisoformat(_until.replace("Z", "+00:00")).timestamp()

REQ = re.compile(r"request_id=")
TTFT = re.compile(r"'ttft_ms':\s*([0-9.]+)")
TPOT = re.compile(r"'tpot_ms':\s*([0-9.]+)")
TOK_IN = re.compile(r"'input_tokens':\s*(\d+)")
TOK_OUT = re.compile(r"'output_tokens':\s*(\d+)")
TOK_ALL = re.compile(r"'total_tokens':\s*(\d+)")
MODEL = re.compile(r"'model_name':\s*'([^']+)'")


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


days = {}
requests = {}
first_ts = None
noise = 0


def bucket(day):
    return days.setdefault(day, {
        "gateway": [], "workswarm": [], "overhead": [],
        "ttft": [], "tpot": [],
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
        if at:
            rid = line[at.end():].split()[0].strip(",;")
            if rid.startswith("digitalmate-"):
                spans = requests.setdefault(rid, [None, None, None, None])
                if "[MessageHandler] dispatch:" in line and spans[0] is None:
                    spans[0] = ts
                elif "[MessageHandler] Stream " in line and "total_chunks=" in line and spans[1] is None:
                    spans[1] = ts
                elif "[E2A][in]" in line and spans[2] is None:
                    spans[2] = ts
                elif "run_stream_task finished" in line and spans[3] is None:
                    spans[3] = ts

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

# Requests belong to the day their gateway dispatch happened, so a request that
# crosses midnight is counted once instead of being split by marker day.
for rid, (g0, g1, w0, w1) in requests.items():
    if g0 is None:
        continue
    rec = bucket(day_of(g0))
    rec.setdefault("requests", set()).add(rid)
    if g1 is not None:
        rec["gateway"].append(round((g1 - g0) * 1000.0, 1))
    if w0 is not None and w1 is not None:
        rec["workswarm"].append(round((w1 - w0) * 1000.0, 1))
        if g1 is not None:
            rec["overhead"].append(round((g1 - g0 - (w1 - w0)) * 1000.0, 1))

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
        "tokens": {"input": rec["input"], "output": rec["output"], "total": rec["total"]},
        "calls": {
            "total": rec["success"] + rec["failed"],
            "success": rec["success"],
            "failed": rec["failed"],
        },
        "models": rec["models"],
    })

json.dump({"days": out, "noise": noise, "first_ts": first_ts}, sys.stdout, ensure_ascii=False)
