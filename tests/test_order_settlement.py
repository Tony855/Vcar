import pytest

from app.modules.payments import settlement


class FakeTransaction:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False


class FakeConnection:
    def __init__(self, order_status="COMPLETED", previous=None):
        self.order_status, self.previous, self.calls = order_status, previous, []
    def transaction(self): return FakeTransaction()
    async def execute(self, query, *args):
        self.calls.append(("execute", query, args)); return "SELECT 1"
    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        if "FROM orders" in query: return {"id": args[0], "status": self.order_status}
        if "FROM order_settlements" in query: return self.previous
        if "INSERT INTO order_settlements" in query: return {"id": 91, "status": "PREPARED"}
        raise AssertionError(f"unexpected query: {query}")


class Acquire:
    def __init__(self, connection): self.connection = connection
    async def __aenter__(self): return self.connection
    async def __aexit__(self, exc_type, exc, tb): return False


class Pool:
    def __init__(self, connection): self.connection = connection
    def acquire(self): return Acquire(self.connection)


@pytest.mark.asyncio
async def test_prepare_creates_snapshot_only_for_completed_order(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    result = await settlement.OrderSettlement.prepare(
        order_id=12, currency="USD", policy_version="kh-v1",
        idempotency_key="settlement-order-12-v1",
        components={"driver_earning": 800, "platform_fee": 200},
    )
    assert result.settlement_id == 91 and result.status == "PREPARED"
    assert result.replayed is False
    insert = [c for c in conn.calls if c[0] == "fetchrow" and "INSERT INTO order_settlements" in c[1]]
    assert len(insert) == 1
    assert '"driver_earning":800' in insert[0][2][4]


@pytest.mark.asyncio
async def test_prepare_replays_identical_snapshot_without_second_insert(monkeypatch):
    previous = {"id": 91, "order_id": 12, "currency": "USD", "policy_version": "kh-v1",
                "idempotency_key": "settlement-order-12-v1", "components": {"driver_earning": 800, "platform_fee": 200}, "status": "PREPARED"}
    conn = FakeConnection(previous=previous)
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    result = await settlement.OrderSettlement.prepare(
        order_id=12, currency="USD", policy_version="kh-v1",
        idempotency_key="settlement-order-12-v1",
        components={"platform_fee": 200, "driver_earning": 800},
    )
    assert result.replayed is True and result.settlement_id == 91
    assert not any("INSERT INTO order_settlements" in c[1] for c in conn.calls if c[0] == "fetchrow")


@pytest.mark.asyncio
async def test_prepare_rejects_changed_snapshot_for_same_order(monkeypatch):
    previous = {"id": 91, "order_id": 12, "currency": "USD", "policy_version": "kh-v1",
                "idempotency_key": "settlement-order-12-v1", "components": {"driver_earning": 800, "platform_fee": 200}, "status": "PREPARED"}
    monkeypatch.setattr(settlement.db, "pool", Pool(FakeConnection(previous=previous)))
    with pytest.raises(settlement.SettlementConflict):
        await settlement.OrderSettlement.prepare(
            order_id=12, currency="USD", policy_version="kh-v1",
            idempotency_key="settlement-order-12-v1",
            components={"driver_earning": 700, "platform_fee": 300},
        )


@pytest.mark.asyncio
async def test_prepare_rejects_non_completed_order(monkeypatch):
    conn = FakeConnection(order_status="IN_PROGRESS")
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    with pytest.raises(settlement.OrderNotSettleable):
        await settlement.OrderSettlement.prepare(
            order_id=12, currency="USD", policy_version="kh-v1",
            idempotency_key="settlement-order-12-v1", components={"fare": 1000},
        )
    assert not any("INSERT INTO order_settlements" in c[1] for c in conn.calls if c[0] == "fetchrow")


@pytest.mark.parametrize("components", [{}, {"Fare": 100}, {"fare": -1}, {"fare": True}, {"fare": 2**63}])
def test_component_validation_rejects_invalid_amounts(components):
    with pytest.raises(ValueError): settlement._validate_components(components)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"order_id": True, "currency": "USD", "policy_version": "v1", "idempotency_key": "key", "components": {"fare": 1}},
    {"order_id": 1, "currency": "EUR", "policy_version": "v1", "idempotency_key": "key", "components": {"fare": 1}},
    {"order_id": 1, "currency": "USD", "policy_version": "", "idempotency_key": "key", "components": {"fare": 1}},
])
async def test_prepare_rejects_invalid_contract_arguments(kwargs):
    with pytest.raises(ValueError): await settlement.OrderSettlement.prepare(**kwargs)


class PostingConnection:
    def __init__(self, status="PREPARED"):
        self.status = status
        self.calls = []

    def transaction(self):
        return FakeTransaction()

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        if "FROM order_settlements" in query:
            return {
                "id": 91, "order_id": 12, "currency": "USD",
                "policy_version": "kh-v1", "status": self.status,
            }
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        if "UPDATE order_settlements" in query and self.status == "PREPARED":
            self.status = "POSTED"
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self.status


@pytest.mark.asyncio
async def test_post_explicit_allocation_binds_snapshot_and_marks_posted(monkeypatch):
    conn = PostingConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    captured = {}

    async def post_batch(**kwargs):
        captured.update(kwargs)
        return settlement.LedgerBatchResult(
            kwargs["idempotency_key"], (), False
        )

    monkeypatch.setattr(settlement.WalletLedger, "post_balanced_batch", post_batch)
    result = await settlement.post_explicit_allocation(
        settlement_id=91,
        legs=[
            {"user_id": 10, "amount_minor": 900, "kind": "DRIVER_EARNING"},
            {"user_id": 20, "amount_minor": -900, "kind": "PASSENGER_FARE"},
        ],
    )

    assert result.status == "POSTED"
    assert result.order_id == 12
    assert captured["currency"] == "USD"
    assert captured["order_id"] == 12
    assert captured["idempotency_key"] == "settlement:91"
    assert captured["legs"][0]["metadata"] == {
        "_settlement_id": 91, "_policy_version": "kh-v1"
    }
    assert captured["legs"][1]["metadata"]["_settlement_id"] == 91
    assert conn.status == "POSTED"


@pytest.mark.asyncio
async def test_post_explicit_allocation_retry_reuses_deterministic_batch_key(monkeypatch):
    conn = PostingConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    calls = []

    async def post_batch(**kwargs):
        calls.append(kwargs)
        return settlement.LedgerBatchResult(
            kwargs["idempotency_key"], (), len(calls) > 1
        )

    monkeypatch.setattr(settlement.WalletLedger, "post_balanced_batch", post_batch)
    legs = [
        {"user_id": 10, "amount_minor": 900, "kind": "DRIVER_EARNING"},
        {"user_id": 20, "amount_minor": -900, "kind": "PASSENGER_FARE"},
    ]
    first = await settlement.post_explicit_allocation(settlement_id=91, legs=legs)
    second = await settlement.post_explicit_allocation(settlement_id=91, legs=legs)

    assert first.replayed is False
    assert second.replayed is True
    assert [call["idempotency_key"] for call in calls] == [
        "settlement:91", "settlement:91"
    ]
    assert conn.status == "POSTED"


@pytest.mark.asyncio
async def test_post_explicit_allocation_keeps_snapshot_prepared_when_ledger_fails(monkeypatch):
    conn = PostingConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))

    async def fail_batch(**kwargs):
        raise settlement.WalletError("insufficient funds")

    monkeypatch.setattr(settlement.WalletLedger, "post_balanced_batch", fail_batch)
    with pytest.raises(settlement.WalletError):
        await settlement.post_explicit_allocation(
            settlement_id=91,
            legs=[
                {"user_id": 10, "amount_minor": 900, "kind": "DRIVER_EARNING"},
                {"user_id": 20, "amount_minor": -900, "kind": "PASSENGER_FARE"},
            ],
        )

    assert conn.status == "PREPARED"
    assert not any("UPDATE order_settlements" in call[1] for call in conn.calls if call[0] == "execute")


@pytest.mark.asyncio
async def test_post_explicit_allocation_rejects_failed_snapshot(monkeypatch):
    conn = PostingConnection(status="FAILED")
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    called = False

    async def should_not_post(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("failed snapshot must not post")

    monkeypatch.setattr(settlement.WalletLedger, "post_balanced_batch", should_not_post)
    with pytest.raises(settlement.SettlementError):
        await settlement.post_explicit_allocation(
            settlement_id=91,
            legs=[
                {"user_id": 10, "amount_minor": 900, "kind": "DRIVER_EARNING"},
                {"user_id": 20, "amount_minor": -900, "kind": "PASSENGER_FARE"},
            ],
        )
    assert called is False


@pytest.mark.asyncio
async def test_post_explicit_allocation_rejects_reserved_metadata(monkeypatch):
    conn = PostingConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))
    with pytest.raises(ValueError):
        await settlement.post_explicit_allocation(
            settlement_id=91,
            legs=[
                {"user_id": 10, "amount_minor": 900, "kind": "DRIVER_EARNING",
                 "metadata": {"_settlement_id": 7}},
                {"user_id": 20, "amount_minor": -900, "kind": "PASSENGER_FARE"},
            ],
        )
