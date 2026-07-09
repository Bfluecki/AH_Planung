"""add must_change_password to users

Revision ID: a1b2c3d4e5f6
Revises: 3bf6e6751de0
Create Date: 2026-07-09 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '3bf6e6751de0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bestehende Benutzer sollen sich weiter ohne Zwang anmelden koennen (server_default
    # false fuer die Backfill-Zeilen), danach greift der App-Default. Neu angelegte
    # Benutzer erhalten das Flag ueber die Admin-Benutzerverwaltung explizit auf True.
    op.add_column(
        'users',
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('users', 'must_change_password', server_default=None)


def downgrade() -> None:
    op.drop_column('users', 'must_change_password')
