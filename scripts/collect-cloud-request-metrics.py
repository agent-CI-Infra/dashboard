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
  ADX_BASE   console base URL (default https://onlineswarm.openjiuwen.com:31470)
  DM_UNTIL   optional ISO-8601 cut-off passed to the in-env aggregator
"""
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ADX_BASE", "https://onlineswarm.openjiuwen.com:31470").rstrip("/")
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
    query = urllib.parse.urlencode(
        {"template": TEMPLATE, "reference": REFERENCE, "kind": KIND, "page_size": 50}
    )
    items = call(f"/api/bindings?{query}").get("items", [])
    if not items:
        raise SystemExit("no ADX bindings for the DigitalMate environment")
    return items


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
                "ttft": [], "tpot": [],
                "input": 0, "output": 0, "total": 0, "success": 0, "failed": 0,
            })
            target["requests"].update(day.get("rids") or [])
            target["gateway"].extend(day.get("gateway") or [])
            target["workswarm"].extend(day.get("workswarm") or [])
            target["overhead"].extend(day.get("overhead") or [])
            target["ttft"].extend(day.get("ttft") or [])
            target["tpot"].extend(day.get("tpot") or [])
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
            "tokens": {"input": rec["input"], "output": rec["output"], "total": rec["total"]},
            "calls": {"total": calls_total, "success": rec["success"], "failed": rec["failed"]},
            "failure_rate": round(rec["failed"] / calls_total, 4) if calls_total else 0.0,
        })
    return {"days": out, "noise": noise}


def merge(previous, fresh):
    days = {day["date"]: day for day in previous.get("days", [])}
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    for day in fresh["days"]:
        if not day["n"] and not day["calls"]["total"]:
            continue
        old = days.get(day["date"])
        # A restarted environment can serve fewer requests than the published
        # day; only accept the fresh day when it is at least as complete.
        if old is not None and (
            day["n"] < old.get("n", 0)
            or day["calls"]["total"] < (old.get("calls") or {}).get("total", 0)
        ):
            continue
        if old is not None and day["date"] != today and old.get("partial") is False:
            day["partial"] = False
        days[day["date"]] = day
    ordered = [days[key] for key in sorted(days)]
    merged = dict(previous)
    merged["days"] = ordered
    merged["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        except Exception as error:  # one broken environment must not sink the run
            print(f"environment {index}/{len(items)} failed: {error}", file=sys.stderr)
    if not results:
        raise SystemExit("every environment failed to report metrics")
    return pool(results)


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
    fresh = collect(os.environ.get("DM_UNTIL", ""))
    with open(previous_path, encoding="utf-8") as fh:
        previous = json.load(fh)
    merged = merge(previous, fresh)
    with open(output, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main()
