#!/usr/bin/env python3
"""
Admission control for ingestion's LLM calls.

Ingestion shares one Ollama host with interactive callers — cos-web's chat
(`general`) and its flow selector (`structured`), and scriven's extraction
(`structured`). **Ollama has no priority mechanism**: no request priority, no
queue reordering, no preemption. Slots are handed out FIFO per model. So
"deprioritize ingestion" is not something the server can be asked for; it can
only be implemented on this side, as *ingestion holds fewer slots*. This module
is the only place that behaviour can live.

Without it, ingestion runs `max_concurrent_files` (10) files concurrently
against `OLLAMA_NUM_PARALLEL` (4) slots, so it occupies every slot AND keeps six
requests queued inside the server. An interactive call then waits behind that
whole queue — measured shape: a cos flow-select that should take 0.6s landing
behind up to six ~12s chunker calls.

Two distinct wins, worth not conflating:

    outstanding | worst-case interactive wait | chunker throughput
    ------------|-----------------------------|-------------------
    10          | ~6 queued x ~12s ~= 70s     | 118.6 tok/s
     4          | first slot to free ~= 12s   | unchanged
     2          | ~0s                         | reduced

Capping at the slot count is free — it only stops ingestion stacking a queue
*inside* Ollama on top of the slots it already holds. Going below the slot count
is the actual reservation, and it is the one that costs throughput.

The GPU is saturated at ~118.6 tok/s aggregate, so throughput ingestion gives up
is *transferred* to whoever else is asking, not lost. The only real loss is idle
slots while nothing else wants them.

Two implementation notes:

1. The limit is read **at acquire time**, not fixed at construction. Flat-2 and
   an adaptive "4 when cos is cold, 2 when it is warm" are then the same code,
   and the choice is a config value rather than a rewrite. This is why it is not
   an `asyncio.Semaphore`, whose value is fixed when it is built.

2. Gates are per (model, event loop). `IncrementalUpdater` calls
   `asyncio.new_event_loop()` per repo, and asyncio's `_LoopBoundMixin` raises
   `RuntimeError: bound to a different event loop` if a primitive built on one
   loop is awaited on another. The httpx clients in llm_chunker/llm_enricher
   already rebuild on loop change for the same reason; this mirrors them.
"""

import asyncio
from typing import Callable, Dict, Optional

from loguru import logger


class LLMGate:
    """
    Bounds the number of ingestion requests in flight against one model.

    Not an asyncio.Semaphore: the limit is a callable consulted on every
    acquire, so it can change between acquisitions without rebuilding the gate
    or losing track of what is already in flight.
    """

    def __init__(self, model: str, limit: Callable[[], int]):
        self.model = model
        self._limit = limit
        self._in_flight = 0
        self._cond: Optional[asyncio.Condition] = None
        self._loop = None

    def _condition(self) -> asyncio.Condition:
        """Get the condition for the running loop, rebuilding if the loop changed."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._cond is None or self._loop is not current_loop:
            # A new loop means the previous loop's in-flight requests are gone
            # with it — their `release` will never run, so the counter has to be
            # reset or the gate leaks permits until it deadlocks.
            self._cond = asyncio.Condition()
            self._loop = current_loop
            self._in_flight = 0

        return self._cond

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def __aenter__(self):
        cond = self._condition()
        async with cond:
            # Re-read the limit on every wakeup, not just on entry, so a limit
            # that changed while this caller was parked is the one it obeys.
            # Floored at 1: a misconfigured 0 should throttle ingestion to
            # single-file, not deadlock it forever.
            while self._in_flight >= max(1, self._limit()):
                await cond.wait()
            self._in_flight += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        cond = self._condition()
        async with cond:
            self._in_flight -= 1
            # notify_all, not notify(1). With notify(1) a limit raised from 2 to
            # 4 would admit exactly one extra caller per release, so the gate
            # would take as many releases to widen as the raise was worth —
            # ingestion would crawl at the old limit while slots sat idle. Waking
            # everyone lets each re-read the current limit and take a slot if
            # there is one. The herd is bounded by max_concurrent_files (10).
            cond.notify_all()
        return False


_gates: Dict[str, LLMGate] = {}


def gate_for(model: str, limit: Callable[[], int]) -> LLMGate:
    """
    Get the process-wide gate for a model, creating it on first use.

    Keyed by model because each model has its own slot pool on the server —
    `general` and `structured` are separate runners and do not contend for slots
    with each other (they do contend for GPU bandwidth, which no client-side
    gate can arbitrate).
    """
    gate = _gates.get(model)
    if gate is None:
        gate = LLMGate(model, limit)
        _gates[model] = gate
        logger.info(f"LLM gate for {model!r}: max {limit()} concurrent request(s) from ingestion")
    return gate
