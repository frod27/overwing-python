"""Overwing Radar: agents acting together on a shared store, and one stolen login behaving like a machine. Radar recommends; it changes nothing on your systems."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any, Mapping, Sequence, Union

import httpx

from ._errors import OverwingError
from ._http import AsyncHTTP, SyncHTTP, compact, query, segment
from ._types import RadarDecoy, RadarIncident, RadarIngest

_STATUS = "/api/v1/swarm"
_EVENTS = "/api/v1/swarm/events"
_SURFACES = "/api/v1/swarm/surfaces"
_DECOYS = "/api/v1/swarm/decoys"
_INCIDENTS = "/api/v1/swarm/incidents"
_CONFIG = "/api/v1/swarm/config"
_AWS = "/api/v1/swarm/connectors/aws"
_NO_KEY = "Overwing API key missing. Every Radar call needs one: pass api_key= or set OVERWING_API_KEY. A key is free: Overwing.signup(). A key of scope \"swarm\" can send events, send CloudTrail records and report a response, and nothing else."

#: The most events one `send_events` call may carry. A batch is taken whole or not at all, so a longer one is yours to split.
MAX_EVENTS = 500

#: What `send_cloudtrail` takes: a CloudTrail log file ({"Records": [...]}), an EventBridge event, or a list of either.
CloudTrailBody = Union[Mapping[str, Any], Sequence[Any]]


def _invalid(field: str, why: str) -> OverwingError:
    return OverwingError(why, code="invalid_input", field=field, retryable=False)


def require_key(token: str | None) -> None:
    if token is None:
        raise OverwingError(_NO_KEY)


def events_body(events: Sequence[Mapping[str, Any]], drill_id: str | None) -> dict[str, Any]:
    if isinstance(events, (str, bytes, Mapping)) or not isinstance(events, Sequence):
        raise _invalid("events", "events must be a list of events: [{\"id\": ..., \"surface_id\": ..., \"artifact_id\": ..., \"actor_id\": ..., \"op\": ..., \"ts\": ...}]")
    if len(events) == 0:
        raise _invalid("events", "events is empty. Send at least one event.")
    if len(events) > MAX_EVENTS:
        raise _invalid("events", f"At most {MAX_EVENTS} events in one request; this one has {len(events)}. A batch is taken whole or not at all, so split it and send each part.")
    for i, e in enumerate(events):
        if not isinstance(e, Mapping):
            raise _invalid("events", f"events[{i}] must be a mapping, not a {type(e).__name__}")
    return compact({"events": [dict(e) for e in events], "drill_id": drill_id})


def surface_body(surface_id: str, kind: str | None, label: str | None) -> dict[str, Any]:
    return compact({"id": surface_id, "kind": kind, "label": label})


def mint_body(surface_id: str, kind: str | None, label: str | None) -> dict[str, Any]:
    return compact({"surface_id": surface_id, "kind": kind, "label": label})


def register_body(surface_id: str, artifact_id: str, content_hash: str, label: str | None) -> dict[str, Any]:
    return compact({"surface_id": surface_id, "artifact_id": artifact_id, "content_hash": content_hash, "label": label})


def response_body(action: str, status: str, target: str | None, detail: str | None, by: str | None) -> dict[str, Any]:
    return compact({"action": action, "status": status, "target": target, "detail": detail, "by": by})


def thresholds_body(changes: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, Mapping):
        raise _invalid("changes", "changes must be a mapping: {\"sweep\": {\"minArtifacts\": 40}}. An empty one restores every default.")
    return dict(changes)


def cloudtrail_body(body: CloudTrailBody) -> Any:
    if isinstance(body, Mapping):
        return dict(body)
    if isinstance(body, (str, bytes)) or not isinstance(body, Sequence):
        raise _invalid("body", "body must be the parsed JSON: a CloudTrail log file ({\"Records\": [...]}), an EventBridge event, or a list of either.")
    return list(body)


def decoy_path(decoy_id: str) -> str:
    return f"{_DECOYS}/{segment(decoy_id)}"


def incidents_path(status: str | None, limit: int | None) -> str:
    return _INCIDENTS + query({"status": status, "limit": limit})


def incident_path(incident_id: str) -> str:
    return f"{_INCIDENTS}/{segment(incident_id)}"


def close_path(incident_id: str) -> str:
    return f"{_INCIDENTS}/{segment(incident_id)}/close"


def responses_path(incident_id: str) -> str:
    return f"{_INCIDENTS}/{segment(incident_id)}/responses"


def verify_radar_webhook(secret: str, signature_header: str | None, raw_body: str | bytes, tolerance_seconds: float = 300, now: float | None = None) -> bool:
    """True when a webhook request was signed with your webhook secret and is recent. Anything else is False: do not act on it.

    Overwing sends `X-Overwing-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "t.body">`. Pass the header's
    value and the body exactly as it arrived, before any JSON parsing. A request whose `t` is more than
    `tolerance_seconds` from `now` (unix seconds; the clock when left out) is refused, so a captured request
    cannot be sent again later. Every Overwing webhook is signed this way, not only Radar's.

        if not verify_radar_webhook(secret, request.headers.get("X-Overwing-Signature"), request.body):
            return 401
    """
    if not secret or not isinstance(signature_header, str):
        return False
    stamp: str | None = None
    signature: str | None = None
    for part in signature_header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t" and re.fullmatch(r"[0-9]{1,15}", value):
            stamp = value
        elif name == "v1" and re.fullmatch(r"[0-9a-f]{64}", value):
            signature = value
    if stamp is None or signature is None:
        return False
    if abs((time.time() if now is None else now) - int(stamp)) > tolerance_seconds:
        return False
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    expected = hmac.new(secret.encode("utf-8"), stamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class Radar(SyncHTTP):
    """Synchronous Radar client.

    You report who touched which entry on a shared store: a cache, a queue, a bucket. Hashes and sizes,
    never contents. Radar looks for agents acting together and for one identity reading its way through
    everything, groups what it finds into incidents, and recommends what to do. It enforces nothing.
    Every call needs a key (`api_key=` or OVERWING_API_KEY).

        radar = Radar()
        radar.send_events([{"id": "evt-1", "surface_id": "scratch-bucket", "artifact_id": "notes/plan.md", "actor_id": "agent-17", "op": "read", "ts": "2026-11-20T12:00:00Z"}])
        for incident in radar.list_incidents(status="open"): incident.recommended_actions
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        require_key(self._token)
        return super()._request(method, path, **kwargs)

    def __enter__(self) -> "Radar":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def status(self) -> dict[str, Any]:
        """Events sent this month, the allowance, monitoring, what is on sale, and the limits of one request."""
        return self._request("GET", _STATUS)

    def send_events(self, events: Sequence[Mapping[str, Any]], drill_id: str | None = None) -> RadarIngest:
        """Report up to 500 events. Each needs `id`, `surface_id`, `artifact_id`, `actor_id`, `op` and `ts`.

        `op` is "read", "write", "list" or "delete"; `ts` is an ISO 8601 time or milliseconds since the epoch.
        Optional: `principal_id`, `content_hash`, `parent_artifact_hash`, `size_bytes`, `metadata`. There is no
        field for contents and a batch that adds one is refused. A batch is taken whole or not at all: one bad
        event raises OverwingError (status 400) naming it. An id sent again is counted once, so repeat a batch
        when no answer came back. `drill_id` is set by the fire drill runner.
        """
        return RadarIngest.from_dict(self._request("POST", _EVENTS, json=events_body(events, drill_id)))

    def list_surfaces(self) -> list[dict[str, Any]]:
        """The surfaces with events, each {"id", "kind", "label", "created_at", "last_24h": {"events", "actors", "last_event_at"}}."""
        return self._request("GET", _SURFACES)["surfaces"]

    def name_surface(self, surface_id: str, kind: str | None = None, label: str | None = None) -> dict[str, Any]:
        """Name a surface before its first event, or change its kind and label. Both are saved as given: one left out is cleared.

        `kind` is "cache", "queue", "bucket", "tickets", "memory" or "other".
        """
        return self._request("POST", _SURFACES, json=surface_body(surface_id, kind, label))

    def list_decoys(self) -> list[RadarDecoy]:
        """The decoys planted and retired."""
        return [RadarDecoy.from_dict(d) for d in self._request("GET", _DECOYS)["decoys"]]

    def mint_decoy(self, surface_id: str, kind: str | None = None, label: str | None = None) -> RadarDecoy:
        """Mint a decoy. Its `content` is in the answer once and is never stored.

        `kind` is "notes" (the default), "credentials", "export" or "config": write `content` to `artifact_id`
        on the surface and report that write like any other event, with the same `content_hash`.

        `kind="key"` mints a decoy key: `base_url`, `key`, and `content` holding both as two lines of an env
        file. Keep them where real keys are kept. Nothing there needs instrumenting: the first request that
        carries the key opens a critical incident.
        """
        return RadarDecoy.from_dict(self._request("POST", _DECOYS, json=mint_body(surface_id, kind, label)))

    def register_decoy(self, surface_id: str, artifact_id: str, content_hash: str, label: str | None = None) -> RadarDecoy:
        """Register an entry of your own as a decoy: where it lies and the hash of its content. Any touch of it then opens an incident."""
        return RadarDecoy.from_dict(self._request("POST", _DECOYS, json=register_body(surface_id, artifact_id, content_hash, label)))

    def retire_decoy(self, decoy_id: str) -> dict[str, Any]:
        """Retire a decoy. It stops opening incidents; what it already caught stays. Returns {"id", "retired": True}."""
        return self._request("DELETE", decoy_path(decoy_id))

    def list_incidents(self, status: str | None = None, limit: int | None = None) -> list[RadarIncident]:
        """Incidents, the most recently active first. `status` is "open" or "closed"; `limit` is 1 to 200 (50 when left out). A fire drill's incidents are not listed here."""
        return [RadarIncident.from_dict(i) for i in self._request("GET", incidents_path(status, limit))["incidents"]]

    def get_incident(self, incident_id: str) -> RadarIncident:
        """One incident, with its `evidence` and a `timeline` of the events the evidence names."""
        return RadarIncident.from_dict(self._request("GET", incident_path(incident_id)))

    def close_incident(self, incident_id: str) -> RadarIncident:
        """Close an incident that has been dealt with. New evidence later opens a new one."""
        return RadarIncident.from_dict(self._request("POST", close_path(incident_id)))

    def list_responses(self, incident_id: str) -> dict[str, Any]:
        """What your own responder reported doing about an incident: {"incident_id", "responses", "contained_in_seconds"}."""
        return self._request("GET", responses_path(incident_id))

    def report_response(self, incident_id: str, action: str, status: str, target: str | None = None, detail: str | None = None, by: str | None = None) -> dict[str, Any]:
        """Record one thing a responder did about an incident, or decided not to do. Overwing itself acts on nothing.

        `action` is "revoke_identity", "block_source", "rotate_secrets" or "quarantine_surface"; `status` is
        "done", "failed", "skipped" or "dry_run". `target` is who or what it was done to, `by` the responder's
        own name. An incident keeps 50 reports. Returns {"incident_id", "recorded", "responses", "contained_in_seconds"}.
        """
        return self._request("POST", responses_path(incident_id), json=response_body(action, status, target, detail, by))

    def get_thresholds(self) -> dict[str, Any]:
        """The detector thresholds: {"thresholds": in force, "changed": what this account changed, "defaults"}."""
        return self._request("GET", _CONFIG)

    def set_thresholds(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Replace what this account has changed. Send only the parts that differ from the defaults; {} restores them all.

            radar.set_thresholds({"convergence": {"minActors": 5}, "sweep": {"exemptActors": ["svc-backup"]}})
        """
        return self._request("PUT", _CONFIG, json=thresholds_body(changes))

    def send_cloudtrail(self, body: CloudTrailBody) -> dict[str, Any]:
        """Send what AWS CloudTrail saw: a log file ({"Records": [...]}) or an EventBridge event, as parsed JSON.

        S3, Secrets Manager and Parameter Store calls become events; every other record is skipped.
        Returns {"records", "accepted", "duplicates", "skipped"}. Up to 5,000 records a request.
        """
        return self._request("POST", _AWS, json=cloudtrail_body(body))


class AsyncRadar(AsyncHTTP):
    """Asynchronous Radar client. See `Radar`."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        require_key(self._token)
        return await super()._request(method, path, **kwargs)

    async def __aenter__(self) -> "AsyncRadar":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", _STATUS)

    async def send_events(self, events: Sequence[Mapping[str, Any]], drill_id: str | None = None) -> RadarIngest:
        return RadarIngest.from_dict(await self._request("POST", _EVENTS, json=events_body(events, drill_id)))

    async def list_surfaces(self) -> list[dict[str, Any]]:
        return (await self._request("GET", _SURFACES))["surfaces"]

    async def name_surface(self, surface_id: str, kind: str | None = None, label: str | None = None) -> dict[str, Any]:
        return await self._request("POST", _SURFACES, json=surface_body(surface_id, kind, label))

    async def list_decoys(self) -> list[RadarDecoy]:
        return [RadarDecoy.from_dict(d) for d in (await self._request("GET", _DECOYS))["decoys"]]

    async def mint_decoy(self, surface_id: str, kind: str | None = None, label: str | None = None) -> RadarDecoy:
        return RadarDecoy.from_dict(await self._request("POST", _DECOYS, json=mint_body(surface_id, kind, label)))

    async def register_decoy(self, surface_id: str, artifact_id: str, content_hash: str, label: str | None = None) -> RadarDecoy:
        return RadarDecoy.from_dict(await self._request("POST", _DECOYS, json=register_body(surface_id, artifact_id, content_hash, label)))

    async def retire_decoy(self, decoy_id: str) -> dict[str, Any]:
        return await self._request("DELETE", decoy_path(decoy_id))

    async def list_incidents(self, status: str | None = None, limit: int | None = None) -> list[RadarIncident]:
        return [RadarIncident.from_dict(i) for i in (await self._request("GET", incidents_path(status, limit)))["incidents"]]

    async def get_incident(self, incident_id: str) -> RadarIncident:
        return RadarIncident.from_dict(await self._request("GET", incident_path(incident_id)))

    async def close_incident(self, incident_id: str) -> RadarIncident:
        return RadarIncident.from_dict(await self._request("POST", close_path(incident_id)))

    async def list_responses(self, incident_id: str) -> dict[str, Any]:
        return await self._request("GET", responses_path(incident_id))

    async def report_response(self, incident_id: str, action: str, status: str, target: str | None = None, detail: str | None = None, by: str | None = None) -> dict[str, Any]:
        return await self._request("POST", responses_path(incident_id), json=response_body(action, status, target, detail, by))

    async def get_thresholds(self) -> dict[str, Any]:
        return await self._request("GET", _CONFIG)

    async def set_thresholds(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", _CONFIG, json=thresholds_body(changes))

    async def send_cloudtrail(self, body: CloudTrailBody) -> dict[str, Any]:
        return await self._request("POST", _AWS, json=cloudtrail_body(body))
