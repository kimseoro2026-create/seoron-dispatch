"""배포 서버가 예전 코드를 붙잡고 있지 않도록, 바뀐 프로젝트 모듈을 다시 읽는다.

Streamlit Cloud 는 git push 뒤 화면 파일(app/*.py)은 다시 실행하지만, 이미 import 한
planner·intake 같은 모듈은 프로세스를 다시 켜기 전까지 예전 것을 쓴다.
그러면 새 화면이 옛 함수를 불러 '알 수 없는 인자' 오류가 난다(11/13 배포에서 실제로 발생).

판단 기준: 파일 수정 시각이 '프로세스 시작 시각' 또는 '마지막으로 다시 읽은 시각'보다 늦으면 다시 읽는다.
한 번 다시 읽은 파일은 또 바뀌기 전까지 다시 읽지 않는다.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

_STATE_ATTR = "_seoron_fresh_state"   # sys 에 붙여 화면 재실행 사이에도 유지한다


def _process_start() -> float:
    """프로세스 시작 시각(리눅스). 알 수 없으면 지금 시각 — 그 뒤 바뀐 파일만 다시 읽는다."""
    try:
        return os.stat(f"/proc/{os.getpid()}").st_ctime
    except OSError:
        return time.time()


def _state() -> dict:
    st = getattr(sys, _STATE_ATTR, None)
    if st is None:
        st = {"baseline": _process_start(), "loaded": {}}
        setattr(sys, _STATE_ATTR, st)
    return st


def project_modules(root: Path) -> list[str]:
    """root 아래 파일에서 온 모듈 이름(가상환경·화면 파일 제외), 이름순."""
    root = root.resolve()
    out = []
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f or name == "__main__" or name == "app" or name.startswith("app."):
            continue
        try:
            p = Path(f).resolve()
        except OSError:
            continue
        if root not in p.parents:
            continue
        rel = p.relative_to(root).parts
        if rel and rel[0] in {".venv", "venv", "site-packages"}:
            continue
        out.append(name)
    return sorted(out)


def reload_stale(root: Path) -> list[str]:
    """바뀐 프로젝트 모듈을 다시 읽고, 다시 읽은 모듈 이름을 돌려준다."""
    state = _state()
    reloaded = []
    for name in project_modules(root):
        mod = sys.modules.get(name)
        try:
            mtime = Path(mod.__file__).stat().st_mtime
        except (OSError, AttributeError, TypeError):
            continue
        last = state["loaded"].get(name, state["baseline"])
        if mtime > last:
            try:
                importlib.reload(mod)
            except Exception:   # 다시 읽기 실패는 화면을 막지 않는다(예전 모듈 그대로 사용)
                continue
            state["loaded"][name] = mtime
            reloaded.append(name)
    return reloaded
