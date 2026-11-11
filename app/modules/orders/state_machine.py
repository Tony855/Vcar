from app.core.enums import OrderStatus


TRANSITIONS = {
    OrderStatus.DISPATCHING: {OrderStatus.ACCEPTED, OrderStatus.CANCELLED},
    OrderStatus.ACCEPTED: {OrderStatus.ARRIVED, OrderStatus.CANCELLED},
    OrderStatus.ARRIVED: {OrderStatus.IN_PROGRESS, OrderStatus.CANCELLED},
    OrderStatus.IN_PROGRESS: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
}


class InvalidOrderTransition(ValueError):
    pass


def validate_transition(current: str | OrderStatus, target: str | OrderStatus) -> None:
    try:
        current_status = OrderStatus(current)
        target_status = OrderStatus(target)
    except ValueError as exc:
        raise InvalidOrderTransition("Unknown order status") from exc

    if target_status not in TRANSITIONS[current_status]:
        raise InvalidOrderTransition(
            f"Transition {current_status.value} -> {target_status.value} is not allowed"
        )
