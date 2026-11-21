"""P5: knowledge operations - qa_events (service log), page_views, FAQ entries / members / events, knowledge gaps /
occurrences, knowledge supplement tasks.

Revision ID: 0004_p5_ops_faq_gaps
Revises: 0003_p4_jobs_jwt_tree
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_p5_ops_faq_gaps"
down_revision = "0003_p4_jobs_jwt_tree"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('qa_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kb_id', sa.String(length=32), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=True),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('question_hash', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=48), nullable=False),
    sa.Column('outcome', sa.String(length=24), nullable=False),
    sa.Column('gap_type', sa.String(length=32), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('generation_ms', sa.Float(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('faq_id', sa.String(length=32), nullable=True),
    sa.Column('generation', sa.String(length=40), nullable=True),
    sa.Column('top_score', sa.Float(), nullable=True),
    sa.Column('retrieved', sa.Integer(), nullable=False),
    sa.Column('citations', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('qa_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_qa_events_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_qa_events_outcome'), ['outcome'], unique=False)
        batch_op.create_index(batch_op.f('ix_qa_events_ts'), ['ts'], unique=False)

    op.create_table('faq_entries',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('kb_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('citations', sa.JSON(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('evidence_key', sa.String(length=64), nullable=False),
    sa.Column('embeddings', sa.JSON(), nullable=False),
    sa.Column('frequency', sa.Integer(), nullable=False),
    sa.Column('distinct_users', sa.Integer(), nullable=False),
    sa.Column('similarity_mean', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('answer_source', sa.String(length=24), nullable=False),
    sa.Column('embedding_model', sa.String(length=128), nullable=False),
    sa.Column('llm_model', sa.String(length=128), nullable=False),
    sa.Column('llm_digest', sa.String(length=128), nullable=False),
    sa.Column('index_generation', sa.String(length=40), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_by', sa.String(length=64), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.String(length=500), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hit_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('faq_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_faq_entries_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_faq_entries_status'), ['status'], unique=False)

    op.create_table('knowledge_gaps',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('kb_id', sa.String(length=32), nullable=False),
    sa.Column('gap_type', sa.String(length=32), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('distinct_users', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verification', sa.JSON(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_gaps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_gaps_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_gaps_status'), ['status'], unique=False)

    op.create_table('page_views',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('route', sa.String(length=48), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('page_views', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_page_views_ts'), ['ts'], unique=False)

    op.create_table('faq_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('faq_id', sa.String(length=32), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('from_status', sa.String(length=16), nullable=True),
    sa.Column('to_status', sa.String(length=16), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['faq_id'], ['faq_entries.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('faq_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_faq_events_faq_id'), ['faq_id'], unique=False)

    op.create_table('faq_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('faq_id', sa.String(length=32), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('similarity', sa.Float(), nullable=False),
    sa.Column('asked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['faq_id'], ['faq_entries.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('faq_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_faq_members_faq_id'), ['faq_id'], unique=False)

    op.create_table('gap_occurrences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('gap_id', sa.String(length=32), nullable=False),
    sa.Column('qa_event_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('gap_type', sa.String(length=32), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['gap_id'], ['knowledge_gaps.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gap_occurrences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gap_occurrences_gap_id'), ['gap_id'], unique=False)

    op.create_table('knowledge_tasks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('gap_id', sa.String(length=32), nullable=False),
    sa.Column('kb_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('assignee', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('document_id', sa.String(length=32), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verification', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['gap_id'], ['knowledge_gaps.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_tasks_gap_id'), ['gap_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_tasks_kb_id'), ['kb_id'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('knowledge_tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_tasks_kb_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_tasks_gap_id'))

    op.drop_table('knowledge_tasks')
    with op.batch_alter_table('gap_occurrences', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gap_occurrences_gap_id'))

    op.drop_table('gap_occurrences')
    with op.batch_alter_table('faq_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_faq_members_faq_id'))

    op.drop_table('faq_members')
    with op.batch_alter_table('faq_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_faq_events_faq_id'))

    op.drop_table('faq_events')
    with op.batch_alter_table('page_views', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_page_views_ts'))

    op.drop_table('page_views')
    with op.batch_alter_table('knowledge_gaps', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_gaps_status'))
        batch_op.drop_index(batch_op.f('ix_knowledge_gaps_kb_id'))

    op.drop_table('knowledge_gaps')
    with op.batch_alter_table('faq_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_faq_entries_status'))
        batch_op.drop_index(batch_op.f('ix_faq_entries_kb_id'))

    op.drop_table('faq_entries')
    with op.batch_alter_table('qa_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_qa_events_ts'))
        batch_op.drop_index(batch_op.f('ix_qa_events_outcome'))
        batch_op.drop_index(batch_op.f('ix_qa_events_kb_id'))

    op.drop_table('qa_events')
    # ### end Alembic commands ###
