"""배포 서버가 예전 모듈을 붙잡는 문제(11/13) 방지: 바뀐 프로젝트 모듈만 다시 읽는지 확인."""
import os
import sys
import time

from app import _fresh


def test_reload_only_changed_project_module(tmp_path, monkeypatch):
    pkg = tmp_path / "fakepkg_fresh"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    mod_file = pkg / "data.py"
    mod_file.write_text("def load_day(a):\n    return 'old'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, _fresh._STATE_ATTR, {"baseline": time.time(), "loaded": {}}, raising=False)

    import fakepkg_fresh.data as m
    assert m.load_day(1) == "old"
    assert _fresh.reload_stale(tmp_path) == []           # 시작 뒤 바뀌지 않음 → 다시 읽지 않음

    mod_file.write_text("def load_day(a, extra_requests=None):\n    return 'new'\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(mod_file, (future, future))
    sys.dont_write_bytecode = True
    try:
        assert _fresh.reload_stale(tmp_path) == ["fakepkg_fresh.data"]
    finally:
        sys.dont_write_bytecode = False
    assert m.load_day(1, extra_requests=[]) == "new"     # 같은 모듈 객체가 새 코드를 쓴다
    assert _fresh.reload_stale(tmp_path) == []           # 또 바뀌기 전까지 다시 읽지 않음

    for k in [k for k in sys.modules if k.startswith("fakepkg_fresh")]:
        del sys.modules[k]
