"""Permission catalogue and system role definitions.

Permissions are code-defined so that endpoints can depend on them statically;
tenants compose them into roles. System roles are created during provisioning.
"""

from enum import StrEnum

from app.modules.auth.models import PlatformRole


class P(StrEnum):
    DASHBOARD_VIEW = "dashboard.view"
    USERS_VIEW_ALL = "users.view_all"
    USERS_VIEW_DOWNLINE = "users.view_downline"
    USERS_MANAGE = "users.manage"
    USERS_MANAGE_DOWNLINE = "users.manage_downline"
    ROLES_MANAGE = "roles.manage"
    EVENTS_VIEW = "events.view"
    EVENTS_MANAGE = "events.manage"
    MATCHES_MANAGE = "matches.manage"
    RESULTS_SUBMIT = "results.submit"
    RESULTS_APPROVE = "results.approve"
    RESULTS_DECLARE = "results.declare"
    RESULTS_CORRECT = "results.correct"
    POINTS_ALLOCATE = "points.allocate"
    POINTS_TRANSFER = "points.transfer"
    POINTS_ADJUST = "points.adjust"
    POINTS_VIEW_ALL = "points.view_all"
    POINTS_VIEW_DOWNLINE = "points.view_downline"
    REPORTS_VIEW = "reports.view"
    AUDIT_VIEW = "audit.view"
    SETTINGS_MANAGE = "settings.manage"
    BRANDING_MANAGE = "branding.manage"
    BILLING_VIEW = "billing.view"


PERMISSION_DESCRIPTIONS: dict[P, str] = {
    P.DASHBOARD_VIEW: "View the administration dashboard",
    P.USERS_VIEW_ALL: "View every account in the tenant",
    P.USERS_VIEW_DOWNLINE: "View accounts in own downline",
    P.USERS_MANAGE: "Create, edit and suspend any account",
    P.USERS_MANAGE_DOWNLINE: "Create and edit accounts in own downline",
    P.ROLES_MANAGE: "Manage roles and permissions",
    P.EVENTS_VIEW: "View events and matches in administration",
    P.EVENTS_MANAGE: "Create and run events",
    P.MATCHES_MANAGE: "Create and run matches",
    P.RESULTS_SUBMIT: "Submit declarations for assigned events",
    P.RESULTS_APPROVE: "Review and finalize result declarations",
    P.RESULTS_DECLARE: "Declare official match results",
    P.RESULTS_CORRECT: "Correct declared results",
    P.POINTS_ALLOCATE: "Issue demonstration points to accounts",
    P.POINTS_TRANSFER: "Transfer own demonstration points to downline",
    P.POINTS_ADJUST: "Make administrative point adjustments",
    P.POINTS_VIEW_ALL: "View all point accounts and transactions",
    P.POINTS_VIEW_DOWNLINE: "View downline point accounts and transactions",
    P.REPORTS_VIEW: "View activity reports",
    P.AUDIT_VIEW: "View tenant audit logs",
    P.SETTINGS_MANAGE: "Manage tenant settings and domains",
    P.BRANDING_MANAGE: "Manage tenant branding",
    P.BILLING_VIEW: "View subscription and invoices",
}

ALL_PERMISSIONS = frozenset(P)

_AGENT = frozenset(
    {
        P.DASHBOARD_VIEW,
        P.USERS_VIEW_DOWNLINE,
        P.USERS_MANAGE_DOWNLINE,
        P.POINTS_TRANSFER,
        P.POINTS_VIEW_DOWNLINE,
        P.REPORTS_VIEW,
    }
)

SYSTEM_ROLES: dict[str, tuple[str, frozenset[P]]] = {
    "tenant_admin": ("Full control of the tenant", ALL_PERMISSIONS),
    "operator": (
        "Runs events, matches and results",
        frozenset(
            {
                P.DASHBOARD_VIEW,
                P.EVENTS_VIEW,
                P.EVENTS_MANAGE,
                P.MATCHES_MANAGE,
                P.RESULTS_DECLARE,
                P.REPORTS_VIEW,
            }
        ),
    ),
    "master_agent": ("Manages a network of agents and players", _AGENT),
    "agent": ("Manages players", _AGENT),
    "declarator": ("Submits results for assigned events", frozenset({P.RESULTS_SUBMIT})),
    "player": ("End-user portal access", frozenset()),
}

# Default system role for each account type.
DEFAULT_ROLE_FOR_TYPE = {
    "admin": "tenant_admin",
    "operator": "operator",
    "declarator": "declarator",
    "master_agent": "master_agent",
    "agent": "agent",
    "player": "player",
}

# Platform roles, ordered from most to least privileged.
PLATFORM_ROLE_RANK = {
    PlatformRole.SUPER_ADMIN: 4,
    PlatformRole.ADMIN: 3,
    PlatformRole.BILLING: 2,
    PlatformRole.SUPPORT: 1,
}
