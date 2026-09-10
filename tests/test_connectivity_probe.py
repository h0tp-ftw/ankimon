import socket


class _SocketContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


def test_connectivity_uses_fast_tcp_path(monkeypatch):
    import Ankimon.utils as utils

    calls = []

    def create_connection(address, timeout):
        calls.append((address, timeout))
        return _SocketContext()

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(
        utils.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP fallback should not run")
        ),
    )

    assert utils.test_online_connectivity("https://example.com/file", timeout=0.5)
    assert calls == [(('example.com', 443), 0.5)]


def test_connectivity_falls_back_to_proxy_aware_http(monkeypatch):
    import Ankimon.utils as utils

    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("direct blocked")),
    )
    response = _Response(200)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(utils.requests, "get", get)

    assert utils.test_online_connectivity("https://example.com/file", timeout=0.5)
    assert calls == [
        ("https://example.com/file", {"timeout": 0.5, "stream": True})
    ]
    assert response.closed is True


def test_connectivity_rejects_unsupported_urls(monkeypatch):
    import Ankimon.utils as utils

    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("socket should not run")
        ),
    )

    assert not utils.test_online_connectivity("file:///tmp/update.txt")
