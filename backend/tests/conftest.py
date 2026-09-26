import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from tests.batch_contract import BatchContract


@pytest.fixture()
def contract():
    """内存库 + 断言辅助；每个场景结束后恢复成种子三批。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不进入 lifespan（避免连生产库）；重复批次号会触发 IntegrityError -> 500
    client = TestClient(app, raise_server_exceptions=False)
    helper = BatchContract(client, session_factory)
    helper.reset_to_seed()
    try:
        yield helper
    finally:
        helper.reset_to_seed()
        app.dependency_overrides.clear()
        engine.dispose()
