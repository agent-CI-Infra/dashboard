"""Collect with scoped App tokens exchanged for this Actions job's OIDC identity."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from collection_context import collection_context

ROOT = Path(__file__).resolve().parent
# GitCode sync records fetched from the bot for publish.py; not uploaded anywhere by itself.
SYNC_RECORDS = ROOT / ".tmp" / "gitcode-sync.json"
RUN_CHANGES = ROOT / ".tmp" / "run-changes.json"
RUN_RECEIPT = ROOT / ".tmp" / "run-changes-receipt.json"


class CredentialError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, bearer, body=None, method="GET", limit=65536):
    request = urllib.request.Request(url, method=method, headers={
        "Authorization": "Bearer " + bearer, "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": "dashboard-collector",
    }, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise CredentialError("credential response too large")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        failure = CredentialError("credential request failed")
        failure.status = error.code
        raise failure from None
    except Exception:
        # Never include response bodies, headers or request URLs in runner logs.
        raise CredentialError("credential request failed") from None


def mask(value):
    print("::add-mask::" + value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"), flush=True)


def oidc_identity(audience):
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CredentialError("invalid OIDC endpoint")
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "audience"]
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query + [("audience", audience)])))
    identity = request_json(url, os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
    token = identity.get("value") if isinstance(identity, dict) else None
    if not isinstance(token, str) or not token or len(token) > 16384 or any(c.isspace() for c in token):
        raise CredentialError("invalid OIDC response")
    mask(token)
    return token


def sync_records(url, identity):
    """GitCode sync records are optional evidence: a failure becomes a stale marker, never a failed collection."""
    try:
        value = request_json(url, identity, {}, "POST", limit=2_000_000)
        return value if isinstance(value, dict) else {"ok": False, "error": "invalid response"}
    except CredentialError as error:
        status = getattr(error, "status", None)
        return {"ok": False, "error": f"bot returned HTTP {status}" if isinstance(status, int) else "bot unreachable"}
    except Exception:
        return {"ok": False, "error": "bot unreachable"}


def changed_runs(url, identity):
    try:
        value = request_json(url, identity, {}, "POST")
        return value if isinstance(value, dict) else {"ok": False}
    except CredentialError:
        print("::warning::Rerun hints unavailable; pending IDs remain at the bot.", flush=True)
        return {"ok": False}


def acknowledge_runs(url, audience, receipt, requested, source):
    # The file only exists after publish.py committed both progress and public data.
    # Read a fresh GitHub identity: collection can outlive the original OIDC JWT.
    try:
        value = json.loads(receipt.read_text())
        from gsb.run_changes import valid_change
        rows = value.get("runs")
        if (value.get("source") != source or not isinstance(value.get("commit"), str)
                or not re.fullmatch('[0-9a-f]{40}', value['commit'])
                or not isinstance(rows, list) or not rows or len(rows) > 20
                or not all(valid_change(row) and row in requested.get("runs", []) for row in rows)):
            raise ValueError("invalid rerun receipt")
        reply = request_json(url, oidc_identity(audience), {"runs": rows, "commit": value["commit"]}, "POST")
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise ValueError("rerun acknowledgement unavailable")
    except Exception:
        # Publication succeeded; lost acknowledgements are safe to retry next collection.
        print("::warning::Rerun acknowledgement failed; pending IDs remain at the bot.", flush=True)


def main():
    grants = []
    stage = "configuration"
    identity = None
    try:
        context = collection_context(os.environ["GITHUB_REPOSITORY"], os.environ.get("SOURCE_REPOSITORY", ""), os.environ.get("COLLECTION_REQUEST_ID", ""))
        url = os.environ["SDBOT_TOKEN_BROKER_URL"]
        endpoint = urllib.parse.urlsplit(url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path != "/actions/token":
            raise CredentialError("invalid token broker URL")
        audience = os.environ["SDBOT_TOKEN_AUDIENCE"]
        if not audience:
            raise CredentialError("missing token audience")
        stage = "GitHub OIDC identity"
        identity = oidc_identity(audience)
        for purpose in ("source", "target"):
            stage = purpose + " token exchange"
            grant = request_json(url, identity, {"purpose": purpose}, "POST")
            token = grant.get("token") if isinstance(grant, dict) else None
            if not isinstance(token, str) or not token or len(token) > 4096 or any(c.isspace() for c in token):
                raise CredentialError("invalid App token response")
            mask(token)
            grants.append(token)  # Revoke even if the remaining metadata is invalid.
            expires = datetime.fromisoformat(grant.get("expires_at", "").replace("Z", "+00:00"))
            if grant.get("repository") != context[purpose] or grant.get("purpose") != purpose or (expires - datetime.now(timezone.utc)).total_seconds() < 600:
                raise CredentialError("App token scope or lifetime mismatch")
        # Same broker, same OIDC identity; the bot returns only sanitized records for this dashboard's source.
        stage = "GitCode sync records"
        SYNC_RECORDS.parent.mkdir(parents=True, exist_ok=True)
        SYNC_RECORDS.write_text(json.dumps(sync_records(urllib.parse.urlunsplit(endpoint._replace(path="/actions/gitcode-sync")), identity)), encoding="utf-8")
        stage = "rerun hints"
        requested = changed_runs(urllib.parse.urlunsplit(endpoint._replace(path="/actions/reruns")), identity)
        RUN_CHANGES.write_text(json.dumps(requested), encoding="utf-8")
        RUN_RECEIPT.unlink(missing_ok=True)
        # The collector receives neither OIDC credentials nor any App private key.
        child_env = {k: v for k, v in os.environ.items() if k not in {"ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL", "GH_TOKEN", "GITHUB_TOKEN", "GSB_PUBLISH_TOKEN"} and "PRIVATE_KEY" not in k}
        child_env.update(GITHUB_TOKEN=grants[0], GSB_PUBLISH_TOKEN=grants[1], GSB_GITCODE_SYNC_FILE=str(SYNC_RECORDS),
                         GSB_RUN_CHANGES_FILE=str(RUN_CHANGES), GSB_RUN_CHANGES_RECEIPT=str(RUN_RECEIPT))
        code = subprocess.run([sys.executable, str(ROOT / "publish.py"), "--repo", context["source"], "--publish-repo", context["target"], "--output", ".tmp/collected-site", "--incremental"], env=child_env, cwd=ROOT, check=False).returncode
        if code == 0 and RUN_RECEIPT.exists():
            acknowledge_runs(urllib.parse.urlunsplit(endpoint._replace(path="/actions/reruns/ack")), audience, RUN_RECEIPT, requested, context["source"])
        return code
    except Exception as error:
        status = getattr(error, "status", None)
        detail = f" (HTTP {status})" if isinstance(status, int) else ""
        if status == 403 and identity:
            # Only public workflow identity fields; never print the JWT or its audience URL.
            try:
                encoded = identity.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                public = {name: str(claims.get(name, ""))[:300] for name in (
                    "repository", "repository_id", "repository_owner_id", "ref", "ref_type",
                    "workflow_ref", "job_workflow_ref", "sub", "event_name")}
                print("OIDC workflow identity: " + json.dumps(public), flush=True)
            except Exception:
                pass
        print(f"::error::OIDC credential exchange failed at {stage}{detail}; check broker configuration and workflow identity.", flush=True)
        return 1
    finally:
        for token in grants:
            try:
                request_json("https://api.github.com/installation/token", token, method="DELETE")
            except CredentialError:
                print("::warning::Temporary App token revocation failed; automatic expiration still applies.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
