import pytest

from lucentapi.config import Settings
from lucentapi.devtokens import DevSigner


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_missing_token(client):
    response = client.get("/v1/people")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]


def test_valid_token(client, as_user):
    response = client.get("/v1/me", headers=as_user("xx121"))
    assert response.status_code == 200
    assert response.json()["person"]["surname"] == "Ripley"
    assert response.json()["person"]["position"] == "Qualitätssicherung"
    assert response.json()["roles"] == ["analyst"]


def test_roles_come_from_lucentbase_not_from_the_token(client, signer):
    # A token that claims roles gets the ones lucentBASE grants (Ripley: analyst).
    token = signer.token("xx121", roles=["admin", "reviewer"])
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["roles"] == ["analyst"]
    response = client.post("/v1/raw-values/1/accept", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "missing_role"


@pytest.mark.parametrize("change, code", [
    ({"lifetime": -120}, "token_expired"),
    ({"audience": "someone-else"}, "invalid_token"),
    ({"issuer": "https://evil.example"}, "invalid_token"),
])
def test_rejected_tokens(client, signer, change, code):
    token = signer.token("grindel", **change)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == code
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_token_from_another_key(client, tmp_path):
    stranger = DevSigner(tmp_path)  # same kid "dev", different key
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {stranger.token('grindel')}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_garbage_token(client):
    response = client.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_unknown_person(client, as_user):
    response = client.get("/v1/me", headers=as_user("mallory"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unknown_person"


def test_missing_role(client, as_user):
    body = {"matrix_id": 3, "collection_class": "spot sample", "spot_id": 6}
    response = client.post("/v1/collections", json=body, headers=as_user("jd"))  # an analyst
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "missing_role"


def test_admin_has_every_role(client, as_user):
    body = {"matrix_id": 3, "collection_class": "spot sample", "spot_id": 6}
    assert client.post("/v1/collections", json=body, headers=as_user("grindel", "admin")).status_code == 201


def test_unknown_setting_in_the_environment_is_refused(monkeypatch):
    monkeypatch.setenv("LUCENT_ROLES_CLAIMS", "realm_access.roles")
    with pytest.raises(ValueError, match="LUCENT_ROLES_CLAIMS"):
        Settings(database_url="sqlite://", oidc_issuer="i", oidc_audience="a", _env_file=None)


def test_the_old_role_settings_are_refused(monkeypatch):
    monkeypatch.setenv("LUCENT_ROLES_CLAIM", "realm_access.roles")
    with pytest.raises(ValueError, match="LUCENT_ROLES_CLAIM"):
        Settings(database_url="sqlite://", oidc_issuer="i", oidc_audience="a", _env_file=None)
