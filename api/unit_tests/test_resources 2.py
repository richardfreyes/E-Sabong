"""Pure contract/isolation checks; no database or external services."""

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.core.permissions import SYSTEM_ROLES, P
from app.modules.auth.models import AccountStatus
from app.modules.resources.models import ResourceAction, ResourceLedgerEntry, ResourceTransaction, ResourceWallet
from app.modules.resources.schemas import IssuanceRequest, SettingsRequest, UsageRequest
from app.modules.resources.service import authorize, targets_query
from app.modules.users.models import AccountType

MODULE = Path(__file__).resolve().parents[1] / "app/modules/resources"


def test_no_game_or_legacy_dependencies():
    forbidden = {"betting", "demo_points", "simulations", "matches", "results", "predictions"}
    for path in MODULE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert forbidden.isdisjoint((node.module or "").split(".")), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert forbidden.isdisjoint(alias.name.split(".")), path
    for model in (ResourceWallet, ResourceTransaction, ResourceLedgerEntry):
        for fk in model.__table__.foreign_keys:
            assert fk.target_fullname.split(".")[0] in {
                "resource_wallets",
                "resource_transactions",
                "tenant_users",
                "tenants",
            }


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, "10", 1_000_000_001])
def test_units_are_bounded_whole_integers(amount):
    with pytest.raises(ValidationError):
        UsageRequest(resource_kind="compute", amount=amount, idempotency_key="key", reason="Usage")


@pytest.mark.parametrize(
    "extra", [{"tenant_id": str(uuid.uuid4())}, {"source_wallet_id": str(uuid.uuid4())}, {"currency": "PHP"}]
)
def test_caller_cannot_override_scope_or_add_currency(extra):
    with pytest.raises(ValidationError):
        IssuanceRequest(
            resource_kind="compute", amount=1, idempotency_key="key", reason="Usage", target_user_id=uuid.uuid4(), **extra
        )


@pytest.mark.parametrize("field,value", [("reason", " "), ("idempotency_key", ""), ("resource_kind", "game")])
def test_required_contract_fields(field, value):
    payload = dict(resource_kind="compute", amount=1, idempotency_key="key", reason="Usage")
    payload[field] = value
    with pytest.raises(ValidationError):
        UsageRequest(**payload)


def test_settings_requires_real_boolean():
    with pytest.raises(ValidationError):
        SettingsRequest(enabled="true")


@pytest.mark.parametrize(
    "tier", [AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.PLAYER, AccountType.OPERATOR, AccountType.DECLARATOR]
)
def test_custom_issue_permission_never_lets_a_non_admin_provision(tier):
    tid = uuid.uuid4()
    current = CurrentUser(
        user=SimpleNamespace(id=uuid.uuid4(), tenant_id=tid, status=AccountStatus.ACTIVE, deleted_at=None, account_type=tier),
        permissions=frozenset({P.RESOURCES_ISSUE}),
        tenant=SimpleNamespace(id=tid, is_operational=True),
        session_family=uuid.uuid4(),
    )
    with pytest.raises(Forbidden):
        targets_query(current)
    with pytest.raises(Forbidden):
        authorize(current, P.RESOURCES_ISSUE, admin=True)


def test_no_allocation_action_or_permission_exists():
    assert {a.value for a in ResourceAction} == {"issue", "consume", "refund"}
    assert not [
        p
        for p in P
        if p.value.startswith("resources.")
        and p.value not in {"resources.configure", "resources.issue", "resources.consume", "resources.audit"}
    ]


def test_default_roles_have_separate_resource_permissions():
    assert P.RESOURCES_ISSUE in SYSTEM_ROLES["tenant_admin"][1]
    for role in ("master_agent", "agent"):
        resource_perms = {p for p in SYSTEM_ROLES[role][1] if p.value.startswith("resources.")}
        assert resource_perms == {P.RESOURCES_CONSUME}
    assert SYSTEM_ROLES["player"][1] == frozenset({P.RESOURCES_CONSUME})
