"""The signed HTTP channel from a test server to the edge.

EVERY connection in this system is opened here, by the test server. The edge
never dials in, which is what lets the internal network keep every inbound port
closed. Four calls make up the whole protocol:

    enroll     once, at startup — presents the public key
    heartbeat  every 30s — "still here", and renews leases
    claim      long-poll — "any work for me?"
    result     "here is what happened"

All four are signed with this server's private key. The replies are signed with
the edge's, and verified here — because a test server that trusts whatever
answers its poll is a test server that will run whatever it is handed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from slack_runtests import identity

log = logging.getLogger(__name__)


class EdgeError(RuntimeError):
    """The edge could not be reached, or refused us."""


class EdgeClient:
    def __init__(self, base_url: str, runner_id: str, key, edge_fingerprint: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.runner_id = runner_id
        self._key = key
        self._edge_public_key = ""
        self._pinned_fingerprint = edge_fingerprint

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _send(self, path: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
        body = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            identity.HEADER_RUNNER_ID: self.runner_id,
            identity.HEADER_TIMESTAMP: ts,
            identity.HEADER_SIGNATURE: identity.sign(self._key, "POST", path, ts, body),
        }
        try:
            return httpx.post(f"{self.base_url}{path}", content=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise EdgeError(f"{path}: {exc}") from exc

    def _verify_reply(self, response: httpx.Response) -> None:
        """Refuse a reply this edge did not sign.

        Skipped only while the edge's key is genuinely unknown — i.e. the very
        first enrolment with no pinned fingerprint. Once a key is known, an
        unsigned or wrongly-signed reply is a hard failure rather than a
        warning: warning-and-continue would mean the check exists but changes
        nothing, which is worse than not having it.
        """
        if not self._edge_public_key:
            return
        ok = identity.verify_reply(
            self._edge_public_key,
            response.headers.get(identity.HEADER_EDGE_TIMESTAMP, ""),
            response.headers.get(identity.HEADER_EDGE_SIGNATURE, ""),
            response.content,
        )
        if not ok:
            raise EdgeError("the edge's reply was not correctly signed — refusing it")

    # ── the four calls ───────────────────────────────────────────────────────

    def enroll(self, public_key: str, labels: tuple[str, ...], token: str) -> dict[str, Any]:
        response = self._send(
            "/runner/enroll",
            {
                "runner_id": self.runner_id,
                "public_key": public_key,
                "labels": list(labels),
                "enroll_token": token,
            },
            timeout=15,
        )
        if response.status_code == 401:
            raise EdgeError(
                "the edge refused this test server. Either its public key is not "
                "pre-authorised, or the enrolment token is wrong, or this runner id "
                "is already enrolled with a different key."
            )
        if response.status_code != 200:
            raise EdgeError(f"enrol failed: HTTP {response.status_code}")

        data = response.json()
        edge_key = str(data.get("edge_public_key", ""))
        fingerprint = identity.fingerprint(edge_key)

        if self._pinned_fingerprint and fingerprint != self._pinned_fingerprint:
            raise EdgeError(
                f"edge key fingerprint {fingerprint} does not match the pinned "
                f"{self._pinned_fingerprint} — refusing to enrol"
            )
        self._edge_public_key = edge_key
        # Verify the enrolment reply itself now that we hold the key it claims
        # to be signed with. Doing it after assignment is deliberate: this is
        # the one reply that carries the key, so it can only be checked against
        # itself, and the pin above is what makes that meaningful.
        self._verify_reply(response)
        log.info("enrolled with edge %s (fingerprint %s)", self.base_url, fingerprint)
        return data

    def heartbeat(self) -> dict[str, Any]:
        response = self._send("/runner/heartbeat", {"runner_id": self.runner_id}, timeout=15)
        self._verify_reply(response)
        if response.status_code != 200:
            raise EdgeError(f"heartbeat refused: HTTP {response.status_code}")
        return response.json()

    def claim(self, poll_timeout: float) -> dict[str, Any] | None:
        """Long-poll. Returns a job, or None when the wait expired.

        The client timeout must comfortably exceed the edge's poll window or
        the test server times out its own long-poll every cycle and reads a
        working edge as a broken one.
        """
        response = self._send("/runner/jobs/claim", {}, timeout=poll_timeout + 15)
        self._verify_reply(response)
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise EdgeError(f"claim refused: HTTP {response.status_code}")
        return response.json()

    def started(self, job_id: str) -> None:
        response = self._send(f"/runner/jobs/{job_id}/started", {}, timeout=15)
        self._verify_reply(response)

    def result(self, job_id: str, **outcome: Any) -> None:
        response = self._send(f"/runner/jobs/{job_id}/result", outcome, timeout=30)
        self._verify_reply(response)
        if response.status_code != 200:
            raise EdgeError(f"result refused: HTTP {response.status_code}")
