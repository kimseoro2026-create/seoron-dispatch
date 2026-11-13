"""모든 테스트가 각자 임시 저장소(SQLite)를 쓰게 한다. 실제 data/store/seoron.db 를 건드리지 않는다."""
import pytest


@pytest.fixture(autouse=True)
def _temp_store(tmp_path, monkeypatch):
    path = tmp_path / "store.db"
    monkeypatch.setenv("SEORON_DB_PATH", str(path))
    return path
