#!/usr/bin/env python3
"""
Publishes ingestion's progress to the Chief of Staff jobs surface.

cos.agsci.com shows what background work is running on this host. Ingestion is
the first producer; scriven's processing is the second, which is why the record
this sends carries no repository, commit or file field — a job is a labelled
unit of work with a state, and cos does not know what the label means.

**Advisory, and fire-and-forget.** The commits index and the run record remain
authoritative; this is a projection for a human to look at. Every call here is
best-effort and swallows its own errors, because the alternative — ingestion
stalling on a dashboard — inverts which of the two matters. cos-api already
depends on code-smriti (`CODESMRITI_API_URL` in its compose); making ingestion
depend on cos in return would close a hard cycle around the ingestion loop, so
this direction has to stay soft.

Three things keep it off the hot path:

- **A throttle.** Progress updates collapse to one post per `min_interval`
  seconds per job. A 600-file repo would otherwise be 600 POSTs.
- **A circuit breaker, with a cooldown.** After `max_failures` consecutive
  failures the publisher stops trying for `cooldown_secs`, then tries once more.
  Without a breaker at all, cos being down would cost a connect timeout on every
  transition, all run. But the first version latched the breaker OPEN for the
  life of the process, which is too harsh for what actually happens: restarting
  cos-api takes ~15 seconds and would blind the dashboard for the rest of a
  multi-hour run. A transient outage should cost a gap, not the whole run.
- **A bounded timeout.** Ingestion is GPU-bound and a second or two is nothing;
  a hung POST is. Originally 5s, which was tuned against an idle box and proved
  too tight against a working one: on 2026-08-23 three consecutive publishes
  timed out 33 minutes into a run and tripped the breaker, and the box at that
  moment was running the chunker flat out with a docker build on top. The
  request itself is a KV upsert — when it takes seconds, the machine is busy,
  not the query.
"""

import time
from typing import Dict, List, Optional

import httpx
from loguru import logger

from config import WorkerConfig

config = WorkerConfig()

PRODUCER = "code-smriti"
QUEUE = "ingestion"


class JobPublisher:
    """Reports ingestion's queue and progress to cos. Never raises."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        token: Optional[str] = None,
        run_id: str = "",
        timeout: float = 15.0,
        min_interval: float = 3.0,
        max_failures: int = 3,
        cooldown_secs: float = 120.0,
    ):
        self.api_url = (api_url if api_url is not None else config.cos_api_url).rstrip("/")
        self.token = token if token is not None else config.cos_token
        self.run_id = run_id
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_failures = max_failures
        self.cooldown_secs = cooldown_secs

        self._failures = 0
        self._disabled = False          # permanent: no credentials configured
        self._muted_until = 0.0         # temporary: cos is not answering
        self._last_post: Dict[str, float] = {}
        self._started_at: Dict[str, str] = {}

        if not self.api_url or not self.token:
            # Not an error. A worker with no cos credentials simply does not
            # publish; ingestion is unaffected either way.
            logger.info("Job publishing disabled (no cos_api_url or cos_token)")
            self._disabled = True

    @property
    def enabled(self) -> bool:
        """Whether a post would be attempted right now."""
        return not self._disabled and time.monotonic() >= self._muted_until

    # --- lifecycle --------------------------------------------------------

    def publish_queue(self, job_ids: List[str]) -> None:
        """
        Declare the whole queue at the start of a run, as `queued`.

        Sent with `replace_queue`, which makes this batch authoritative and
        retires rows left behind by a previous run that was killed before it
        could finish them. Without that, a watchdog kill would leave a repo
        showing as `running` until its TTL expired, and the dashboard would
        report work that stopped hours ago as live.
        """
        jobs = [
            {"job_id": jid, "label": jid, "state": "queued"} for jid in sorted(job_ids)
        ]
        self._post(jobs, replace_queue=True)

    def start(self, job_id: str, mode: Optional[str] = None, detail: Optional[str] = None) -> None:
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._started_at[job_id] = started
        self._post([{
            "job_id": job_id,
            "label": job_id,
            "state": "running",
            "mode": mode,
            "detail": detail,
            "started_at": started,
        }], force=True)

    def update(
        self,
        job_id: str,
        mode: Optional[str] = None,
        detail: Optional[str] = None,
        done: int = 0,
        total: int = 0,
        in_flight: Optional[List[str]] = None,
        degraded: Optional[List[str]] = None,
        force: bool = False,
    ) -> None:
        """Progress for a running job. Throttled unless `force`."""
        self._post([{
            "job_id": job_id,
            "label": job_id,
            "state": "running",
            "mode": mode,
            "detail": detail,
            "started_at": self._started_at.get(job_id),
            "progress": {
                "done": done,
                "total": total,
                # Trimmed hard: this is a display sample, not a manifest, and the
                # API rejects more than 50 either way.
                "in_flight": (in_flight or [])[:10],
                "degraded": (degraded or [])[:10],
            },
        }], force=force)

    def finish(
        self,
        job_id: str,
        state: str = "done",
        detail: Optional[str] = None,
        error: Optional[str] = None,
        mode: Optional[str] = None,
        noop: bool = False,
    ) -> None:
        """Close a job out.

        `noop` means "completed, nothing to do". Most repos in a run skip, and
        without the flag they would fill the dashboard's finished list and bury
        the handful that actually did work.
        """
        self._post([{
            "job_id": job_id,
            "label": job_id,
            "state": state,
            "mode": mode,
            "detail": detail,
            "error": error,
            "noop": noop,
            "started_at": self._started_at.pop(job_id, None),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }], force=True)

    # --- transport --------------------------------------------------------

    def _post(self, jobs: List[dict], replace_queue: bool = False, force: bool = False) -> None:
        if self._disabled or not jobs:
            return

        # Muted after repeated failures. Expires on its own so a cos restart costs
        # a gap in the dashboard rather than the rest of the run.
        if time.monotonic() < self._muted_until:
            return

        # Throttle per job, not globally: two repos should not silence each
        # other, and a lifecycle transition (force) is never throttled.
        #
        # A forced post still stamps the window. It used to skip that, which made
        # every transition post twice in a row — start(), then the first
        # unthrottled progress update a moment later, because that update saw no
        # prior timestamp to measure against.
        key = jobs[0].get("job_id", "")
        now = time.monotonic()
        if not force and now - self._last_post.get(key, 0.0) < self.min_interval:
            return
        self._last_post[key] = now

        payload = {
            "producer": PRODUCER,
            "queue": QUEUE,
            "replace_queue": replace_queue,
            "jobs": [
                {**{k: v for k, v in job.items() if v is not None}, "run_id": self.run_id}
                for job in jobs
            ],
        }

        try:
            resp = httpx.post(
                f"{self.api_url}/api/cos/jobs",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            if self._failures:
                logger.info("Job publishing recovered")
            self._failures = 0
        except Exception as e:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._muted_until = time.monotonic() + self.cooldown_secs
                self._failures = 0
                logger.warning(
                    f"Job publishing muted for {self.cooldown_secs:.0f}s after "
                    f"{self.max_failures} consecutive failures (last: {e}). Ingestion "
                    f"is unaffected; the dashboard will be stale until it recovers."
                )
            else:
                logger.debug(f"Job publish failed ({self._failures}/{self.max_failures}): {e}")
