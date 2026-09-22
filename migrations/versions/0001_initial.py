"""initial schema: files and audit_events

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_files"),
        sa.UniqueConstraint("storage_key", name="uq_files_storage_key"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_files_owner_idempotency"),
    )
    # Owner listing, newest first.
    op.create_index("ix_files_owner_created", "files", ["owner_id", "created_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("link_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], name="fk_audit_events_file_id_files", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    # Per-file audit trail, newest first.
    op.create_index("ix_audit_events_file_created", "audit_events", ["file_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_file_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_files_owner_created", table_name="files")
    op.drop_table("files")
