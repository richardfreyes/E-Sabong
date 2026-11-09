"""The Loader role is a system role and does not inherit administrator powers."""

from app.core.permissions import SYSTEM_ROLES, P


def test_loader_permissions_are_only_the_dedicated_ones() -> None:
    description, perms = SYSTEM_ROLES["loader"]
    assert description == "Manages balance allocation for Master Agents, Agents, and Players."
    assert perms == frozenset({P.LOADER_VIEW, P.LOADER_GRANT, P.LOADER_HISTORY})
    assert P.USERS_MANAGE not in perms
    assert P.BALANCE_MANAGE not in perms
    assert P.ROLES_MANAGE not in perms
    assert P.EVENTS_MANAGE not in perms
    assert P.SETTINGS_MANAGE not in perms
    holders = [name for name, (_, role_perms) in SYSTEM_ROLES.items() if name != "tenant_admin" and P.LOADER_GRANT in role_perms]
    assert holders == ["loader"]
