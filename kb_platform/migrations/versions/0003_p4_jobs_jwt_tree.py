"""P4: background jobs, token kind (opaque|jwt), department tree (parent_id).

Revision ID: 0003_p4_jobs_jwt_tree
Revises: 0002_p3_ingestion
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_p4_jobs_jwt_tree"
down_revision = '0002_p3_ingestion'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('kb_id', sa.String(length=32), nullable=True),
    sa.Column('document_id', sa.String(length=32), nullable=True),
    sa.Column('version_id', sa.String(length=32), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=255), nullable=True),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('auth_tokens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=8), server_default='opaque', nullable=False))

    with op.batch_alter_table('departments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_departments_parent_id', 'departments', ['parent_id'], ['id'], ondelete='SET NULL')

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('departments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_departments_parent_id', type_='foreignkey')
        batch_op.drop_column('parent_id')

    with op.batch_alter_table('auth_tokens', schema=None) as batch_op:
        batch_op.drop_column('kind')

    op.drop_table('jobs')
