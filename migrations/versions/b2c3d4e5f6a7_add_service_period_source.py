"""add service_period_source to bookings

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-13 09:00:00.000000

Kennzeichnet, woher der Leistungszeitraum einer Buchung stammt: aus dem Belegtitel
(der Anlasstermin selbst) oder nur aus dem Dokumentdatum. Der Wert wird beim
naechsten Sync-Lauf fuer alle Buchungen neu berechnet, deshalb genuegt hier ein
Default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bookings',
        sa.Column('service_period_source', sa.String(), nullable=False, server_default='none'),
    )


def downgrade() -> None:
    op.drop_column('bookings', 'service_period_source')
