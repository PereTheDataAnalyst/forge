"""Import every model module so that metadata is complete for Alembic and tests."""

from app.db.models.api_key import ApiKey
from app.db.models.integration import (
    Evaluation,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    McpClient,
    SystemSetting,
    VoiceRequest,
)
from app.db.models.project import Project
from app.db.models.telemetry import AuditLog, RequestLog, UsageDaily
from app.db.models.user import User, UserSession

__all__ = [
    "ApiKey",
    "AuditLog",
    "Evaluation",
    "EvaluationCase",
    "EvaluationResult",
    "EvaluationRun",
    "McpClient",
    "Project",
    "RequestLog",
    "SystemSetting",
    "UsageDaily",
    "User",
    "UserSession",
    "VoiceRequest",
]
