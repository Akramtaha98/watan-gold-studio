"""Every test gets an isolated persistent backend; never use a curator's real DB."""
import pytest


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "studio.db"}')
    monkeypatch.setenv('RELEASES_DIR',str(tmp_path/'releases'))
    for key in ('STUDIO_ACCESS_TOKEN','STUDIO_ENV','PTSL_API_TOKEN','PTSL_WEBHOOK_URL','PTSL_WEBHOOK_SECRET','PTSL_ALLOWED_ORIGINS'):
        monkeypatch.delenv(key,raising=False)
