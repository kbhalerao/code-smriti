"""
Tests for the ingestion-side LLM admission gate.

The gate is the only place ingestion can be deprioritised relative to cos and
scriven — ollama hands out slots FIFO with no priority, so a caller cannot be
moved up the queue and ingestion can only decline to fill it. Two properties
carry that: the cap actually holds under concurrency, and the limit is read at
acquire time so it can change without rebuilding the gate.

The loop-rebuild test is the one that matters operationally.
`IncrementalUpdater` calls `asyncio.new_event_loop()` once per repo, and
asyncio's `_LoopBoundMixin` raises "bound to a different event loop" for a
primitive built on one loop and awaited on another. A gate that did not handle
that would fail on the second repo of every run.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_gate import LLMGate


class TestConcurrencyCap:
    def test_never_exceeds_limit(self):
        gate = LLMGate("test", lambda: 2)
        peak = 0
        live = 0

        async def worker():
            nonlocal peak, live
            async with gate:
                live += 1
                peak = max(peak, live)
                await asyncio.sleep(0.01)
                live -= 1

        async def scenario():
            await asyncio.gather(*(worker() for _ in range(10)))

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert peak == 2

    def test_all_callers_complete(self):
        """A cap must serialise work, not drop it."""
        gate = LLMGate("test", lambda: 2)
        done = []

        async def worker(i):
            async with gate:
                done.append(i)

        async def scenario():
            await asyncio.gather(*(worker(i) for i in range(10)))

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert sorted(done) == list(range(10))

    def test_permit_released_when_body_raises(self):
        """A failing call must not leak its permit, or the gate deadlocks."""
        gate = LLMGate("test", lambda: 1)

        async def scenario():
            for _ in range(3):
                try:
                    async with gate:
                        raise RuntimeError("call failed")
                except RuntimeError:
                    pass
            assert gate.in_flight == 0

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))

    def test_limit_of_zero_is_floored_to_one(self):
        """A misconfigured 0 must stall ingestion, not deadlock it forever."""
        gate = LLMGate("test", lambda: 0)

        async def scenario():
            async with gate:
                return "ran"

        assert asyncio.run(asyncio.wait_for(scenario(), timeout=5)) == "ran"


class TestLimitReadAtAcquireTime:
    """
    This is why the gate is not an asyncio.Semaphore, whose value is fixed at
    construction. Flat-2 and an adaptive "4 when cos is cold, 2 when warm" have
    to be the same code, with only the value differing.
    """

    def test_raising_the_limit_admits_more_callers(self):
        limit = 1
        gate = LLMGate("test", lambda: limit)
        peak = 0
        live = 0

        async def worker():
            nonlocal peak, live
            async with gate:
                live += 1
                peak = max(peak, live)
                await asyncio.sleep(0.05)
                live -= 1

        async def scenario():
            nonlocal limit
            tasks = [asyncio.create_task(worker()) for _ in range(4)]
            await asyncio.sleep(0.01)
            assert peak == 1, "should be capped at the original limit"
            limit = 3
            await asyncio.gather(*tasks)

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert peak > 1, "a raised limit should release parked callers"

    def test_lowering_the_limit_takes_effect_on_next_acquire(self):
        limit = 3
        gate = LLMGate("test", lambda: limit)
        peak_after = 0
        live = 0

        async def worker():
            nonlocal peak_after, live
            async with gate:
                live += 1
                peak_after = max(peak_after, live)
                await asyncio.sleep(0.01)
                live -= 1

        async def scenario():
            nonlocal limit, peak_after
            await asyncio.gather(*(worker() for _ in range(3)))
            limit = 1
            peak_after = 0
            await asyncio.gather(*(worker() for _ in range(4)))

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        assert peak_after == 1


class TestEventLoopRebuild:
    """
    IncrementalUpdater builds a fresh event loop per repo. A gate that cached a
    Condition from the first loop would raise "bound to a different event loop"
    on the second repo of every run.
    """

    def test_gate_survives_a_new_event_loop(self):
        gate = LLMGate("test", lambda: 2)

        async def scenario():
            async with gate:
                return "ok"

        # Three separate loops, as three consecutive repos would produce.
        for _ in range(3):
            assert asyncio.run(asyncio.wait_for(scenario(), timeout=5)) == "ok"

    def test_in_flight_resets_across_loops(self):
        """
        Permits held when a loop dies are never released — their `__aexit__`
        does not run. Carrying that count into the next loop would leak permits
        until the gate wedged, which on a 2-permit gate takes two repos.
        """
        gate = LLMGate("test", lambda: 2)

        async def leak():
            # Enter without leaving, then let the loop be torn down.
            await gate.__aenter__()

        asyncio.run(asyncio.wait_for(leak(), timeout=5))

        async def scenario():
            peak = 0
            live = 0

            async def worker():
                nonlocal peak, live
                async with gate:
                    live += 1
                    peak = max(peak, live)
                    await asyncio.sleep(0.01)
                    live -= 1

            await asyncio.gather(*(worker() for _ in range(4)))
            return peak

        assert asyncio.run(asyncio.wait_for(scenario(), timeout=5)) == 2
