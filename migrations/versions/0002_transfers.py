"""Transfer and idempotency tables.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transfers",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("from_account_id", sa.String(length=32), nullable=False),
        sa.Column("to_account_id", sa.String(length=32), nullable=False),
        sa.Column("amount_piastres", sa.Integer(), nullable=False),
        sa.Column("memo", sa.String(length=140), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.CheckConstraint("amount_piastres > 0", name="ck_transfers_amount_positive"),
        sa.ForeignKeyConstraint(["from_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["to_account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "key", name="uq_idempotency_customer_key"),
    )


def downgrade():
    op.drop_table("idempotency_records")
    op.drop_table("transfers")
