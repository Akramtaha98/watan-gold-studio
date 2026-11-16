"""Every test gets an isolated persistent backend; never use a curator's real DB."""
import os
import tempfile

import pytest

# studio_api opens its SQLite file at import time; point it at a throwaway directory first so that
# running the suite can never touch (or deactivate the batch in) a live ./studio_state.db.
os.environ['STUDIO_DB'] = os.path.join(tempfile.mkdtemp(prefix='studio-test-'), 'studio_state.db')


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "studio.db"}')
    monkeypatch.setenv('RELEASES_DIR',str(tmp_path/'releases'))
    monkeypatch.setenv('STUDIO_DB',str(tmp_path/'studio_state.db'))
    for key in ('STUDIO_ACCESS_TOKEN','STUDIO_ENV','PTSL_API_TOKEN','PTSL_WEBHOOK_URL','PTSL_WEBHOOK_SECRET','PTSL_ALLOWED_ORIGINS','PTSL_REVIEW_TOKEN','STUDIO_PORTAL_ONLY'):
        monkeypatch.delenv(key,raising=False)
