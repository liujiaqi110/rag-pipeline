"""P3 (01cfd9a): document versions, chunks, index generations, KB managers; documents.source/original_filename/current_version_id.

Revision ID: 0002_p3_ingestion
Revises: 0001_p2_baseline
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_p3_ingestion"
down_revision = '0001_p2_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('index_generations',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('document_count', sa.Integer(), nullable=False),
    sa.Column('dense', sa.Boolean(), nullable=False),
    sa.Column('dense_model', sa.String(length=128), nullable=True),
    sa.Column('dense_error', sa.String(length=64), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('built_by', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('kb_managers',
    sa.Column('kb_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('kb_id', 'user_id')
    )
    op.create_table('document_versions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('document_id', sa.String(length=32), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('file_format', sa.String(length=16), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_relpath', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=255), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('generation_id', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'version_no', name='uq_version_no')
    )
    op.create_table('chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.String(length=32), nullable=False),
    sa.Column('chunk_id', sa.String(length=32), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('is_parent', sa.Boolean(), nullable=False),
    sa.Column('parent_id', sa.String(length=32), nullable=True),
    sa.Column('section_heading', sa.String(length=512), nullable=True),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('paragraph', sa.Integer(), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['version_id'], ['document_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'chunk_id', name='uq_version_chunk')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=16), server_default='metadata', nullable=False))
        batch_op.add_column(sa.Column('original_filename', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('current_version_id', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('current_version_id')
        batch_op.drop_column('original_filename')
        batch_op.drop_column('source')

    op.drop_table('chunks')
    op.drop_table('document_versions')
    op.drop_table('kb_managers')
    op.drop_table('index_generations')
    # ### end Alembic commands ###
