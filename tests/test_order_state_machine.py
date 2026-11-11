import pytest

from app.modules.orders.state_machine import InvalidOrderTransition, validate_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DISPATCHING", "ACCEPTED"),
        ("DISPATCHING", "CANCELLED"),
        ("ACCEPTED", "ARRIVED"),
        ("ACCEPTED", "CANCELLED"),
        ("ARRIVED", "IN_PROGRESS"),
        ("ARRIVED", "CANCELLED"),
        ("IN_PROGRESS", "COMPLETED"),
    ],
)
def test_valid_order_transitions(current, target):
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DISPATCHING", "COMPLETED"),
        ("ACCEPTED", "IN_PROGRESS"),
        ("IN_PROGRESS", "CANCELLED"),
        ("COMPLETED", "ACCEPTED"),
        ("CANCELLED", "DISPATCHING"),
        ("UNKNOWN", "ACCEPTED"),
    ],
)
def test_invalid_order_transitions(current, target):
    with pytest.raises(InvalidOrderTransition):
        validate_transition(current, target)
