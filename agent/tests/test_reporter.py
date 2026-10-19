import httpx

from runboard_agent.collector.fake import FakeCollector
from runboard_agent.reporter import REPORT_PATH, Reporter


def _report():
    return FakeCollector("fake01", gpu_count=1, clock=lambda: 0.0).collect()


def test_sends_bearer_token_and_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["ctype"] = request.headers.get("content-type")
        seen["body"] = request.read()
        return httpx.Response(204)

    reporter = Reporter("http://srv/", "tok-1", transport=httpx.MockTransport(handler))
    assert reporter.send(_report()) is True
    assert seen["path"] == REPORT_PATH
    assert seen["auth"] == "Bearer tok-1"
    assert seen["ctype"] == "application/json"
    assert b'"name":"fake01"' in seen["body"]
    assert reporter.wait_time(5.0) == 5.0


def test_backoff_grows_then_resets():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            raise httpx.ConnectError("refused")
        return httpx.Response(204)

    reporter = Reporter("http://srv", "t", transport=httpx.MockTransport(handler))
    waits = []
    for _ in range(3):
        assert reporter.send(_report()) is False
        waits.append(reporter.wait_time(5.0))
    assert waits == [5.0, 5.0, 8.0], "2,4 는 주기 5초보다 짧아 주기를 쓰고, 8 부터 백오프"
    assert reporter.send(_report()) is True
    assert reporter.failures == 0 and reporter.wait_time(5.0) == 5.0


def test_backoff_caps_at_max():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    reporter = Reporter("http://srv", "t", transport=httpx.MockTransport(handler))
    for _ in range(12):
        reporter.send(_report())
    assert reporter.wait_time(5.0) == 60.0


def test_auth_failure_is_reported_as_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad token"})

    reporter = Reporter("http://srv", "t", transport=httpx.MockTransport(handler))
    assert reporter.send(_report()) is False
    assert reporter.failures == 1
