"""Reserved schema for the Evaluation, Voice and MCP modules (owned by Agent 2).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

Agent 2 may amend this revision before it is merged to a shared branch, or add later
revisions. Table names must not change.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW = sa.text("now()")
EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_evaluations"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evaluations_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_evaluations_created_by_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_evaluations_project_id", "evaluations", ["project_id"])

    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_cases"),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name="fk_evaluation_cases_evaluation_id_evaluations",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_evaluation_cases_evaluation_id", "evaluation_cases", ["evaluation_id"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_runs"),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name="fk_evaluation_runs_evaluation_id_evaluations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_evaluation_runs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by"],
            ["users.id"],
            name="fk_evaluation_runs_triggered_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_evaluation_runs_evaluation_id", "evaluation_runs", ["evaluation_id"])
    op.create_index("ix_evaluation_runs_project_id", "evaluation_runs", ["project_id"])

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("request_log_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_results"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_results_run_id_evaluation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["evaluation_cases.id"],
            name="fk_evaluation_results_case_id_evaluation_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_log_id"],
            ["requests.id"],
            name="fk_evaluation_results_request_log_id_requests",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])

    op.create_table(
        "voice_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("asr_model", sa.String(length=120), nullable=False),
        sa.Column("audio_mime", sa.String(length=64), nullable=True),
        sa.Column("audio_bytes", sa.Integer(), nullable=True),
        sa.Column("audio_duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validation_ms", sa.Integer(), nullable=True),
        sa.Column("asr_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("transcript_chars", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_voice_requests"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_voice_requests_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name="fk_voice_requests_api_key_id_api_keys",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_voice_requests_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_voice_requests_project_id", "voice_requests", ["project_id"])

    op.create_table(
        "mcp_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("allowed_tools", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_clients"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_mcp_clients_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"], name="fk_mcp_clients_api_key_id_api_keys", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_mcp_clients_created_by_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_mcp_clients_project_id", "mcp_clients", ["project_id"])

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_system_settings"),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_system_settings_updated_by_users", ondelete="SET NULL"
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("mcp_clients")
    op.drop_table("voice_requests")
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_cases")
    op.drop_table("evaluations")
