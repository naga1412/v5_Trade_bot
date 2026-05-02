import pytest

from app.ws.manager import ConnectionManager, Subscription


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_subscribe_then_publish_routes_message() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert len(sock.sent) == 1
    assert sock.sent[0]["payload"]["price"] == 100.0


@pytest.mark.asyncio
async def test_publish_to_nonmatching_key_does_not_send() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "ETH/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_detach_removes_subscription() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)
    mgr.detach("c1")

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_failed_send_auto_detaches_client() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    await sock.close()  # makes sends raise
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    # Subsequent publish must not raise (client already detached)
    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 101.0})
    assert mgr.subscriber_count("live_prediction") == 0
