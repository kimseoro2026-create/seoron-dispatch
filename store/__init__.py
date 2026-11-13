"""운영 데이터 저장소. 화면 세션 대신 파일 DB(SQLite)에 남겨 새로고침·다른 화면에서도 같은 데이터를 본다."""
from store.db import Store, default_store_path

__all__ = ["Store", "default_store_path"]
