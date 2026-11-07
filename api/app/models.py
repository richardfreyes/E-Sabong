"""Imports every ORM model so metadata is complete for Alembic and mappers resolve."""

from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthSession, PlatformUser
from app.modules.balances.models import BalanceOperation
from app.modules.betting.models import Bet, BettingSettings, FightBook, PlayWallet, PlayWalletEntry
from app.modules.commissions.models import AgentCommissionRate, CommissionEntry
from app.modules.declarator.models import DeclarationAudit, EventDeclarator, GhostAutoBet, ResultDeclaration
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction
from app.modules.events.models import Event
from app.modules.licenses.models import DeploymentInstance, LicenseActivation, LicenseKey
from app.modules.matches.models import Match
from app.modules.notifications.models import Notification
from app.modules.predictions.models import Prediction
from app.modules.resources.models import ResourceLedgerEntry, ResourceSettings, ResourceTransaction, ResourceWallet
from app.modules.results.models import MatchResult, MatchResultHistory
from app.modules.simulations.models import (
    SimulationArena,
    SimulationCommand,
    SimulationController,
    SimulationMatch,
    SimulationSettlement,
    SimulationStake,
)
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
from app.modules.video.models import VideoBroadcast, VideoProvider, VideoRecording, VideoStream, VideoStreamOperator
from app.shared.models import Base

__all__ = [
    "AgentCommissionRate",
    "CommissionEntry",
    "ResourceLedgerEntry",
    "ResourceSettings",
    "ResourceTransaction",
    "ResourceWallet",
    "SimulationArena",
    "SimulationCommand",
    "SimulationController",
    "SimulationMatch",
    "SimulationSettlement",
    "SimulationStake",
    "VideoBroadcast",
    "VideoProvider",
    "VideoRecording",
    "VideoStream",
    "VideoStreamOperator",
    "Bet",
    "BettingSettings",
    "FightBook",
    "PlayWallet",
    "PlayWalletEntry",
    "BalanceOperation",
    "AuditLog",
    "AuthSession",
    "Base",
    "DeploymentInstance",
    "Event",
    "DeclarationAudit",
    "EventDeclarator",
    "GhostAutoBet",
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
    "resource_settings",
    "resource_wallets",
    "resource_transactions",
    "resource_ledger_entries",
    "simulation_settlements",
    "simulation_arenas",
    "simulation_controllers",
    "simulation_matches",
    "simulation_stakes",
    "simulation_commands",
    "video_providers",
    "video_streams",
    "video_stream_operators",
    "video_broadcasts",
    "video_recordings",
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
    "ghost_auto_bets",
    "result_declarations",
    "declaration_audit",
    "match_results",
    "match_result_history",
    "predictions",
    "betting_settings",
    "fight_books",
    "bets",
    "agent_commission_rates",
    "commission_entries",
    "play_wallets",
    "play_wallet_entries",
    "balance_operations",
    "point_accounts",
    "point_transactions",
    "point_ledger_entries",
    "notifications",
    "audit_logs",
)
