import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


class WebSocketLike(Protocol):
    async def send_json(self, data: dict) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Subscription:
    client_id: str
    channel: str
    params: dict[str, Any]


def _key_matches(sub_params: dict[str, Any], publish_key: dict[str, Any]) -> bool:
    return all(sub_params.get(k) == v for k, v in publish_key.items())


class ConnectionManager:
    def __init__(self) -> None:
        self._subs: dict[str, tuple[Subscription, WebSocketLike]] = {}
        self._lock = asyncio.Lock()

    def attach(self, sub: Subscription, socket: WebSocketLike) -> None:
        self._subs[sub.client_id] = (sub, socket)

    def detach(self, client_id: str) -> None:
        self._subs.pop(client_id, None)

    def subscriber_count(self, channel: str) -> int:
        return sum(1 for sub, _ in self._subs.values() if sub.channel == channel)

    async def publish(
        self, *, channel: str, key: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        message = {"channel": channel, "key": key, "payload": payload}
        dead: list[str] = []
        for cid, (sub, sock) in list(self._subs.items()):
            if sub.channel != channel:
                continue
            if not _key_matches(sub.params, key):
                continue
            try:
                await sock.send_json(message)
            except Exception:  # noqa: BLE001 — drop dead client
                dead.append(cid)
        async with self._lock:
            for cid in dead:
                self._subs.pop(cid, None)
