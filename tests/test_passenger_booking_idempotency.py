from app.bot.handlers.passenger import booking_idempotency_data


def test_same_confirmation_message_has_stable_booking_key_and_fingerprint():
    first = booking_idempotency_data(
        -100123, 456, 789, 11.5, 104.9, 11.6, 105.0, 5.2, 3.75
    )
    repeated = booking_idempotency_data(
        -100123, 456, 789, 11.5, 104.9, 11.6, 105.0, 5.2, 3.75
    )

    assert first == repeated
    assert first[0] == "tg-confirm:-100123:456"
    assert len(first[1]) == 64


def test_changed_booking_payload_changes_fingerprint_but_not_message_key():
    first = booking_idempotency_data(
        -100123, 456, 789, 11.5, 104.9, 11.6, 105.0, 5.2, 3.75
    )
    changed = booking_idempotency_data(
        -100123, 456, 789, 11.5, 104.9, 11.61, 105.0, 5.2, 3.75
    )

    assert first[0] == changed[0]
    assert first[1] != changed[1]
