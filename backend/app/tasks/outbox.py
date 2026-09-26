from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="mmcat.outbox.dispatch", bind=True, max_retries=8)
def dispatch_outbox_event(self, event_id: str) -> None:
    """Worker entrypoint.

    The async service claims a row with SKIP LOCKED, verifies idempotency, then
    invokes the target. Keeping this synchronous entrypoint small prevents a
    Celery retry from duplicating business writes.
    """
    # Implementation is intentionally delegated to the async outbox service in the next slice.
    # This task must not accept arbitrary event payloads from a message broker.
    if not event_id:
        raise ValueError("event_id is required")
