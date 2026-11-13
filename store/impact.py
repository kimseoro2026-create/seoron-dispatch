"""확정 일정 점검 — 인력 설정·근무가 바뀐 뒤 이미 확정된 일정이 새 조건과 맞는지 검증기로 다시 본다.

확정 일정을 조용히 바꾸지 않는다. 맞지 않는 건을 보여주고, 담당자가 '취소·지연 대응'이나 '확정 해제'로 처리한다.
"""
from __future__ import annotations

from pathlib import Path


def confirmed_conflicts(store, data_dir: str | Path, dates: list[str]) -> list[dict]:
    """→ [{service_date, request_id, worker_id, message}] (검증기 위반 그대로)."""
    from planner.data import load_day
    from verifier import verify

    out = []
    for d in dates:
        conf = store.confirmed(d)
        if not conf:
            continue
        inst = load_day(data_dir, d, **store.day_inputs(d))
        rep = verify(inst.as_instance(), [{k: c[k] for k in ("request_id", "worker_id", "arrive_min")} for c in conf])
        for v in rep.violations:
            vd = v.as_dict()
            out.append({"service_date": d, "request_id": vd.get("request_id"), "worker_id": vd.get("worker_id"),
                        "code": vd["code"], "message": vd.get("detail") or vd["code"]})
    return out
