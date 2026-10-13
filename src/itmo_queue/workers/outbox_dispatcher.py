"""Scaffold for dispatching durable Outbox events.

Implementation requirements: claim events safely across worker instances, retry transient
failures, mark delivery state, and make handling idempotent. Event payloads must be committed
with the originating business-state transaction in PostgreSQL.
"""
