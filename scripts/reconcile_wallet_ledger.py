#!/usr/bin/env python3
"""Audit wallet ledgers; optionally finalize one verified committed settlement batch."""
from __future__ import annotations

import argparse
import asyncio
import json

from app.db.core import db
from app.modules.payments.reconciliation import (
    reconcile_order_settlements,
    reconcile_wallet_ledger,
)
from app.modules.payments.settlement import finalize_committed_allocation


async def main(*, finalize_settlement: int | None = None) -> int:
    await db.init()
    try:
        recovery = None
        if finalize_settlement is not None:
            recovery_result = await finalize_committed_allocation(
                settlement_id=finalize_settlement
            )
            recovery = {
                "settlement_id": recovery_result.settlement_id,
                "status": recovery_result.status,
                "ledger_count": recovery_result.ledger_count,
                "recovered": recovery_result.recovered,
            }
        discrepancies = await reconcile_wallet_ledger()
        settlement_findings = await reconcile_order_settlements()
        has_findings = bool(discrepancies or settlement_findings)
        print(json.dumps({
            "status": "discrepancies_found" if has_findings else "balanced",
            "settlement_recovery": recovery,
            "wallet_discrepancy_count": len(discrepancies),
            "wallet_discrepancies": discrepancies,
            "settlement_finding_count": len(settlement_findings),
            "settlement_findings": settlement_findings,
        }, ensure_ascii=False, indent=2))
        return 2 if has_findings else 0
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--finalize-settlement", type=int,
        help="explicitly recover a PREPARED settlement only if its full balanced ledger batch is already committed",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(finalize_settlement=arguments.finalize_settlement)))
