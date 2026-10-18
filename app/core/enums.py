from enum import Enum
class OrderStatus(str, Enum):
    DISPATCHING = "DISPATCHING"
    ACCEPTED = "ACCEPTED"
    ARRIVED = "ARRIVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
class DriverStatus(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
