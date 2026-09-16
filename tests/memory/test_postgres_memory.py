from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from memory import postgres


def test_connection_string_preserves_special_characters(monkeypatch):
    monkeypatch.setattr(postgres.settings, "POSTGRES_USER", "app user")
    monkeypatch.setattr(postgres.settings, "POSTGRES_PASSWORD", SecretStr("p@ss word"))
    monkeypatch.setattr(postgres.settings, "POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setattr(postgres.settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(postgres.settings, "POSTGRES_DB", "agent service")

    parsed = conninfo_to_dict(postgres.get_postgres_connection_string())

    assert parsed["user"] == "app user"
    assert parsed["password"] == "p@ss word"
    assert parsed["dbname"] == "agent service"
