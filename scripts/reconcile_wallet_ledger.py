#!/usr/bin/env python3
"""Run a read-only wallet ledger reconciliation; never auto-adjust balances."""
from __future__ import annotations

import asyncio
import json

from app.db.core import db
from app.modules.payments.reconciliation import reconcile_wallet_ledger


async def main() -> int:
    await db.init()
    try:
        discrepancies = await reconcile_wallet_ledger()
        print(json.dumps({
            "status": "discrepancies_found" if discrepancies else "balanced",
            "discrepancy_count": len(discrepancies),
            "discrepancies": discrepancies,
        }, ensure_ascii=False, indent=2))
        return 2 if discrepancies else 0
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
