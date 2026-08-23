"""
Publishing progress to the dashboard must never be able to harm ingestion.

cos-api already depends on code-smriti; if ingestion depended on cos in return,
a dashboard outage would become an ingestion outage — a hard cycle around the
run loop. So the publisher is fire-and-forget in the strong sense: it swallows
every error, throttles itself off the hot path, and gives up entirely rather
than paying a connect timeout per file for a whole run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import job_publisher
from job_publisher import JobPublisher


class FakePost:
    """Stands in for httpx.post. Records payloads; can be told to fail."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        if self.fail:
            raise RuntimeError("cos unreachable")

        class R:
            @staticmethod
            def raise_for_status():
                return None

        return R()


def _publisher(monkeypatch, fail=False, **kwargs) -> tuple[JobPublisher, FakePost]:
    post = FakePost(fail=fail)
    monkeypatch.setattr(job_publisher.httpx, "post", post)
    p = JobPublisher(api_url="http://cos.test", token="tok", run_id="r1", **kwargs)
    return p, post


class TestNeverHarmsIngestion:
    def test_a_failing_endpoint_does_not_raise(self, monkeypatch):
        pub, _ = _publisher(monkeypatch, fail=True)
        pub.publish_queue(["a/b"])
        pub.start("a/b")
        pub.update("a/b", done=1, total=2, force=True)
        pub.finish("a/b")  # would have raised if errors escaped

    def test_it_gives_up_after_repeated_failures(self, monkeypatch):
        """
        Without a breaker, cos being down costs a connect timeout on every
        transition for the whole run — on a GPU-bound job that is the one kind
        of overhead that actually shows up.
        """
        pub, post = _publisher(monkeypatch, fail=True, max_failures=3)
        for i in range(10):
            pub.finish(f"repo/{i}")
        assert len(post.calls) == 3, "should stop trying after the breaker trips"
        assert not pub.enabled

    def test_missing_credentials_disable_it_quietly(self, monkeypatch):
        post = FakePost()
        monkeypatch.setattr(job_publisher.httpx, "post", post)
        pub = JobPublisher(api_url="", token="")
        pub.start("a/b")
        assert post.calls == []
        assert not pub.enabled


class TestThrottling:
    def test_progress_updates_collapse(self, monkeypatch):
        """A 600-file repo must not become 600 POSTs."""
        pub, post = _publisher(monkeypatch, min_interval=60)
        for i in range(50):
            pub.update("a/b", done=i, total=50)
        assert len(post.calls) == 1

    def test_lifecycle_transitions_are_never_throttled(self, monkeypatch):
        """Start and finish are the events the dashboard cannot miss."""
        pub, post = _publisher(monkeypatch, min_interval=60)
        pub.start("a/b")
        pub.update("a/b", done=1, total=2)  # throttled behind start
        pub.finish("a/b")
        states = [c["jobs"][0]["state"] for c in post.calls]
        assert states == ["running", "done"]

    def test_one_repo_does_not_silence_another(self, monkeypatch):
        """Throttling is per job, or a busy repo would hide its neighbours."""
        pub, post = _publisher(monkeypatch, min_interval=60)
        pub.update("a/one", done=1, total=2)
        pub.update("a/two", done=1, total=2)
        assert len(post.calls) == 2


class TestPayload:
    def test_queue_publish_is_authoritative(self, monkeypatch):
        """
        replace_queue is what retires rows left at `running` by a run the
        watchdog killed. Without it the dashboard shows dead work as live.
        """
        pub, post = _publisher(monkeypatch)
        pub.publish_queue(["b/two", "a/one"])
        payload = post.calls[0]
        assert payload["replace_queue"] is True
        assert [j["job_id"] for j in payload["jobs"]] == ["a/one", "b/two"]
        assert {j["state"] for j in payload["jobs"]} == {"queued"}

    def test_progress_updates_do_not_replace_the_queue(self, monkeypatch):
        """A per-file update must not retire every other repo as a side effect."""
        pub, post = _publisher(monkeypatch)
        pub.update("a/b", done=1, total=2, force=True)
        assert post.calls[0]["replace_queue"] is False

    def test_run_id_is_attached(self, monkeypatch):
        pub, post = _publisher(monkeypatch)
        pub.start("a/b")
        assert post.calls[0]["jobs"][0]["run_id"] == "r1"

    def test_display_lists_are_trimmed(self, monkeypatch):
        """The API caps these at 50; sending a 600-file manifest is pointless."""
        pub, post = _publisher(monkeypatch)
        pub.update(
            "a/b",
            done=1,
            total=600,
            in_flight=[f"f{i}.py" for i in range(100)],
            degraded=[f"d{i}.py" for i in range(100)],
            force=True,
        )
        progress = post.calls[0]["jobs"][0]["progress"]
        assert len(progress["in_flight"]) == 10
        assert len(progress["degraded"]) == 10

    def test_noop_false_is_sent_not_stripped(self, monkeypatch):
        """
        The None-filter drops absent fields, and `False` is falsy — so this pins
        that a real job says so explicitly rather than defaulting by omission.
        """
        pub, post = _publisher(monkeypatch)
        pub.finish("a/b", noop=False)
        assert post.calls[0]["jobs"][0]["noop"] is False

    def test_noop_true_is_carried(self, monkeypatch):
        pub, post = _publisher(monkeypatch)
        pub.finish("a/b", noop=True)
        assert post.calls[0]["jobs"][0]["noop"] is True

    def test_none_fields_are_omitted(self, monkeypatch):
        """Sending nulls would overwrite a mode the previous update established."""
        pub, post = _publisher(monkeypatch)
        pub.start("a/b")
        assert "mode" not in post.calls[0]["jobs"][0]

    def test_producer_and_queue_identify_the_source(self, monkeypatch):
        pub, post = _publisher(monkeypatch)
        pub.start("a/b")
        assert post.calls[0]["producer"] == "code-smriti"
        assert post.calls[0]["queue"] == "ingestion"
