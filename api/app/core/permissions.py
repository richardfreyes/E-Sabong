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
    SIMULATIONS_MANAGE = "simulations.manage"
    SIMULATIONS_CONTROL = "simulations.control"
    RESOURCES_CONFIGURE = "resources.configure"
    RESOURCES_ISSUE = "resources.issue"
    RESOURCES_CONSUME = "resources.consume"
    RESOURCES_AUDIT = "resources.audit"
    POINTS_ALLOCATE = "points.allocate"
    POINTS_TRANSFER = "points.transfer"
    POINTS_ADJUST = "points.adjust"
    POINTS_VIEW_ALL = "points.view_all"
    POINTS_VIEW_DOWNLINE = "points.view_downline"
    BALANCE_MANAGE = "balance.manage"
    REPORTS_VIEW = "reports.view"
    AUDIT_VIEW = "audit.view"
    SETTINGS_MANAGE = "settings.manage"
    BRANDING_MANAGE = "branding.manage"
    BILLING_VIEW = "billing.view"
    STREAMS_OPERATE = "streams.operate"
    # Loader: view the hierarchy and move already-allocated wallet points. These do not grant
    # users.manage or balance.manage (no edits, resets, uniform grants or unrestricted minting).
    LOADER_VIEW = "loader.view"
    LOADER_GRANT = "loader.grant"
    LOADER_HISTORY = "loader.history"


PERMISSION_DESCRIPTIONS: dict[P, str] = {
    P.RESOURCES_CONFIGURE: "Enable or disable the isolated operational resource ledger",
    P.RESOURCES_ISSUE: "Issue operational resource units directly to an account and reverse usage (admin only)",
    P.RESOURCES_CONSUME: "Record operational resource usage against own balance",
    P.RESOURCES_AUDIT: "Read the tenant operational resource audit ledger (admin only)",
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
    P.SIMULATIONS_MANAGE: "Manage simulated tournament arenas and controller assignments",
    P.SIMULATIONS_CONTROL: "Access only assigned simulated tournament arenas",
    P.POINTS_ALLOCATE: "Retired: points can no longer be issued (grants nothing)",
    P.POINTS_TRANSFER: "Retired: points can no longer be transferred (grants nothing)",
    P.POINTS_ADJUST: "Retired: points can no longer be adjusted or refunded (grants nothing)",
    P.POINTS_VIEW_ALL: "View every balance, the balance ledger and the legacy points archive of the tenant (read-only)",
    P.POINTS_VIEW_DOWNLINE: "View balances and balance history of own downline, and its legacy points archive (read-only)",
    P.BALANCE_MANAGE: "Uniform balance grants and resets, and proposing or reviewing balance corrections (admin only)",
    P.REPORTS_VIEW: "View activity reports",
    P.AUDIT_VIEW: "View tenant audit logs",
    P.SETTINGS_MANAGE: "Manage tenant settings and domains",
    P.BRANDING_MANAGE: "Manage tenant branding",
    P.BILLING_VIEW: "View subscription and invoices",
    P.STREAMS_OPERATE: "Broadcast and record the live streams assigned to the account (Stream Operator dashboard)",
    P.LOADER_VIEW: "View the Loader dashboard and every master agent, agent and player in the tenant",
    P.LOADER_GRANT: (
        "Transfer allocated balance to a master agent, agent or player; "
        "withdraw upline balance into the Loader allocation; move commission into the upline wallet"
    ),
    P.LOADER_HISTORY: "View the Loader's own balance allocation history (read-only)",
}

ALL_PERMISSIONS = frozenset(P)

# The points module is retired (migration 0013). These codes stay valid so existing role rows load,
# but no endpoint checks them any more and the role editor no longer offers them.
RETIRED_PERMISSIONS = frozenset({P.POINTS_ALLOCATE, P.POINTS_TRANSFER, P.POINTS_ADJUST})

_AGENT = frozenset(
    {
        P.RESOURCES_CONSUME,
        P.DASHBOARD_VIEW,
        P.USERS_VIEW_DOWNLINE,
        P.USERS_MANAGE_DOWNLINE,
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
    "declarator": ("Submits results for assigned events", frozenset({P.RESULTS_SUBMIT, P.SIMULATIONS_CONTROL})),
    "player": ("End-user portal access", frozenset({P.RESOURCES_CONSUME})),
    "stream_operator": ("Broadcasts and records assigned live streams only", frozenset({P.STREAMS_OPERATE})),
    "loader": (
        "Manages balance allocation for Master Agents, Agents, and Players.",
        frozenset({P.LOADER_VIEW, P.LOADER_GRANT, P.LOADER_HISTORY}),
    ),
}

# Default system role for each account type.
DEFAULT_ROLE_FOR_TYPE = {
    "admin": "tenant_admin",
    "operator": "operator",
    "declarator": "declarator",
    "master_agent": "master_agent",
    "agent": "agent",
    "player": "player",
    "stream_operator": "stream_operator",
    "loader": "loader",
}

# Platform roles, ordered from most to least privileged.
PLATFORM_ROLE_RANK = {
    PlatformRole.SUPER_ADMIN: 4,
    PlatformRole.ADMIN: 3,
    PlatformRole.BILLING: 2,
    PlatformRole.SUPPORT: 1,
}
