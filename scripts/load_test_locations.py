"""Load-test admin fleet WebSocket fan-out from driver GPS updates in staging only.

Set DRIVER_TOKEN (GPS publisher) and ADMIN_TOKEN (fleet subscribers) to short-lived staging JWTs. Never pass tokens on the command line.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile + 0.999)))
    return ordered[index]


def build_report(
    *, requested_connections: int, connected: int, messages_sent: int,
    frames_received: int, connect_latencies_ms: list[float],
    fanout_latencies_ms: list[float], failures: int, elapsed_seconds: float,
) -> dict:
    expected = connected * messages_sent
    return {
        "requested_connections": requested_connections,
        "connected": connected,
        "connection_success_rate": round(connected / requested_connections, 4) if requested_connections else 0.0,
        "messages_sent": messages_sent,
        "frames_received": frames_received,
        "expected_frames": expected,
        "frame_delivery_rate": round(frames_received / expected, 4) if expected else 0.0,
        "failures": failures,
        "connect_latency_ms": {
            "p50": round(percentile(connect_latencies_ms, 0.50), 2),
            "p95": round(percentile(connect_latencies_ms, 0.95), 2),
            "p99": round(percentile(connect_latencies_ms, 0.99), 2),
        },
        "fanout_latency_ms": {
            "p50": round(percentile(fanout_latencies_ms, 0.50), 2),
            "p95": round(percentile(fanout_latencies_ms, 0.95), 2),
            "p99": round(percentile(fanout_latencies_ms, 0.99), 2),
        },
        "elapsed_seconds": round(elapsed_seconds, 3),
        "gps_publish_per_second": round(messages_sent / elapsed_seconds, 3) if elapsed_seconds > 0 else 0.0,
    }


async def run(args: argparse.Namespace) -> int:
    driver_token = os.getenv("DRIVER_TOKEN", "")
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not driver_token or not admin_token:
        raise SystemExit("DRIVER_TOKEN and ADMIN_TOKEN must be set to short-lived staging JWTs")

    connector = aiohttp.TCPConnector(limit=max(args.connections + 20, 100))
    timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=15)
    base = args.base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    ws_base += "/api/v1/admin/fleet/stream"
    driver_headers = {"Authorization": f"Bearer {driver_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    sockets: list[aiohttp.ClientWebSocketResponse] = []
    connect_latencies: list[float] = []
    fanout_latencies: list[float] = []
    failures = 0
    messages_sent = 0
    frames_received = 0
    started = time.monotonic()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def connect_one():
            nonlocal failures
            connect_started = time.monotonic()
            try:
                async with session.post(
                    base + "/api/ws-ticket",
                    json={"scope": "admin-fleet"},
                    headers=admin_headers,
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(f"ticket endpoint returned {response.status}")
                    ticket_payload = await response.json()
                ticket = ticket_payload.get("ticket")
                if not isinstance(ticket, str) or not ticket:
                    raise RuntimeError("ticket endpoint returned no ticket")
                ws_url = ws_base + "?ticket=" + quote(ticket, safe="")
                ws = await session.ws_connect(ws_url, heartbeat=20)
                sockets.append(ws)
                connect_latencies.append((time.monotonic() - connect_started) * 1000)
            except Exception as exc:
                failures += 1
                print(f"WebSocket connect failed: {type(exc).__name__}")

        await asyncio.gather(*(connect_one() for _ in range(args.connections)))
        print(f"Connected {len(sockets)}/{args.connections} WebSocket clients")
        if not sockets:
            return 1

        await asyncio.sleep(1.0)
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
            async with session.post(
                base + "/api/v1/driver/location", json=payload, headers=driver_headers
            ) as response:
                if response.status != 200:
                    print(f"GPS publish failed ({response.status})")
                    failures += 1
                    break
                await response.read()
                messages_sent += 1

            async def receive_one(ws):
                nonlocal failures, frames_received
                try:
                    message = await ws.receive(timeout=args.receive_timeout)
                    if message.type == aiohttp.WSMsgType.TEXT:
                        frames_received += 1
                    else:
                        failures += 1
                except Exception:
                    failures += 1

            fanout_started = time.monotonic()
            await asyncio.gather(*(receive_one(ws) for ws in sockets))
            fanout_latencies.append((time.monotonic() - fanout_started) * 1000)

        elapsed = time.monotonic() - started
        report = build_report(
            requested_connections=args.connections,
            connected=len(sockets),
            messages_sent=messages_sent,
            frames_received=frames_received,
            connect_latencies_ms=connect_latencies,
            fanout_latencies_ms=fanout_latencies,
            failures=failures,
            elapsed_seconds=elapsed,
        )
        rendered = json.dumps(report, indent=2, sort_keys=True)
        print(rendered)
        if args.json_output:
            output = Path(args.json_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        for ws in sockets:
            await ws.close()
    return 1 if failures or report["frame_delivery_rate"] < args.min_delivery_rate else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("VCAR_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--connections", type=int, default=500)
    parser.add_argument("--messages", type=int, default=3)
    parser.add_argument("--latitude", type=float, default=11.5564)
    parser.add_argument("--longitude", type=float, default=104.9282)
    parser.add_argument("--receive-timeout", type=float, default=12.0)
    parser.add_argument("--min-delivery-rate", type=float, default=0.99)
    parser.add_argument("--json-output", help="Optional path for a machine-readable JSON report")
    args = parser.parse_args()
    if not 1 <= args.connections <= 1000 or not 1 <= args.messages <= 20:
        parser.error("connections must be 1..1000 and messages must be 1..20")
    if args.receive_timeout <= 0 or not 0 <= args.min_delivery_rate <= 1:
        parser.error("receive-timeout must be positive and min-delivery-rate must be 0..1")
    if not (-90 <= args.latitude <= 90 and -180 <= args.longitude <= 180):
        parser.error("latitude/longitude are outside valid geographic bounds")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
