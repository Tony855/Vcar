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


def fleet_websocket_url(base_url: str, ticket: str) -> str:
    ws_base = base_url.rstrip("/")
    if ws_base.startswith("https://"):
        ws_base = "wss://" + ws_base[len("https://"):]
    elif ws_base.startswith("http://"):
        ws_base = "ws://" + ws_base[len("http://"):]
    else:
        raise ValueError("base URL must use http or https")
    return ws_base + "/api/v1/admin/fleet/stream?ticket=" + quote(ticket, safe="")


def is_fleet_snapshot_frame(payload: str) -> bool:
    try:
        frame = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        isinstance(frame, dict)
        and frame.get("type") == "snapshot"
        and isinstance(frame.get("drivers"), list)
    )


def is_fleet_location_event(payload: str) -> bool:
    try:
        event = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        isinstance(event, dict)
        and isinstance(event.get("telegram_id"), int)
        and isinstance(event.get("latitude"), (int, float))
        and isinstance(event.get("longitude"), (int, float))
        and isinstance(event.get("recorded_at"), str)
    )


def build_report(
    *, requested_connections: int, connected: int, messages_sent: int,
    frames_received: int, connect_latencies_ms: list[float],
    fanout_latencies_ms: list[float], failures: int, elapsed_seconds: float,
    reconnect_attempts: int = 0, reconnect_snapshots: int = 0,
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
        "reconnect_attempts": reconnect_attempts,
        "reconnect_snapshots": reconnect_snapshots,
        "reconnect_snapshot_rate": round(reconnect_snapshots / reconnect_attempts, 4) if reconnect_attempts else 1.0,
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
                ws_url = fleet_websocket_url(base, ticket)
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

        # Consume the durable snapshot sent on every new admin stream before
        # counting live GPS fan-out frames.
        async def receive_initial_snapshot(ws):
            nonlocal failures
            try:
                frame = await ws.receive(timeout=args.receive_timeout)
                if frame.type != aiohttp.WSMsgType.TEXT or not is_fleet_snapshot_frame(frame.data):
                    failures += 1
            except Exception:
                failures += 1

        await asyncio.gather(*(receive_initial_snapshot(ws) for ws in sockets))

        await asyncio.sleep(0.2)
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
                    if message.type == aiohttp.WSMsgType.TEXT and is_fleet_location_event(message.data):
                        frames_received += 1
                    else:
                        failures += 1
                except Exception:
                    failures += 1

            fanout_started = time.monotonic()
            await asyncio.gather(*(receive_one(ws) for ws in sockets))
            fanout_latencies.append((time.monotonic() - fanout_started) * 1000)

        reconnect_attempts = 0
        reconnect_snapshots = 0
        for index in range(min(args.reconnect_probes, len(sockets))):
            reconnect_attempts += 1
            old_socket = sockets[index]
            await old_socket.close()
            try:
                async with session.post(
                    base + "/api/ws-ticket",
                    json={"scope": "admin-fleet"},
                    headers=admin_headers,
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(f"reconnect ticket endpoint returned {response.status}")
                    ticket_payload = await response.json()
                ticket = ticket_payload.get("ticket")
                if not isinstance(ticket, str) or not ticket:
                    raise RuntimeError("reconnect ticket endpoint returned no ticket")
                new_socket = await session.ws_connect(
                    fleet_websocket_url(base, ticket), heartbeat=20
                )
                frame = await new_socket.receive(timeout=args.receive_timeout)
                if frame.type != aiohttp.WSMsgType.TEXT or not is_fleet_snapshot_frame(frame.data):
                    raise RuntimeError("reconnected stream did not send a fleet snapshot")
                sockets[index] = new_socket
                reconnect_snapshots += 1
            except Exception as exc:
                failures += 1
                print(f"WebSocket reconnect probe failed: {type(exc).__name__}")

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
            reconnect_attempts=reconnect_attempts,
            reconnect_snapshots=reconnect_snapshots,
        )
        rendered = json.dumps(report, indent=2, sort_keys=True)
        print(rendered)
        if args.json_output:
            output = Path(args.json_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        for ws in sockets:
            await ws.close()
    return 1 if failures or report["frame_delivery_rate"] < args.min_delivery_rate or report["reconnect_snapshot_rate"] < args.min_delivery_rate else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("VCAR_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--connections", type=int, default=500)
    parser.add_argument("--messages", type=int, default=3)
    parser.add_argument("--reconnect-probes", type=int, default=5,
                        help="Number of admin clients to reconnect and verify snapshot recovery (0..connections)")
    parser.add_argument("--latitude", type=float, default=11.5564)
    parser.add_argument("--longitude", type=float, default=104.9282)
    parser.add_argument("--receive-timeout", type=float, default=12.0)
    parser.add_argument("--min-delivery-rate", type=float, default=0.99)
    parser.add_argument("--json-output", help="Optional path for a machine-readable JSON report")
    args = parser.parse_args()
    if not 1 <= args.connections <= 1000 or not 1 <= args.messages <= 20:
        parser.error("connections must be 1..1000 and messages must be 1..20")
    if not 0 <= args.reconnect_probes <= args.connections:
        parser.error("reconnect-probes must be 0..connections")
    if args.receive_timeout <= 0 or not 0 <= args.min_delivery_rate <= 1:
        parser.error("receive-timeout must be positive and min-delivery-rate must be 0..1")
    if not (-90 <= args.latitude <= 90 and -180 <= args.longitude <= 180):
        parser.error("latitude/longitude are outside valid geographic bounds")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
