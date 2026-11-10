#!/usr/bin/env python3
"""Collect cloud-side (test-0) request metrics and merge them into the board JSON.

The agent logs live behind the ADX console API. The DigitalMate alias runs
several environment instances that share the traffic, so this script opens a
terminal in each of them, uploads scripts/cloud-request-log-aggregate.py, and
pools the per-day samples before computing statistics. A day from the previous
document is replaced only when the fresh pool covers at least as many requests,
so an environment restart cannot shrink history.

Usage:
  collect-cloud-request-metrics.py --raw                 # print pooled days
  collect-cloud-request-metrics.py OUTPUT PREVIOUS       # write merged JSON

Environment:
  ADX_TOKEN  bearer token for the console API (required)
  ADX_BASE   console base URL (default https://onlineswarm.openjiuwen.com:31472)
  DM_UNTIL   optional ISO-8601 cut-off passed to the in-env aggregator
"""
import base64
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ADX_BASE", "https://onlineswarm.openjiuwen.com:31472").rstrip("/")
TOKEN = os.environ.get("ADX_TOKEN", "")
TEMPLATE = os.environ.get("ADX_TEMPLATE", "digitalmate")
REFERENCE = os.environ.get("ADX_REFERENCE", "production")
KIND = os.environ.get("ADX_KIND", "aliases")
CHUNK = 1800
CST = datetime.timezone(datetime.timedelta(hours=8))
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")

AGGREGATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud-request-log-aggregate.py")


class AdxError(RuntimeError):
    """A failed ADX API call.

    Raised as a normal exception rather than SystemExit: several environments
    are collected per run, and one unreachable environment (e.g. a deleted
    generation whose terminal can no longer be opened) must not abort the
    others.
    """

    def __init__(self, status, path, detail=""):
        super().__init__(f"ADX API HTTP {status} {path}: {detail}".rstrip(": "))
        self.status = status


def call(path, method="GET", payload=None, timeout=60):
    headers = {"authorization": "Bearer " + TOKEN}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["content-type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise AdxError(error.code, path, detail) from None


def bindings():
    """Read every page; an incomplete inventory must never look complete."""
    items = []
    token = None
    seen_tokens = set()
    for _ in range(1000):
        params = {"template": TEMPLATE, "reference": REFERENCE, "kind": KIND, "page_size": 50}
        if token:
            params["page_token"] = token
        data = call(f"/api/bindings?{urllib.parse.urlencode(params)}")
        page = data.get("items") if isinstance(data, dict) else None
        if not isinstance(page, list):
            raise SystemExit("invalid ADX bindings page")
        items.extend(page)
        token = data.get("next_page_token")
        if not token:
            if not items:
                raise SystemExit("no ADX bindings for the DigitalMate environment")
            return items
        if not isinstance(token, str) or token in seen_tokens:
            raise SystemExit("invalid or repeated ADX pagination token")
        seen_tokens.add(token)
    raise SystemExit("ADX bindings pagination exceeds safety limit")


class Terminal:
    def __init__(self, binding):
        self.binding = binding
        self._open()
        self.settle(3.0)

    def _open(self):
        item = self.binding
        created = call(
            "/api/terminals",
            "POST",
            {
                "template": TEMPLATE,
                "kind": KIND,
                "reference": REFERENCE,
                "binding_id": item["binding"]["scope"]["binding_id"],
                "generation": item["binding"]["generation"],
                "environment_id": item["binding"]["current"]["environment_id"],
                "wake": True,
                "rows": 50,
                "cols": 200,
            },
        )
        self.id = created["id"]
        self.offset = 0
        self.seq = 0

    def _read(self):
        data = call(f"/api/terminals/{self.id}?offset={self.offset}", timeout=30)
        self.offset = data.get("offset", self.offset)
        text = []
        for event in data.get("events", []):
            if event.get("type") == "output":
                text.append(base64.b64decode(event["data"]).decode("utf-8", "replace"))
        return "".join(text)

    def settle(self, wait, idle_limit=1.2):
        deadline = time.time() + wait
        idle = time.time()
        out = []
        while time.time() < deadline:
            chunk = self._read()
            if chunk:
                idle = time.time()
                out.append(chunk)
            elif time.time() - idle > idle_limit:
                break
            time.sleep(0.25)
        return ANSI.sub("", "".join(out))

    def send(self, command):
        # The console sits behind a WAF that throttles bursts of terminal input
        # and drops the terminal when it blocks a payload; pace the writes and
        # reopen the terminal when it disappears.
        payload = base64.b64encode((command + "\r").encode()).decode()
        for attempt in range(5):
            self.seq += 1
            try:
                call(f"/api/terminals/{self.id}", "POST", {"type": "input", "data": payload, "seq": self.seq})
                time.sleep(1.2)
                return
            except AdxError as error:
                message = str(error)
                if attempt == 4:
                    raise
                if "terminal_missing" in message or "HTTP 404" in message:
                    time.sleep(2)
                    self._open()
                    self.settle(2.0)
                    continue
                if "HTTP 418" not in message:
                    raise
                time.sleep(8 + attempt * 6)
                try:
                    self._read()
                except AdxError:
                    self._open()
                    self.settle(2.0)

    def close(self):
        try:
            call(f"/api/terminals/{self.id}", "DELETE", timeout=10)
        except Exception:
            pass


def collect_environment(binding, script, until=""):
    encoded = base64.b64encode(script.encode()).decode()
    terminal = Terminal(binding)
    try:
        terminal.settle(2.0)
        terminal.send("rm -f /tmp/dmagg.b64 /tmp/dmagg.json /tmp/dmagg.err")
        for start in range(0, len(encoded), CHUNK):
            terminal.send(f"echo {encoded[start:start + CHUNK]} >> /tmp/dmagg.b64")
        # One command per input: the console WAF rejects some compound lines.
        terminal.send("base64 --decode /tmp/dmagg.b64 > /tmp/dmagg.py")
        prefix = f"DM_UNTIL={until} " if until else ""
        terminal.send(f"{prefix}python3 /tmp/dmagg.py > /tmp/dmagg.json 2>/tmp/dmagg.err")
        terminal.send("echo ==DM_BEGIN==")
        terminal.send("cat /tmp/dmagg.json")
        terminal.send("echo ==DM_END==")
        terminal.send("cat /tmp/dmagg.err")
        deadline = time.time() + 240
        buffer = ""
        while time.time() < deadline:
            buffer += terminal.settle(2.0, 2.5)
            if "==DM_END==" in buffer:
                break
        if "==DM_END==" not in buffer:
            raise RuntimeError("aggregator did not finish")
        body = buffer.rsplit("==DM_BEGIN==", 1)[1].split("==DM_END==", 1)[0].strip()
        start = body.find("{")
        if start < 0:
            raise RuntimeError("aggregator produced no JSON")
        # The shell echoes the delimiter commands themselves, so the captured
        # body can carry a trailing "echo " fragment; decode only the JSON.
        return json.JSONDecoder().raw_decode(body[start:])[0]
    finally:
        terminal.close()


def percentile(values, p):
    values = sorted(values)
    if not values:
        return 0.0
    return values[max(0, min(len(values) - 1, int(round(p * (len(values) - 1)))))]


def stats(values, digits):
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), digits),
        "p50": round(percentile(values, 0.5), digits),
        "p95": round(percentile(values, 0.95), digits),
    }


def weighted_stats(pairs):
    pairs = sorted((value, weight) for value, weight in pairs if weight > 0)
    total = sum(weight for _, weight in pairs)
    if not pairs or total <= 0:
        return {"n": 0, "tokens": 0}

    def quantile(p):
        target = p * total
        seen = 0
        for value, weight in pairs:
            seen += weight
            if seen >= target:
                return value
        return pairs[-1][0]

    return {
        "n": len(pairs),
        "tokens": total,
        "mean": round(sum(value * weight for value, weight in pairs) / total, 2),
        "p50": round(quantile(0.5), 2),
        "p95": round(quantile(0.95), 2),
    }


ANOMALY_WINDOW_DAYS = 7
ANOMALY_MIN_SAMPLES = 20
# Columns of one row emitted by cloud-request-log-aggregate.py:
# [gateway, first_token, answer_first, react, workswarm].
REQUEST_SAMPLE_FIELDS = (
    ("gateway", 0),
    ("first_token", 1),
    ("answer_first", 2),
    ("react", 3),
)
ARRAY_METRICS = ("ttft", "tpot", "tool")
COHORT_PHASES = ("gateway", "first_token", "react", "answer_tail", "workswarm", "overhead")
# The slow tail is a share of the day's requests. Only REQUEST_DETAIL_LIMIT
# rows are published; they exist to locate a request, not to mirror the log.
SLOW_TAIL_RATIO = 0.05
REQUEST_DETAIL_LIMIT = 50
REQUEST_ID_INDEX = 5
TIMEOUT_RID_LIMIT = 200


def number(value):
    """A finite float, or None for anything that cannot be compared."""
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def request_values(day, index):
    values = []
    for sample in day.get("samples") or []:
        if not isinstance(sample, (list, tuple)) or len(sample) <= index:
            continue
        value = number(sample[index])
        if value is not None:
            values.append(value)
    return values


def array_values(day, name):
    # A pooled row keeps the per-call arrays under "_raw" for the anomaly
    # baseline: the public fields of the same name are already summaries.
    source = day.get("_raw") or {}
    values = []
    for item in source.get(name, day.get(name)) or []:
        raw = item[0] if name == "tpot" and isinstance(item, (list, tuple)) and item else item
        value = number(raw)
        if value is not None:
            values.append(value)
    return values


def summary_values(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), 1),
        "p50": round(percentile(values, 0.5), 1),
    }


def metric_anomaly(values, baseline):
    """Count samples above the baseline P95 for one metric.

    The threshold comes from earlier complete days, not from the day being
    analysed: using the same day's P95 would mechanically label about 5% of
    its samples as anomalous.
    """
    values = [value for value in values if value is not None]
    baseline = [value for value in baseline if value is not None]
    record = {"n": len(values), "baseline_n": len(baseline)}
    if len(baseline) < ANOMALY_MIN_SAMPLES:
        record["insufficient_baseline"] = True
        return record
    threshold = percentile(baseline, 0.95)
    record["baseline_p95"] = round(threshold, 1)
    if not values:
        # No sample today: a 0% outlier rate would claim the metric was clean
        # rather than unmeasured.
        record.update({"outliers": None, "rate": None})
        return record
    outliers = sum(1 for value in values if value > threshold)
    record.update({
        "outliers": outliers,
        "rate": round(outliers / len(values), 4),
    })
    return record


def request_rows(day):
    """One row per request with its phase breakdown and opaque identifier.

    Only timing values and request ids are kept; message content never reaches
    this collector.
    """
    rows = []
    for sample in day.get("samples") or []:
        if not isinstance(sample, (list, tuple)) or len(sample) < 5:
            continue
        gateway = number(sample[0])
        if gateway is None:
            continue
        rid = sample[REQUEST_ID_INDEX] if len(sample) > REQUEST_ID_INDEX else None
        row = {
            "request_id": rid if isinstance(rid, str) and rid else None,
            "gateway": gateway,
            "first_token": number(sample[1]),
            "answer_first": number(sample[2]),
            "react": number(sample[3]),
            "workswarm": number(sample[4]),
        }
        # Only the four fixed fields from the in-environment allowlist may
        # survive into public slow-request rows; never copy raw history.
        tools = sample[6] if len(sample) > 6 and isinstance(sample[6], list) else []
        allowed_names = {"bash", "grep", "read_file", "tool_search", "tool"}
        cleaned = []
        for item in tools[:200]:
            if not isinstance(item, dict):
                continue
            start, duration = number(item.get("start_ms")), number(item.get("duration_ms"))
            if start is None or duration is None or start < 0 or duration < 0 or start + duration > gateway:
                continue
            cleaned.append({"name": item.get("name") if item.get("name") in allowed_names else "tool",
                            "start_ms": start, "duration_ms": duration,
                            "status": "error" if item.get("status") == "error" else "ok"})
        if cleaned:
            row["tool_events"] = cleaned
        for key in ("first_token", "answer_first", "react", "workswarm"):
            # A phase cannot be longer than its request; out-of-order markers
            # are missing data, not a huge phase.
            if row[key] is not None and row[key] > gateway:
                row[key] = None
        rows.append(row)
    return rows


def latency_summary(rows):
    """Maximum latency and the slowest 5% of requests, with identifiers."""
    ordered = sorted(rows, key=lambda row: (row["gateway"], row["request_id"] or ""), reverse=True)
    if not ordered:
        return {"n": 0, "max_ms": None, "slow_count": 0, "slow_threshold_ms": None, "slow_requests": []}
    slow_count = math.ceil(len(ordered) * SLOW_TAIL_RATIO)
    slow = ordered[:slow_count]
    return {
        "n": len(ordered),
        "max_ms": round(ordered[0]["gateway"], 1),
        "max_request": ordered[0],
        "slow_count": slow_count,
        "slow_threshold_ms": round(slow[-1]["gateway"], 1),
        "slow_requests": slow[:REQUEST_DETAIL_LIMIT],
    }


def timeout_summary(day, rows):
    """Timed-out request count and the request ids that can be looked up."""
    categories = day.get("error_categories") if isinstance(day.get("error_categories"), dict) else {}
    count = categories.get("timeout") if isinstance(categories.get("timeout"), int) else 0
    by_id = {row["request_id"]: row for row in rows if row.get("request_id")}
    items = []
    for rid in day.get("timeout_rids") or []:
        if not isinstance(rid, str) or not rid:
            continue
        items.append(by_id.get(rid) or {"request_id": rid})
    items.sort(key=lambda row: row.get("gateway") if number(row.get("gateway")) is not None else -1, reverse=True)
    return {
        "count": count,
        "records": len(items),
        "requests": items[:REQUEST_DETAIL_LIMIT],
    }


def cohort_analysis(day, threshold):
    """Compare the phase profile of slow requests with the rest of the day.

    A request is in the outlier cohort when its gateway duration exceeds the
    gateway baseline P95. first_token + react + answer_tail partitions that
    duration, so the phase with the largest mean increase points at what
    dragged the slow requests out.
    """
    outlier_rows = []
    normal_rows = []
    for sample in day.get("samples") or []:
        if not isinstance(sample, (list, tuple)) or len(sample) < 5:
            continue
        gateway = number(sample[0])
        if gateway is None:
            continue
        row = {
            "gateway": gateway,
            "first_token": number(sample[1]),
            "answer_first": number(sample[2]),
            "react": number(sample[3]),
            "workswarm": number(sample[4]),
        }
        # A phase cannot be longer than the request that contains it. Treat
        # out-of-order log markers as missing rather than as a huge outlier.
        for key in ("first_token", "answer_first", "react", "workswarm"):
            if row[key] is not None and row[key] > gateway:
                row[key] = None
        if row["answer_first"] is not None and 0 <= row["answer_first"] <= gateway:
            row["answer_tail"] = gateway - row["answer_first"]
        if row["workswarm"] is not None and 0 <= row["workswarm"] <= gateway:
            row["overhead"] = gateway - row["workswarm"]
        (outlier_rows if gateway > threshold else normal_rows).append(row)

    phases = {}
    for phase in COHORT_PHASES:
        phases[phase] = {
            "outlier": summary_values(row.get(phase) for row in outlier_rows),
            "normal": summary_values(row.get(phase) for row in normal_rows),
        }

    driver = None
    driver_delta = None
    for phase in ("first_token", "react", "answer_tail"):
        outlier = phases[phase]["outlier"]
        normal = phases[phase]["normal"]
        if not outlier.get("n") or not normal.get("n"):
            continue
        # A handful of outlier requests makes a median jumpy (two samples put
        # the median on the lower one); the mean is the stabler driver signal.
        delta = outlier["mean"] - normal["mean"]
        if driver_delta is None or delta > driver_delta:
            driver, driver_delta = phase, delta
    if driver_delta is not None and driver_delta <= 0:
        driver, driver_delta = None, None

    return {
        "outliers": len(outlier_rows),
        "normal": len(normal_rows),
        "phases": phases,
        "driver": driver,
        "driver_delta_ms": round(driver_delta, 1) if driver_delta is not None else None,
    }


def annotate_anomalies(days):
    """Attach a per-day outlier summary to every day in place.

    Only aggregate counts and cohort means are attached; the per-request
    rows stay in the collector's memory and are never written to the board.
    """
    ordered = sorted(days, key=lambda day: day["date"])
    complete = [day for day in ordered if not day.get("partial")]
    for day in ordered:
        rows = request_rows(day)
        # Maximum latency and the slow/timeout request tables do not need a
        # baseline; they stay available on the first collected day too.
        latency = latency_summary(rows)
        timeouts = timeout_summary(day, rows)
        categories = day.get("error_categories") if isinstance(day.get("error_categories"), dict) else {}
        refs = [ref for ref in complete if ref["date"] < day["date"]][-ANOMALY_WINDOW_DAYS:]
        if not refs:
            day["anomaly"] = {
                "available": False,
                "reason": "no_complete_baseline",
                "latency": latency,
                "timeouts": timeouts,
                "error_categories": categories,
            }
            continue

        metrics = {}
        for name, index in REQUEST_SAMPLE_FIELDS:
            baseline = []
            for ref in refs:
                baseline.extend(request_values(ref, index))
            metrics[name] = metric_anomaly(request_values(day, index), baseline)
        for name in ARRAY_METRICS:
            baseline = []
            for ref in refs:
                baseline.extend(array_values(ref, name))
            metrics[name] = metric_anomaly(array_values(day, name), baseline)

        record = {
            "available": True,
            "baseline": {
                "from": refs[0]["date"],
                "to": refs[-1]["date"],
                "days": len(refs),
                "min_samples": ANOMALY_MIN_SAMPLES,
            },
            "metrics": metrics,
            "latency": latency,
            "timeouts": timeouts,
            "error_categories": categories,
        }
        gateway = metrics["gateway"]
        if gateway.get("baseline_p95") is not None:
            record["cohort"] = cohort_analysis(day, gateway["baseline_p95"])
        day["anomaly"] = record
    return days


def pool(results):
    """Pool the per-environment samples of every day, then summarise."""
    days = {}
    noise = 0
    started_late = set()
    for result in results:
        noise += result.get("noise") or 0
        first_ts = result.get("first_ts")
        if first_ts:
            stamp_day = datetime.datetime.fromtimestamp(first_ts, CST).strftime("%Y-%m-%d")
            day_start = datetime.datetime.strptime(stamp_day, "%Y-%m-%d").replace(tzinfo=CST).timestamp()
            if first_ts - day_start > 3600:
                started_late.add(stamp_day)
        for day in result.get("days", []):
            target = days.setdefault(day["date"], {
                "requests": set(), "gateway": [], "workswarm": [], "overhead": [],
                "ttft": [], "tpot": [], "first_token": [], "answer_first": [], "react": [],
                "tool": [], "tool_failed": 0,
                "samples": [],
                "error_categories": {}, "timeout_rids": set(),
                # One-way network metrics arrive with a later gateway release;
                # the keys are carried through now so the cards light up as
                # soon as an aggregator version emits them.
                "uplink_send": [], "downlink_first_text": [], "downlink_terminal": [],
                "input": 0, "output": 0, "total": 0, "success": 0, "failed": 0,
            })
            target["requests"].update(day.get("rids") or [])
            target["gateway"].extend(day.get("gateway") or [])
            target["workswarm"].extend(day.get("workswarm") or [])
            target["overhead"].extend(day.get("overhead") or [])
            target["ttft"].extend(day.get("ttft") or [])
            target["tpot"].extend(day.get("tpot") or [])
            target["first_token"].extend(day.get("first_token") or [])
            target["answer_first"].extend(day.get("answer_first") or [])
            target["react"].extend(day.get("react") or [])
            target["tool"].extend(day.get("tool") or [])
            target["tool_failed"] += day.get("tool_failed") or 0
            target["samples"].extend(day.get("samples") or [])
            for category, count in (day.get("error_categories") or {}).items():
                if isinstance(count, int) and count > 0:
                    target["error_categories"][category] = target["error_categories"].get(category, 0) + count
            target["timeout_rids"].update(
                rid for rid in day.get("timeout_rids") or [] if isinstance(rid, str) and rid)
            for key in ("uplink_send", "downlink_first_text", "downlink_terminal"):
                target[key].extend(day.get(key) or [])
            tokens = day.get("tokens") or {}
            target["input"] += tokens.get("input", 0)
            target["output"] += tokens.get("output", 0)
            target["total"] += tokens.get("total", 0)
            calls = day.get("calls") or {}
            target["success"] += calls.get("success", 0)
            target["failed"] += calls.get("failed", 0)

    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    out = []
    for name in sorted(days):
        rec = days[name]
        calls_total = rec["success"] + rec["failed"]
        out.append({
            "date": name,
            "partial": name == today or name in started_late,
            "n": len(rec["requests"]),
            "gateway": stats(rec["gateway"], 1),
            "workswarm": stats(rec["workswarm"], 1),
            "overhead": stats(rec["overhead"], 1),
            "ttft": stats(rec["ttft"], 2),
            "tpot": weighted_stats(rec["tpot"]),
            # Gateway dispatch -> first chunk carrying visible text. The gateway
            # logs the head of the stream and the first chunk of every event
            # type; logs written before that change only carry the first three
            # chunks, so older days can have fewer samples than "n".
            "first_token": stats(rec["first_token"], 1),
            # Request scope, but two different end markers: answer_first stops
            # at the first answer-text chunk (chat.delta), react covers only the
            # thinking/tool phase between the first text chunk and that answer.
            "answer_first": stats(rec["answer_first"], 1),
            "react": stats(rec["react"], 1),
            "error_categories": dict(sorted(rec["error_categories"].items(),
                                             key=lambda item: (-item[1], item[0]))),
            "timeout_rids": sorted(rec["timeout_rids"])[:TIMEOUT_RID_LIMIT],
            # Per-request rows are used by annotate_anomalies below and removed
            # before the document is returned.
            "samples": rec["samples"],
            # The summary above replaces these arrays in the public row; the
            # outlier baseline still needs the individual values, so they ride
            # along privately until annotate_anomalies has run.
            "_raw": {"ttft": rec["ttft"], "tpot": rec["tpot"], "tool": rec["tool"]},
            # One sample per finished tool call, timed by the product adapter.
            # "n" here counts tool calls, not requests.
            "tool": stats(rec["tool"], 1),
            "tool_failed": rec["tool_failed"],
            # Client-observed one-way network latency. The gateway release that
            # emits these markers is not deployed yet, so the groups are empty
            # today and carry n=0.
            "uplink_send": stats(rec["uplink_send"], 1),
            "downlink_first_text": stats(rec["downlink_first_text"], 1),
            "downlink_terminal": stats(rec["downlink_terminal"], 1),
            "tokens": {"input": rec["input"], "output": rec["output"], "total": rec["total"]},
            "calls": {"total": calls_total, "success": rec["success"], "failed": rec["failed"]},
            "failure_rate": round(rec["failed"] / calls_total, 4) if calls_total else 0.0,
        })
    annotate_anomalies(out)
    for day in out:
        day.pop("samples", None)
        day.pop("_raw", None)
    return {"days": out, "noise": noise}


def merge(previous, fresh):
    days = {day["date"]: dict(day) for day in previous.get("days", [])}
    # Legacy retained days have no trustworthy observation timestamp. Keep it
    # unknown instead of assigning a newer document generation time.
    observed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    for day in fresh["days"]:
        if not day["n"] and not day["calls"]["total"] and not day["tool"]["n"]:
            continue
        old = days.get(day["date"])
        # A restarted environment can serve fewer requests than the published
        # day; only accept the fresh day when it is at least as complete.
        if old is not None and (
            day["n"] < old.get("n", 0)
            or day["calls"]["total"] < (old.get("calls") or {}).get("total", 0)
            or day["tool"]["n"] < (old.get("tool") or {}).get("n", 0)
        ):
            continue
        if old is not None and day["date"] != today and old.get("partial") is False:
            day["partial"] = False
        day["observed_at"] = observed_at
        days[day["date"]] = day
    ordered = [days[key] for key in sorted(days)]
    # The per-request rows and raw arrays are an internal collection detail;
    # never let one survive into the published document, even if an old
    # snapshot had one.
    for day in ordered:
        day.pop("samples", None)
        day.pop("_raw", None)
    merged = dict(previous)
    merged["days"] = ordered
    merged["generated_at"] = observed_at
    defs = dict(previous.get("definitions") or {})
    defs["first_token"] = "请求口径：网关受理 → 首个携带文本的 chunk（chat.delta / chat.reasoning）"
    defs["answer_first"] = "请求口径：网关受理 → 首个答案文本 chunk（chat.delta）"
    defs["react"] = "请求口径：首个文本 chunk → 首个答案文本 chunk（思考与工具阶段）"
    defs["tool"] = "工具口径：chat.tool_call → chat.tool_result（服务端适配器记录，含下发、执行与结果返回；不含端侧）"
    defs["uplink_send"] = "端侧发送口径：客户端 send → 网关收到该帧（单程，不含服务端处理）"
    defs["downlink_first_text"] = "端侧接收口径：网关发出首个文本帧 → 客户端 recv 返回（单程）"
    defs["downlink_terminal"] = "端侧接收口径：网关发出终态帧 → 客户端 recv 返回（单程）"
    defs["anomaly"] = "基线 P95 = 该日之前最近 7 个完整日的合并样本 P95（每项至少 20 个样本）；异常 = 当日样本严格大于基线 P95；异常请求 = 请求耗时超过基线 P95 的请求"
    defs["timeout"] = "llm_call_error 中含 timeout / timed out / deadline exceeded 的失败调用"
    defs["slow_requests"] = "按请求耗时排序的最慢 5% 请求；公开近 7 天内的 request_id、各阶段耗时与状态，用于在网关定位单条请求"
    merged["definitions"] = defs
    merged["anomaly_version"] = 2
    scope = dict(previous.get("metric_scope") or {})
    scope["valid_calls"] = sum(day.get("calls", {}).get("success", 0) for day in ordered)
    if "noise" in fresh:
        scope["ignored_noise_lines"] = fresh["noise"]
        notes = scope.get("note") or "TTFT/TPOT 仅取 agent_adapter 的 llm_usage 事件"
        scope["note"] = f"{notes.split('；')[0]}；另有 {fresh['noise']} 行含该关键词的工具输出文本，非 usage 记录，已排除"
    merged["metric_scope"] = scope
    return merged


def collect(until=""):
    with open(AGGREGATOR, encoding="utf-8") as fh:
        script = fh.read()
    results = []
    failed = 0
    items = bindings()
    for index, item in enumerate(items, 1):
        # A binding can outlive its environment (deleted generation); the
        # console refuses a terminal on it, so there is nothing left to read.
        state = (item.get("environments") or {}).get("current") or {}
        if str(state.get("status") or "") == "not_found":
            print(f"environment {index}/{len(items)} skipped: {state.get('environment_id')} no longer exists", file=sys.stderr)
            continue
        try:
            results.append(collect_environment(item, script, until))
            if os.environ.get("ADX_DEBUG"):
                print(f"environment {index}/{len(items)} ok", file=sys.stderr)
        except Exception as error:
            failed += 1
            print(f"environment {index}/{len(items)} failed: {error}", file=sys.stderr)
    if failed:
        raise SystemExit(f"{failed} active environments failed; retain previous snapshot")
    if not results:
        raise SystemExit("every environment failed to report metrics")
    return pool(results)


def production_seed(previous):
    """Use only a production-labelled history; repair legacy test-0 seeding.

    The existing workflow has already fetched the data branch. Reading the
    separate production snapshot from that local ref avoids network access and
    refuses to carry test-0 rows into the production aggregate.
    """
    def valid(doc):
        return (isinstance(doc, dict) and doc.get("env") == "adx-jiuwen"
                and doc.get("schema") == "digitalmate/env-request-latency-daily@1")

    if valid(previous):
        return previous
    try:
        raw = subprocess.check_output(
            ["git", "show", "origin/digitalmate:site/data/production-request-latency-daily.json"],
            stderr=subprocess.DEVNULL,
        )
        candidate = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, ValueError):
        candidate = None
    if not valid(candidate):
        raise SystemExit("no production-labelled seed; refusing to mix environments")
    print("Using the production-labelled historical seed; ignoring incompatible snapshot", file=sys.stderr)
    return candidate


def main():
    if not TOKEN:
        raise SystemExit("ADX_TOKEN is not set")
    if len(sys.argv) >= 2 and sys.argv[1] == "--raw":
        fresh = collect(os.environ.get("DM_UNTIL", ""))
        json.dump(fresh, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
        return
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    output, previous_path = sys.argv[1], sys.argv[2]
    with open(previous_path, encoding="utf-8") as fh:
        previous = json.load(fh)
    previous = production_seed(previous)
    fresh = collect(os.environ.get("DM_UNTIL", ""))
    merged = merge(previous, fresh)
    with open(output, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main()
