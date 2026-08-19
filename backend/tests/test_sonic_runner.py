import asyncio

from app.services.runners.base import RunContext
from app.services.runners.sonic_runner import SonicRunner, release_cached_sonic_sessions


class _Case:
    id = "case-1"
    title = "demo"
    steps = []
    expected_result = ""
    platforms = ["Android App"]


def test_sonic_runner_reuses_occupied_device_within_same_execution(monkeypatch):
    import app.services.runners.sonic_runner as sr

    calls = {"occupy": 0, "release": 0, "android": 0, "adb": []}

    class _FakeClient:
        async def occupy(self, ud_id: str, sas_port: int) -> str:
            calls["occupy"] += 1
            return "127.0.0.1:30001"

        async def release(self, ud_id: str) -> None:
            calls["release"] += 1

    class _FakeCp:
        def __init__(self, out: str):
            self.stdout = out
            self.stderr = ""

    def _fake_adb(args, timeout=30):
        calls["adb"].append(tuple(args))
        if args[:1] == ["connect"]:
            return _FakeCp("connected to 127.0.0.1:30001")
        if "shell" in args and "echo" in args and "ready" in args:
            return _FakeCp("ready")
        if args[:1] == ["disconnect"]:
            return _FakeCp("disconnected")
        return _FakeCp("")

    class _FakeAndroidRunner:
        async def run(self, case, ctx):
            calls["android"] += 1
            assert ctx.device_udid == "127.0.0.1:30001"
            from app.services.runners.base import RunOutcome

            return RunOutcome(status="passed", duration_ms=1)

    monkeypatch.setattr(sr, "_adb", _fake_adb)
    monkeypatch.setattr(sr, "AndroidAgentRunner", _FakeAndroidRunner)
    monkeypatch.setattr("app.services.sonic_client.SonicClient", _FakeClient)

    async def _run():
        ctx = RunContext(execution_id="exec-1", extra={"target_device": "sonic:demo-udid"})
        runner = SonicRunner()
        out1 = await runner.run(_Case(), ctx)
        out2 = await runner.run(_Case(), ctx)
        assert out1.status == "passed"
        assert out2.status == "passed"
        assert calls["occupy"] == 1
        assert calls["android"] == 2
        await release_cached_sonic_sessions(ctx.extra)
        assert calls["release"] == 1

    asyncio.run(_run())
