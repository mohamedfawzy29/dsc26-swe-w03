import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.conftest import auth
from app.models import Account, IdempotencyRecord, Transfer
from app.main import create_app
from app.models import Base
from app.settings import Settings

BODY = {"from_account_id": "acc_dina", "to_account_id": "acc_karim", "amount_piastres": 15000}


@pytest.mark.parametrize("body", [
    {**BODY, "amount_piastres": n} for n in (0, -1, 2_000_001, 150.5, "150", True)
] + [{**BODY, "extra": 1}, {**BODY, "from_account_id": "acc_karim"},
     {**BODY, "to_account_id": "missing"}, {"from_account_id": "acc_dina"}])
def test_invalid_transfer_payloads_are_problems(client, body):
    r = client.post("/v1/transfers", json=body, headers=auth(key="invalid"))
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")


def test_auth_and_missing_key(client):
    for path, method in [("/v1/accounts/acc_dina", "get"), ("/v1/transfers/nope", "get"), ("/v1/transfers", "post")]:
        r = getattr(client, method)(path, json=BODY) if method == "post" else getattr(client, method)(path)
        assert r.status_code == 401 and "www-authenticate" in r.headers
    assert client.post("/v1/transfers", json=BODY, headers={"Authorization": "Bearer tok_dina"}).status_code == 400


def test_replay_and_scope(client):
    first = client.post("/v1/transfers", json=BODY, headers=auth(key="replay"))
    second = client.post("/v1/transfers", json=BODY, headers=auth(key="replay"))
    assert first.status_code == second.status_code == 201 and first.content == second.content
    assert second.headers["idempotent-replayed"] == "true"
    assert client.post("/v1/transfers", json={**BODY, "memo": "different"}, headers=auth(key="replay")).status_code == 422
    assert client.get("/v1/transfers/" + first.json()["id"], headers=auth("tok_karim", None)).status_code == 200
    client.app.state.settings.api_tokens["tok_third"] = "cus_third"
    assert client.get("/v1/transfers/" + first.json()["id"], headers=auth("tok_third", None)).status_code == 404


def test_conflict_is_replayed(client):
    body = {**BODY, "amount_piastres": 100001}
    first = client.post("/v1/transfers", json=body, headers=auth(key="conflict"))
    second = client.post("/v1/transfers", json=body, headers=auth(key="conflict"))
    assert first.status_code == second.status_code == 409
    assert first.content == second.content and second.headers["idempotent-replayed"] == "true"


def test_unowned_is_hidden_and_balances_unchanged(client):
    r = client.post("/v1/transfers", json={**BODY, "from_account_id": "acc_karim", "to_account_id": "acc_dina"}, headers=auth())
    assert r.status_code == 404
    with Session(client.app.state.engine) as s:
        assert s.get(Account, "acc_karim").balance_piastres == 50000


def test_rate_limit_replay_consumes_tokens(client, clock):
    for i in range(5):
        assert client.post("/v1/transfers", json=BODY, headers=auth(key=f"r{i}")).status_code != 429
    limited = client.post("/v1/transfers", json=BODY, headers=auth(key="r5"))
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    assert client.post("/v1/transfers", json=BODY, headers=auth(key=None)).status_code == 429
    other = {"from_account_id": "acc_karim", "to_account_id": "acc_dina", "amount_piastres": 1}
    assert client.post("/v1/transfers", json=other, headers=auth("tok_karim", "independent")).status_code == 201
    clock.advance(1)
    assert client.post("/v1/transfers", json=BODY, headers=auth(key="r6")).status_code != 429


def test_schema_constraints(client):
    with Session(client.app.state.engine) as s, pytest.raises(IntegrityError):
        s.add(Transfer(id="trf_bad", from_account_id="acc_dina", to_account_id="acc_karim", amount_piastres=0, status="completed")); s.commit()
    with Session(client.app.state.engine) as s:
        s.add(IdempotencyRecord(customer_id="c", key="k", request_hash="x", status_code=201, response_body="{}")); s.commit()
    with Session(client.app.state.engine) as s, pytest.raises(IntegrityError):
        s.add(IdempotencyRecord(customer_id="c", key="k", request_hash="x", status_code=201, response_body="{}")); s.commit()


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Token abc", "Bearer", "Bearer unknown"])
def test_rejects_bad_bearer_tokens_everywhere(client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    assert client.get("/v1/accounts/acc_dina", headers=headers).status_code == 401
    assert client.get("/v1/transfers/nope", headers=headers).status_code == 401
    assert client.post("/v1/transfers", json=BODY, headers=headers).status_code == 401


def test_key_is_scoped_per_customer(client):
    one = client.post("/v1/transfers", json=BODY, headers=auth(key="shared"))
    other = {"from_account_id": "acc_karim", "to_account_id": "acc_dina", "amount_piastres": 100}
    two = client.post("/v1/transfers", json=other, headers=auth("tok_karim", "shared"))
    assert one.status_code == two.status_code == 201


def test_exact_balance_is_allowed(client):
    client.app.state.settings.max_transfer_piastres = 100000
    r = client.post("/v1/transfers", json={**BODY, "amount_piastres": 100000}, headers=auth(key="all"))
    assert r.status_code == 201
    assert client.get("/v1/accounts/acc_dina", headers=auth("tok_dina", None)).json()["balance_piastres"] == 0


@hypothesis_settings(max_examples=50, deadline=None)
@given(st.lists(st.tuples(st.integers(min_value=0, max_value=3), st.integers(min_value=1, max_value=100000)), min_size=1, max_size=12))
def test_random_transfers_match_ledger(operations):
    balances = [100000, 30000, 40000, 20000]
    clock = [1000.0]
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:",
                              api_tokens={"dina": "cus_dina", "karim": "cus_karim"},
                              rate_limit_capacity=5, clock=lambda: clock[0]))
    Base.metadata.create_all(app.state.engine)
    with Session(app.state.engine) as session:
        session.add_all([
            Account(id="acc_dina", owner_id="cus_dina", balance_piastres=100000),
            Account(id="acc_karim", owner_id="cus_karim", balance_piastres=30000),
            Account(id="acc_extra_a", owner_id="cus_dina", balance_piastres=40000),
            Account(id="acc_extra_b", owner_id="cus_karim", balance_piastres=20000),
        ])
        session.commit()
    client = TestClient(app)
    ids = ["acc_dina", "acc_karim", "acc_extra_a", "acc_extra_b"]
    for i, (raw_source, raw_amount) in enumerate(operations):
        source = raw_source % 4
        # Source ownership is enforced; issue using Dina's two accounts or Karim's two accounts.
        owner = "tok_dina" if source in (0, 2) else "tok_karim"
        destination = (source + 1) % 4
        amount = raw_amount % (balances[source] + 2)
        amount = max(1, amount)
        clock[0] += 1
        result = client.post("/v1/transfers", json={"from_account_id": ids[source], "to_account_id": ids[destination],
                            "amount_piastres": amount}, headers={"Authorization": f"Bearer {'dina' if owner == 'tok_dina' else 'karim'}",
                                                                  "Idempotency-Key": f"hyp-{i}"})
        if balances[source] >= amount:
            assert result.status_code == 201
            balances[source] -= amount
            balances[destination] += amount
        else:
            assert result.status_code == 409
        with Session(client.app.state.engine) as session:
            actual = [session.get(Account, account_id).balance_piastres for account_id in ids]
        assert actual == balances and sum(actual) == 190000
