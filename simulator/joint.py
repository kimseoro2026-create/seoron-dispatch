"""공동 동행 실험(EXP, PRD 12장·FR-38) — 같은 병원·비슷한 예약 시각의 병원동행 두 건을 한 인력이 함께 모시면 무엇이 바뀌나.

기본은 꺼져 있다(1:1 전담). 이 실험은 조건부이며, 결과를 1:1 기본 성과에 합산하지 않는다. 실제 다인 동행 허용을 확정하지 않는다.

짝이 되는 조건(설정값, config/region.yaml joint_escort) — 하나라도 맞지 않거나 확인되지 않으면 짝에서 뺀다
  - 두 어르신 모두 공동 동행 동의(가상 자료 joint_escort_ok) — 동의가 확인되지 않으면 제외
  - 동시 도움이 필요 없는 이동조건(allowed_mobility, 기본 '자립 보행')
  - 같은 병원, 예약 시각 차이 ≤ max_appointment_gap_min
  - 먼저 탄 분이 더 타는 시간(돌아가는 길 포함 각각) ≤ max_detour_min, 병원에서 기다리는 시간 ≤ max_wait_min
  - 두 분의 픽업 시간창 안에서 함께 출발할 수 있어야 함

두 가지 방식(따로 고른다)
  - simultaneous(병원 내 동시 지원): 인력이 병원에서 두 분을 함께 돕는다. 병원을 나서는 시각 = 두 분 볼일이 모두 끝난 시각.
  - ride_share(이동 공유): 이동만 함께하고 병원에서는 한 분씩 돕는다. 병원 시간 = 두 분 체류의 합(보수적으로).

모델: 짝은 한 노드로 합친다(첫 분 집 → 둘째 분 집 → 병원 → 둘째 분 집 → 첫 분 집, 끝 위치는 첫 분 집).
  - 두 분이 병원에 함께 도착 ≤ 이른 쪽 예약 − 여유. 그래서 출발 시각을 가장 늦은 가능 시각 한 점으로 둔다(점유 시간이 고정되도록).
  - 노드 종류는 'joint_escort'(점유 = service_minutes). 엔진·검증기는 그대로 쓴다. 인력 시간은 한 번만 센다.
  - 어르신별 기다림(돌아가는 길·병원 대기)은 따로 계산해 보고한다.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class JointConfig:
    enabled: bool = False
    mode: str = "simultaneous"             # simultaneous / ride_share
    require_consent: bool = True
    allowed_mobility: list[str] = field(default_factory=lambda: ["independent"])
    max_appointment_gap_min: int = 30
    max_detour_min: int = 20
    max_wait_min: int = 60

    @classmethod
    def from_config(cls, **over) -> "JointConfig":
        cfg = (yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8")) or {}).get("joint_escort") or {}
        keys = {k: cfg[k] for k in ("enabled", "mode", "require_consent", "allowed_mobility", "max_appointment_gap_min",
                                    "max_detour_min", "max_wait_min") if k in cfg}
        keys.update(over)
        return cls(**keys)


def _tr(travel: dict, a: str, b: str) -> float | None:
    v = travel.get((a, b))
    return None if v is None else float(v)


def _pair_plan(ra: dict, rb: dict, travel: dict, cfg: JointConfig) -> dict | None:
    """ra 를 먼저 태우는 순서로 짝 계획을 만든다. 조건이 안 맞으면 None."""
    a, b, h = ra["home_node"], rb["home_node"], ra["hospital_node"]
    d_ab, d_bh, d_hb, d_ba = _tr(travel, a, b), _tr(travel, b, h), _tr(travel, h, b), _tr(travel, b, a)
    d_ah, d_ha = _tr(travel, a, h), _tr(travel, h, a)
    if None in (d_ab, d_bh, d_hb, d_ba, d_ah, d_ha):
        return None
    d_ab, d_bh, d_hb, d_ba = (int(round(x)) for x in (d_ab, d_bh, d_hb, d_ba))
    deadline = min(int(ra["appointment_min"]) - int(ra["arrival_buffer_min"]),
                   int(rb["appointment_min"]) - int(rb["arrival_buffer_min"]))
    t = deadline - d_ab - d_bh                        # 첫 분 픽업(가장 늦은 가능 시각)
    if not (int(ra["earliest_start_min"]) <= t <= int(ra["latest_start_min"])):
        return None
    if not (int(rb["earliest_start_min"]) <= t + d_ab <= int(rb["latest_start_min"])):
        return None
    arrive = t + d_ab + d_bh
    need_end = {r["request_id"]: max(arrive, int(r["appointment_min"]) - int(r["arrival_buffer_min"]))
                + int(r["hospital_stay_min"]) for r in (ra, rb)}
    if cfg.mode == "simultaneous":
        leave = max(need_end.values())
    elif cfg.mode == "ride_share":
        leave = arrive + int(ra["hospital_stay_min"]) + int(rb["hospital_stay_min"])
    else:
        raise ValueError(f"알 수 없는 방식: {cfg.mode}")
    detour_go = d_ab + d_bh - int(round(d_ah))        # 먼저 탄 분이 더 타는 시간(가는 길)
    detour_back = d_hb + d_ba - int(round(d_ha))      # 돌아가는 길(둘째 분을 먼저 내려 드림)
    wait = {rid: leave - end for rid, end in need_end.items()}
    if max(detour_go, detour_back) > cfg.max_detour_min or max(wait.values()) > cfg.max_wait_min:
        return None
    wait[ra["request_id"]] += max(0, detour_go) + max(0, detour_back)
    span = d_ab + d_bh + (leave - arrive) + d_hb + d_ba
    return {"a": ra["request_id"], "b": rb["request_id"], "order": (ra["request_id"], rb["request_id"]),
            "pickup_min": t, "hospital_arrive": arrive, "leave_min": leave, "span": span, "wait": wait,
            "detour": max(0, detour_go) + max(0, detour_back)}


def find_pairs(instance: dict, bens: dict, cfg: JointConfig) -> tuple[list[dict], dict]:
    """조건이 맞는 짝(한 요청은 한 짝에만). → (짝 목록, 제외 사유별 건수)."""
    travel = instance["travel"] if isinstance(instance["travel"], dict) else \
        {(t["from_node"], t["to_node"]): float(t["minutes"]) for t in instance["travel"]}
    esc = [r for r in instance["requests"] if r["service_type"] == "hospital_escort"]
    excluded = {"동의·이동조건": 0, "같은 병원 짝 없음": 0, "시간·경로 조건": 0}
    ok = []
    for r in esc:
        b = bens.get(r["beneficiary_id"]) or {}
        consent = bool(b.get("joint_escort_ok")) if cfg.require_consent else True
        if not consent or b.get("mobility") not in cfg.allowed_mobility:
            excluded["동의·이동조건"] += 1
        else:
            ok.append(r)
    cands = []
    for i, x in enumerate(ok):
        for y in ok[i + 1:]:
            if x["hospital_node"] != y["hospital_node"] or x["beneficiary_id"] == y["beneficiary_id"]:
                continue
            if abs(int(x["appointment_min"]) - int(y["appointment_min"])) > cfg.max_appointment_gap_min:
                continue
            plans = [p for p in (_pair_plan(x, y, travel, cfg), _pair_plan(y, x, travel, cfg)) if p]
            if plans:
                cands.append(min(plans, key=lambda p: (p["detour"], sum(p["wait"].values()), p["a"])))
    # 한 요청은 한 짝에만: 기다림이 적은 짝부터 고른다(결정론적)
    cands.sort(key=lambda p: (sum(p["wait"].values()), p["span"], p["a"], p["b"]))
    used, pairs = set(), []
    for p in cands:
        if p["a"] in used or p["b"] in used:
            continue
        used |= {p["a"], p["b"]}
        pairs.append(p)
    same_hosp = {}
    for r in ok:
        same_hosp.setdefault(r["hospital_node"], []).append(r)
    for r in ok:
        if r["request_id"] in used:
            continue
        if len(same_hosp[r["hospital_node"]]) < 2:
            excluded["같은 병원 짝 없음"] += 1
        else:
            excluded["시간·경로 조건"] += 1
    return pairs, excluded


def build_joint_instance(instance: dict, bens: dict, cfg: JointConfig) -> tuple[dict, list[dict]]:
    """짝을 한 노드로 합친 입력. 꺼져 있으면 입력을 그대로 돌려준다(AT-17)."""
    if not cfg.enabled:
        return instance, []
    pairs, excluded = find_pairs(instance, bens, cfg)
    if not pairs:
        return instance, []
    by_id = {r["request_id"]: r for r in instance["requests"]}
    paired = {x for p in pairs for x in (p["a"], p["b"])}
    reqs = [copy.deepcopy(r) for r in instance["requests"] if r["request_id"] not in paired]
    for p in pairs:
        ra, rb = by_id[p["order"][0]], by_id[p["order"][1]]
        reqs.append({
            "request_id": f"공동-{ra['request_id']}+{rb['request_id']}",
            "beneficiary_id": f"{ra['beneficiary_id']}+{rb['beneficiary_id']}",
            "service_type": "joint_escort", "service_date": ra["service_date"], "home_node": ra["home_node"],
            "earliest_start_min": p["pickup_min"], "latest_start_min": p["pickup_min"],
            "service_minutes": int(p["span"]), "priority": max(int(ra.get("priority") or 1), int(rb.get("priority") or 1)),
            "priority_reason": "공동 동행(실험)", "requested_at": min(str(ra.get("requested_at")), str(rb.get("requested_at"))),
            "required_skills": ["hospital_escort"], "members": [ra["request_id"], rb["request_id"]]})
    return dict(instance, requests=reqs), pairs


def expand_assignments(assignments: list[dict], pairs: list[dict]) -> list[str]:
    """배정 결과 → 실제로 모신 요청 ID(공동 노드는 두 요청으로 펼친다)."""
    members = {f"공동-{p['order'][0]}+{p['order'][1]}": list(p["order"]) for p in pairs}
    out = []
    for a in assignments:
        out += members.get(a["request_id"], [a["request_id"]])
    return out


def run_joint_experiment(data_dir: str | Path, cfg: JointConfig, *, n_days: int = 5, algorithm: str = "nearest",
                         time_limit_sec: int = 5, out_dir: str | Path, store=None, actor: str = "") -> dict:
    """날짜마다 같은 요청으로 1:1 과 공동 동행을 각각 계산해 나란히 기록한다(합치지 않음)."""
    from planner.baseline import nearest, request_order
    from planner.data import load_day, load_travel, service_dates
    from planner.optimize import optimize
    from store.db import engine_version
    from verifier import verify

    d = Path(data_dir)
    run = {"nearest": nearest, "request_order": request_order,
           "optimize": lambda i: optimize(i, time_limit_sec=time_limit_sec)}[algorithm]
    bdf = pd.read_csv(d / "beneficiaries.csv", dtype={"beneficiary_id": str})
    bens = {r.beneficiary_id: {"mobility": r.mobility, "joint_escort_ok": bool(r.joint_escort_ok)} for r in bdf.itertuples()}
    travel = load_travel(d)
    rows = []
    on = JointConfig(**{**asdict(cfg), "enabled": True})
    for day in service_dates(d)[: max(1, n_days)]:
        base = load_day(d, day, travel=travel).as_instance()
        joint, pairs = build_joint_instance(base, bens, on)
        _, excluded = find_pairs(base, bens, on)
        by_id = {r["request_id"]: r for r in base["requests"]}
        for mode, inst, prs in (("1:1", base, []), ("공동 동행", joint, pairs)):
            res = run(inst)
            rep = verify(inst, res.assignments)
            served = expand_assignments(res.assignments, prs)
            pair_ids = {f"공동-{p['order'][0]}+{p['order'][1]}": p for p in prs}
            done_pairs = [pair_ids[a["request_id"]] for a in res.assignments if a["request_id"] in pair_ids]
            waits = [w for p in done_pairs for w in p["wait"].values()]
            rows.append({
                "service_date": day, "mode": mode, "algorithm": algorithm, "requests": len(base["requests"]),
                "escort_requests": sum(1 for r in base["requests"] if r["service_type"] == "hospital_escort"),
                "assigned_requests": len(served),
                "unique_beneficiaries": len({by_id[x]["beneficiary_id"] for x in served}),
                "escort_served": sum(1 for x in served if by_id[x]["service_type"] == "hospital_escort"),
                "worker_minutes": int(sum(rep.work_minutes.values())) if rep.work_minutes else 0,
                "pairs_formed": len(prs), "pairs_assigned": len(done_pairs),
                "avg_extra_wait_min": round(sum(waits) / len(waits), 1) if waits else "",
                "max_extra_wait_min": max(waits) if waits else "",
                "violations": len(rep.violations),
                "excluded_consent_mobility": excluded["동의·이동조건"] if mode != "1:1" else "",
                "excluded_no_partner": excluded["같은 병원 짝 없음"] if mode != "1:1" else "",
                "excluded_time_route": excluded["시간·경로 조건"] if mode != "1:1" else "",
            })
    raw = pd.DataFrame(rows)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_path = out / f"joint-{stamp}.csv"
    raw.to_csv(raw_path, index=False, encoding="utf-8-sig")
    config = {"experiment": "joint_escort", **asdict(on), "n_days": n_days, "algorithm": algorithm,
              "time_limit_sec": time_limit_sec}
    input_hash = hashlib.sha256(b"".join((d / n).read_bytes() for n in ("requests.csv", "beneficiaries.csv"))).hexdigest()[:16]
    meta = {"config": config, "input_hash": input_hash, "engine_version": engine_version(), "created_at": stamp,
            "notes": ["조건부 실험이다. 결과를 1:1 기본 성과에 합산하지 않는다.",
                      "동의·이동조건은 가상 자료 값이다. 운영·보험·정산 조건은 확인되지 않았다(실제 허용을 뜻하지 않음).",
                      "공동 인력시간은 한 번만 세고, 어르신별 추가 기다림(돌아가는 길·병원 대기)은 따로 보고한다."]}
    assumptions_path = out / f"joint-{stamp}-assumptions.json"
    assumptions_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if store is not None:
        store.record_experiment(config=config, input_hash=input_hash, result_path=str(raw_path),
                                assumptions_path=str(assumptions_path), n_rows=len(raw), actor=actor or "담당자")
    return {"raw": raw, "raw_path": raw_path, "assumptions_path": assumptions_path, "meta": meta}
