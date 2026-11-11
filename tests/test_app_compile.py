"""화면 파일이 문법 오류 없이 읽히는지(배포 화면이 깨지는 것을 커밋 전에 잡는다)."""
import py_compile
from pathlib import Path

import pytest


@pytest.mark.parametrize("path", sorted(Path("app").glob("*.py")), ids=lambda p: p.name)
def test_app_file_compiles(path):
    py_compile.compile(str(path), doraise=True)
