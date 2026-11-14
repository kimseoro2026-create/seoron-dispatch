"""효과 검증 실험실(W09) — 화면에서 실험 가정을 바꿔 여러 날·여러 시드로 돌리고, 결과와 가정을 함께 저장한다.

PRD FR-36(인력 20명·1개월 기본 프리셋, 시드·취소율·요청량(부하율)·이동·병원 체류 배율),
FR-37(시드별 비교, 평균·범위·실패 횟수, 입력 가정 내려받기), 12장(미래 정보 금지: 취소는 통보 시각에만 반영 — 재생 엔진 그대로).

가정은 모두 결과 파일 옆 JSON 에 남긴다. 기본값(배율 1, 취소율 '자료 그대로')이면 입력을 바꾸지 않는다.
같은 자원 효과만 다룬다(인력 추가 효과는 '부족 자원·비용 리포트'에서 따로 본다 — 섞지 않는다).
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

ALGO_KO = {"request_order": "신청순", "nearest": "근거리", "optimize": "AI 최적화",
           "request_order_replay": "신청순+취소 재배정", "nearest_replay": "근거리+취소 재배정",
           "request_order_cancel": "신청순+취소 반영", "nearest_cancel": "근거리+취소 반영"}
NOTICE_CHOICES = (30, 60, 120, 240)     # 취소율을 바꿀 때 쓰는 '통보 몇 분 전' 후보(가정)


@dataclass
class LabConfig:
    n_days: int = 5
    seeds: list[int] = field(default_factory=lambda: [42])
    rhos: list[float] = field(default_factory=lambda: [0.9])
    algorithms: list[str] = field(default_factory=lambda: ["request_order", "nearest", "optimize"])
    time_limit_sec: int = 5
    travel_factor: float = 1.0
    stay_factor: float = 1.0
    cancel_rate: float | None = None      # None = 가상 자료의 취소 그대로
    label: str = ""


def apply_assumptions(instance: dict, cfg: LabConfig, seed: int) -> dict:
    """하루 입력에 실험 가정을 적용한다. 기본값이면 같은 객체를 그대로 돌려준다."""
    if cfg.travel_factor == 1.0 and cfg.stay_factor == 1.0 and cfg.cancel_rate is None:
        return instance
    inst = copy.deepcopy(instance)
    if cfg.travel_factor != 1.0:
        tr = inst["travel"]
        if isinstance(tr, dict):
            inst["travel"] = {k: round(float(v) * cfg.travel_factor, 1) for k, v in tr.items()}
        else:
            inst["travel"] = [dict(t, minutes=round(float(t["minutes"]) * cfg.travel_factor, 1)) for t in tr]
    if cfg.stay_factor != 1.0:
        for r in inst["requests"]:
            if r["service_type"] == "hospital_escort" and r.get("hospital_stay_min") is not None:
                r["hospital_stay_min"] = int(round(int(r["hospital_stay_min"]) * cfg.stay_factor))
    if cfg.cancel_rate is not None:
        rng = random.Random(seed * 1009 + 17)
        for r in sorted(inst["requests"], key=lambda q: q["request_id"]):
            hit = rng.random() < cfg.cancel_rate
            r["cancelled_same_day"] = hit
            r["cancel_notice_min_before"] = rng.choice(NOTICE_CHOICES) if hit else None
    return inst


def _failed(row) -> bool:
    return (row["violations"] > 0 or str(row["status"]) not in ("ok", "optimal", "feasible")
            or (row["assigned"] == 0 and row["requests"] > 0))


def seed_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (시드별 표, 방식×부하율 요약: 평균·최소·최대·실패 횟수)."""
    d = df.copy()
    d["failed"] = d.apply(_failed, axis=1)
    per_seed = (d.groupby(["target_rho", "algorithm", "seed"])
                .agg(days=("service_date", "nunique"), unique=("unique_beneficiaries", "mean"),
                     on_time=("on_time_rate", "mean"), travel=("travel_minutes", "mean"), failed=("failed", "sum"))
                .reset_index())
    per_seed_out = pd.DataFrame({
        "부하율": per_seed["target_rho"], "방식": per_seed["algorithm"].map(lambda a: ALGO_KO.get(a, a)),
        "시드": per_seed["seed"], "날짜 수": per_seed["days"],
        "고유 인원(하루 평균)": per_seed["unique"].round(2), "적기 배정률": per_seed["on_time"].round(4),
        "이동(분, 하루 평균)": per_seed["travel"].round(1), "실패": per_seed["failed"].astype(int)})
    g = per_seed.groupby(["target_rho", "algorithm"])
    summ = pd.DataFrame({
        "고유 인원 평균": g["unique"].mean().round(2), "고유 인원 최소": g["unique"].min().round(2),
        "고유 인원 최대": g["unique"].max().round(2), "적기 배정률 평균": g["on_time"].mean().round(4),
        "이동 평균(분)": g["travel"].mean().round(1), "시드 수": g.size(),
        "실패 횟수": g["failed"].sum().astype(int)}).reset_index()
    summ.insert(0, "방식", summ.pop("algorithm").map(lambda a: ALGO_KO.get(a, a)))
    summ.insert(0, "부하율", summ.pop("target_rho"))
    return per_seed_out, summ


def run_lab(cfg: LabConfig, data_dir: str | Path, *, out_dir: str | Path, store=None, actor: str = "",
            progress=None) -> dict:
    """실험을 돌리고 원본·요약·가정을 저장한다. store 가 있으면 실험 기록(experiment_runs)도 남긴다."""
    from planner.data import service_dates
    from simulator.experiment import run_experiment
    from store.db import engine_version

    d = Path(data_dir)
    dates = service_dates(d)[: max(1, int(cfg.n_days))]
    raw = run_experiment(d, algorithms=cfg.algorithms, load_factors=cfg.rhos, seeds=cfg.seeds, dates=dates,
                         time_limit_sec=cfg.time_limit_sec, progress=progress,
                         transform=None if (cfg.travel_factor == 1.0 and cfg.stay_factor == 1.0
                                            and cfg.cancel_rate is None) else _Transform(cfg))
    per_seed, summ = seed_summary(raw)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 이름표(label)가 있으면 파일 이름에 넣는다 — 여러 실험을 동시에 돌려도 이름이 겹치지 않게
    tag = f"lab-{cfg.label}-{stamp}" if cfg.label else f"lab-{stamp}"
    raw_path = out / f"{tag}.csv"
    raw.to_csv(raw_path, index=False, encoding="utf-8-sig")
    input_hash = hashlib.sha256(b"".join((d / n).read_bytes() for n in ("requests.csv", "workers.csv"))).hexdigest()[:16]
    meta = {"config": asdict(cfg), "dates": dates, "input_hash": input_hash, "engine_version": engine_version(),
            "dataset": "가상 권역(인력 20명·평일 30일) — data/processed", "created_at": stamp,
            "notes": ["취소·지연은 통보 시각에만 반영한다(미래 정보 금지).",
                      f"취소율을 바꾸면 통보 시각은 {list(NOTICE_CHOICES)}분 전 중 하나로 정한다(가정)." if cfg.cancel_rate
                      is not None else "취소는 가상 자료에 있는 그대로 쓴다.",
                      "인력 추가 효과는 이 실험에 섞지 않는다."]}
    assumptions_path = out / f"{tag}-assumptions.json"
    assumptions_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if store is not None:
        store.record_experiment(config=asdict(cfg), input_hash=input_hash, result_path=str(raw_path),
                                assumptions_path=str(assumptions_path), n_rows=len(raw), actor=actor or "담당자")
    return {"raw": raw, "per_seed": per_seed, "summary": summ, "raw_path": raw_path,
            "assumptions_path": assumptions_path, "meta": meta}


class _Transform:
    """run_experiment 의 transform 은 시드를 받지 않아, 서비스일·요청으로 시드를 정하는 대신 호출 순서별 시드를 쓴다."""

    def __init__(self, cfg: LabConfig):
        self.cfg = cfg

    def __call__(self, inst: dict) -> dict:
        # 같은 날·같은 요청 집합이면 같은 결과가 나오도록 요청 ID 로 시드를 만든다(재현 가능)
        key = inst.get("service_date", "") + "|" + ",".join(sorted(r["request_id"] for r in inst["requests"]))
        seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        return apply_assumptions(inst, self.cfg, seed)
