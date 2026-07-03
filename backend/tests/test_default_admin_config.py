from app.config import Settings


def test_default_admin_credentials(monkeypatch):
    monkeypatch.delenv("DEFAULT_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("DEFAULT_ADMIN_PASSWORD", raising=False)

    settings = Settings(_env_file=None)

    assert settings.default_admin_username == "admin"
    assert settings.default_admin_password == "admin"
