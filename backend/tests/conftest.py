"""Integration tests for the retention-plan workflow.

A real PostgreSQL is required because mount/detach/reconcile use temporary
partitions. Point ``DB_HOST``/``DB_PORT``/``DB_USER``/``DB_PASSWORD`` at a
throwaway database (``DB_NAME`` defaults to ``workflow``). Every test gets its
own schema, so existing data is never touched.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('AUTH_KEY', 'test-key')
os.environ.setdefault('DB_DRIVER', 'postgresql+psycopg')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')


def _base_url():
    return (f"{os.getenv('DB_DRIVER', 'postgresql+psycopg')}://"
            f"{os.getenv('DB_USER', 'app')}:{os.getenv('DB_PASSWORD', 'local-db-demo')}@"
            f"{os.getenv('DB_HOST', 'db')}:{os.getenv('DB_PORT', '5432')}/"
            f"{os.getenv('DB_NAME', 'workflow')}")


def _reachable():
    try:
        probe = create_engine(_base_url(), connect_args={'connect_timeout': 3})
        with probe.connect() as conn:
            conn.execute(text('SELECT 1'))
        probe.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason='PostgreSQL test database unavailable')

ENGINE_OWNERS = ('src.config.settings', 'src.controllers.workflow', 'src.routes.workflow',
                 'src.services.plans', 'src.services.workflow', 'src.services.security',
                 'src.main', 'scripts.seed_demo')


@pytest.fixture()
def app_env(monkeypatch, tmp_path):
    """Fresh schema, isolated engine, seeded accounts and one sample case."""
    import importlib

    from fastapi.testclient import TestClient

    from src.models.tables import users
    from src.services.security import hash_password

    namespace = f'test_{uuid.uuid4().hex[:12]}'
    engine = create_engine(_base_url(), connect_args={'options': f'-csearch_path={namespace}'})
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {namespace}'))
    modules = [importlib.import_module(name) for name in ENGINE_OWNERS]
    for module in modules:
        if hasattr(module, 'engine'):
            monkeypatch.setattr(module, 'engine', engine)
    monkeypatch.setenv('SEED_DEMO', 'false')

    import src.main as main
    with TestClient(main.app) as client:  # lifespan creates tables
        with engine.begin() as conn:
            for role in ('operator', 'reviewer', 'viewer'):
                conn.execute(users.insert().values(username=role, role=role,
                                                   password_hash=hash_password('pw')))
        yield {'client': client, 'engine': engine}
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {namespace} CASCADE'))
    engine.dispose()


def login(client, username):
    response = client.post('/login', json={'username': username, 'password': 'pw'})
    assert response.status_code == 200, response.text
    return {'Authorization': f'Bearer {response.json()["token"]}'}
