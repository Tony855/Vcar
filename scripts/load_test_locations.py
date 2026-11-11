"""Load-test the authenticated driver location WebSocket fan-out.

Run only against a staging environment with an approved test driver account.
Set DRIVER_TOKEN in the environment; never put the JWT in shell history.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp


async def run(args: argparse.Namespace) -> int:
    token = os.getenv("DRIVER_TOKEN", "")
    if not token:
        raise SystemExit("DRIVER_TOKEN must be set to a short-lived staging driver JWT")

    connector = aiohttp.TCPConnector(limit=max(args.connections + 20, 100))
    timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=15)
    base = args.base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    ws_base += "/api/v1/driver/location/stream"
    headers = {"Authorization": f"Bearer {token}"}
    sockets: list[aiohttp.ClientWebSocketResponse] = []
    failures = 0
    started = time.monotonic()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def connect_one():
            nonlocal failures
            try:
                async with session.post(base + "/api/ws-ticket", json={"scope": "driver-location"}, headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError(f"ticket endpoint returned {response.status}")
                    ticket_payload = await response.json()
                ws_url = ws_base + "?ticket=" + quote(ticket_payload["ticket"], safe="")
                ws = await session.ws_connect(ws_url, heartbeat=20)
                sockets.append(ws)
            except Exception as exc:
                failures += 1
                print(f"WebSocket connect failed: {type(exc).__name__}")

        await asyncio.gather(*(connect_one() for _ in range(args.connections)))
        print(f"Connected {len(sockets)}/{args.connections} WebSocket clients")
        if not sockets:
            return 1

        await asyncio.sleep(1.0)
        received = 0
        for index in range(args.messages):
            now = datetime.now(timezone.utc) + timedelta(microseconds=index)
            payload = {
                "latitude": args.latitude,
                "longitude": args.longitude,
                "accuracy_m": 8.0,
                "heading_deg": 90.0,
                "speed_mps": 4.0,
                "recorded_at": now.isoformat(),
            }
            async with session.post(base + "/api/v1/driver/location", json=payload, headers=headers) as response:
                if response.status != 200:
                    body = await response.text()
                    print(f"GPS publish failed ({response.status}): {body[:300]}")
                    failures += 1
                    break
                await response.read()

            async def receive_one(ws):
                nonlocal failures, received
                try:
                    message = await ws.receive(timeout=12)
                    if message.type == aiohttp.WSMsgType.TEXT:
                        received += 1
                    else:
                        failures += 1
                except Exception:
                    failures += 1

            await asyncio.gather(*(receive_one(ws) for ws in sockets))

        elapsed = time.monotonic() - started
        print(
            f"connections={len(sockets)} messages={args.messages} "
            f"received_frames={received} failures={failures} elapsed_seconds={elapsed:.2f}"
        )
        for ws in sockets:
            await ws.close()
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("VCAR_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--connections", type=int, default=500)
    parser.add_argument("--messages", type=int, default=3)
    parser.add_argument("--latitude", type=float, default=11.5564)
    parser.add_argument("--longitude", type=float, default=104.9282)
    args = parser.parse_args()
    if not 1 <= args.connections <= 1000 or not 1 <= args.messages <= 20:
        parser.error("connections must be 1..1000 and messages must be 1..20")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
