#!/usr/bin/env python3
"""Run a read-only wallet ledger reconciliation; never auto-adjust balances."""
from __future__ import annotations

import asyncio
import json

from app.db.core import db
from app.modules.payments.reconciliation import (
    reconcile_order_settlements,
    reconcile_wallet_ledger,
)


async def main() -> int:
    await db.init()
    try:
        discrepancies = await reconcile_wallet_ledger()
        settlement_findings = await reconcile_order_settlements()
        has_findings = bool(discrepancies or settlement_findings)
        print(json.dumps({
            "status": "discrepancies_found" if has_findings else "balanced",
            "wallet_discrepancy_count": len(discrepancies),
            "wallet_discrepancies": discrepancies,
            "settlement_finding_count": len(settlement_findings),
            "settlement_findings": settlement_findings,
        }, ensure_ascii=False, indent=2))
        return 2 if has_findings else 0
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
