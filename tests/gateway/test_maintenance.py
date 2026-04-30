import asyncio

from gateway.maintenance import retention_loop


class _FakeStore:
    def __init__(self):
        self.calls = []

    def archive_and_prune_expired_transcripts(self, older_than_days=14):
        self.calls.append(older_than_days)
        raise asyncio.CancelledError()


def test_retention_loop_calls_store():
    store = _FakeStore()

    async def _run():
        try:
            await retention_loop(store, older_than_days=14, initial_delay=0, interval_seconds=3600)
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert store.calls == [14]
