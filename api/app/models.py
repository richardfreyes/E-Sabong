"""Imports every ORM model so metadata is complete for Alembic and mappers resolve."""

from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthSession, PlatformUser
from app.modules.declarator.models import DeclarationAudit, EventDeclarator, ResultDeclaration
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction
from app.modules.events.models import Event
from app.modules.licenses.models import DeploymentInstance, LicenseActivation, LicenseKey
from app.modules.matches.models import Match
from app.modules.notifications.models import Notification
from app.modules.predictions.models import Prediction
from app.modules.results.models import MatchResult, MatchResultHistory
from app.modules.subscriptions.models import (
    Invoice,
    InvoiceLine,
    SubscriptionPlan,
    TenantSubscription,
)
from app.modules.tenants.models import (
    FeatureFlag,
    SignupRequest,
    Tenant,
    TenantBranding,
    TenantDomain,
    TenantFeatureFlag,
    TenantSettings,
    TenantUsage,
)
from app.modules.users.models import Role, RolePermission, TenantUser, UserRole
from app.shared.models import Base

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "DeploymentInstance",
    "Event",
    "DeclarationAudit",
    "EventDeclarator",
    "ResultDeclaration",
    "Prediction",
    "FeatureFlag",
    "Invoice",
    "InvoiceLine",
    "LicenseActivation",
    "LicenseKey",
    "Match",
    "MatchResult",
    "MatchResultHistory",
    "Notification",
    "PlatformUser",
    "PointAccount",
    "PointLedgerEntry",
    "PointTransaction",
    "Role",
    "RolePermission",
    "SignupRequest",
    "SubscriptionPlan",
    "Tenant",
    "TenantBranding",
    "TenantDomain",
    "TenantFeatureFlag",
    "TenantSettings",
    "TenantSubscription",
    "TenantUsage",
    "TenantUser",
    "UserRole",
]

# Tables isolated by RLS on tenant_id (the `tenants` table is isolated on id).
TENANT_TABLES = (
    "tenant_domains",
    "tenant_branding",
    "tenant_settings",
    "tenant_feature_flags",
    "tenant_usage",
    "tenant_subscriptions",
    "invoices",
    "invoice_lines",
    "tenant_users",
    "roles",
    "role_permissions",
    "user_roles",
    "auth_sessions",
    "events",
    "matches",
    "event_declarators",
    "result_declarations",
    "declaration_audit",
    "match_results",
    "match_result_history",
    "predictions",
    "point_accounts",
    "point_transactions",
    "point_ledger_entries",
    "notifications",
    "audit_logs",
)
