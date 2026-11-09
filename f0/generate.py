"""가상 구 생성기 (F0).

입력: 병원 표, 행정동별 65세 이상 인구 표, 행정동 경계
출력: beneficiaries, workers, hospitals, requests 표
같은 seed와 같은 입력이면 항상 같은 결과가 나온다.

이름은 CLAUDE.md 5절 데이터 계약을 따른다:
대상자 beneficiary, 인력 worker, 서비스 hospital_escort / household_support.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .geo import estimated_drive_minutes, haversine_km, random_points_in

ESCORT = "hospital_escort"
HOMECARE = "household_support"


@dataclass
class World:
    beneficiaries: pd.DataFrame
    workers: pd.DataFrame
    hospitals: pd.DataFrame
    requests: pd.DataFrame


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _beneficiaries(sim, boundaries, population, rng) -> pd.DataFrame:
    pop = population[population["dong"].isin(boundaries.keys())].copy()
    if pop.empty:
        raise ValueError("인구 표의 행정동명이 경계 파일의 행정동명과 하나도 맞지 않습니다")
    weights = pop["age_65_plus"] / pop["age_65_plus"].sum()
    counts = rng.multinomial(sim["beneficiaries"], weights.to_numpy())
    mob = sim["mobility_mix"]
    rows, k = [], 1
    for dong, n in zip(pop["dong"], counts):
        for lat, lon in random_points_in(boundaries[dong], int(n), rng):
            mobility = rng.choice(list(mob.keys()), p=list(mob.values()))
            joint_ok = bool(mobility == "independent" and rng.random() < sim["joint_consent_rate"])
            rows.append({"beneficiary_id": f"B{k:04d}", "dong": dong, "lat": lat, "lon": lon,
                         "mobility": mobility, "joint_escort_ok": joint_ok})
            k += 1
    return pd.DataFrame(rows)


def _workers(sim, boundaries, rng) -> pd.DataFrame:
    union = None
    for g in boundaries.values():
        union = g if union is None else union.union(g)
    shift = sim["shift"]
    rows, k = [], 1
    for role, n in sim["workers"].items():
        for lat, lon in random_points_in(union, n, rng):
            skills = f"{ESCORT},{HOMECARE}" if role == ESCORT else HOMECARE
            rows.append({"worker_id": f"W{k:02d}", "role": role, "skills": skills,
                         "home_lat": lat, "home_lon": lon,
                         "shift_start_min": _hm(shift["start"]), "shift_end_min": _hm(shift["end"]),
                         "break_start_min": _hm(shift["break_start"]), "break_minutes": shift["break_minutes"],
                         "max_work_minutes": shift["max_work_minutes"]})
            k += 1
    return pd.DataFrame(rows)


def _assign_primary_worker(beneficiaries: pd.DataFrame, workers: pd.DataFrame) -> pd.Series:
    """담당 연속성용 주 담당자: 가장 가까운 가사 인력, 1인당 상한을 두어 고르게."""
    hc = workers[workers["skills"].str.contains(HOMECARE)]
    cap = math.ceil(len(beneficiaries) / len(hc))
    load = {s: 0 for s in hc["worker_id"]}
    out = {}
    for _, c in beneficiaries.iterrows():
        dists = sorted(((haversine_km(c.lat, c.lon, s.home_lat, s.home_lon), s.worker_id)
                        for s in hc.itertuples()))
        for _, sid in dists:
            if load[sid] < cap:
                out[c.beneficiary_id] = sid
                load[sid] += 1
                break
    return beneficiaries["beneficiary_id"].map(out)


def _service_days(sim) -> list[date]:
    start = datetime.strptime(sim["start_date"], "%Y-%m-%d").date()
    days, d = [], start
    while len(days) < sim["days"]:
        if not sim.get("weekdays_only") or d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _pick_hospital(beneficiary, hospitals, sim, rng) -> pd.Series:
    w_cls = hospitals["class_name"].map(sim["hospital_class_weights"]).fillna(0.5).to_numpy()
    dist = np.array([haversine_km(beneficiary.lat, beneficiary.lon, h.lat, h.lon) for h in hospitals.itertuples()])
    w = w_cls * np.exp(-dist / sim["hospital_distance_decay_km"])
    return hospitals.iloc[rng.choice(len(hospitals), p=w / w.sum())]


def _appointment_minute(sim, rng) -> int:
    lo, hi = sim["appointment_hours"]
    if rng.random() < sim["morning_weight"]:
        hour = rng.integers(lo, 12)
    else:
        hour = rng.integers(12, hi)
    return int(hour * 60 + rng.integers(0, 6) * 10)


def _requests(sim, beneficiaries, hospitals, rng) -> pd.DataFrame:
    shift_start, shift_end = _hm(sim["shift"]["start"]), _hm(sim["shift"]["end"])
    rows, k = [], 1
    for day in _service_days(sim):
        n = int(rng.poisson(sim["requests_per_day_mean"]))
        for _ in range(n):
            c = beneficiaries.iloc[rng.integers(len(beneficiaries))]
            kind = ESCORT if rng.random() < sim["service_mix"][ESCORT] else HOMECARE
            base = {"request_id": f"R{k:05d}", "service_date": day.isoformat(), "beneficiary_id": c.beneficiary_id,
                    "service": kind, "hospital_id": None, "appointment_min": None,
                    "hospital_stay_min": None, "est_travel_min": None}
            if kind == ESCORT:
                h = _pick_hospital(c, hospitals, sim, rng)
                appt = _appointment_minute(sim, rng)
                stay = int(rng.integers(sim["escort_hospital_stay_minutes"][0],
                                        sim["escort_hospital_stay_minutes"][1] + 1) // 10 * 10)
                travel = estimated_drive_minutes(c.lat, c.lon, h.lat, h.lon)
                # 근무 시작에 출발해도 늦지 않도록 예약 시각을 10분 단위로 올린다
                earliest = shift_start + travel + 10
                if appt < earliest:
                    appt = int(math.ceil(earliest / 10) * 10)
                # 픽업 시간창: 예약 시각에 맞춰 도착하도록, 여유 10~30분
                tw_start = max(shift_start, int(appt - travel - 30))
                tw_end = max(tw_start, int(appt - travel - 10))
                duration = int(round(stay + 2 * travel))
                base.update(hospital_id=h.hospital_id, appointment_min=appt, hospital_stay_min=stay,
                            est_travel_min=travel)
            else:
                pref = int(rng.integers(shift_start // 60, (shift_end - sim["homecare_minutes"]) // 60 + 1) * 60)
                duration = sim["homecare_minutes"]
                tw_start = max(shift_start, pref - 60)
                tw_end = min(shift_end - duration, pref + 60)
            discharge = rng.random() < sim["discharge_case_rate"]
            cancelled = rng.random() < sim["same_day_cancel_rate"]
            # 지연은 취소와 동시에 일어나지 않는다. 취소된 요청은 시작하지 않으므로
            # '늦게 시작한다'가 의미를 갖지 않는다.
            delayed = (not cancelled) and rng.random() < sim.get("same_day_delay_rate", 0.0)
            lead_days = int(rng.integers(1, 8))
            requested_at = datetime.combine(day, datetime.min.time()) - timedelta(
                days=lead_days, minutes=-int(rng.integers(9 * 60, 18 * 60)))
            rows.append({**base,
                         "time_window_start_min": tw_start, "time_window_end_min": tw_end,
                         "service_duration_min": duration,
                         "discharge_case": bool(discharge),
                         "days_since_discharge": int(rng.integers(0, 15)) if discharge else None,
                         "priority": 2 if discharge else 1,
                         "requested_at": requested_at.isoformat(timespec="minutes"),
                         "cancelled_same_day": bool(cancelled),
                         "cancel_notice_min_before": int(rng.integers(30, 181)) if cancelled else None,
                         # 당일 지연 — 통보 시각과 지연 폭. 계획 단계에서는 쓰지 않는다.
                         "delayed_same_day": bool(delayed),
                         "delay_minutes": (int(rng.integers(sim["delay_minutes_range"][0],
                                                            sim["delay_minutes_range"][1] + 1))
                                           if delayed else None),
                         "delay_notice_min_before": (int(rng.integers(15, 61))
                                                     if delayed else None)})
            k += 1
    return pd.DataFrame(rows)


def generate_world(sim: dict, hospitals: pd.DataFrame, population: pd.DataFrame,
                   boundaries: dict, seed: int) -> World:
    if hospitals.empty:
        raise ValueError("병원 표가 비어 있습니다")
    rng = np.random.default_rng(seed)
    beneficiaries = _beneficiaries(sim, boundaries, population, rng)
    workers = _workers(sim, boundaries, rng)
    beneficiaries["primary_worker_id"] = _assign_primary_worker(beneficiaries, workers)
    requests = _requests(sim, beneficiaries, hospitals.reset_index(drop=True), rng)
    return World(beneficiaries=beneficiaries, workers=workers,
                 hospitals=hospitals.reset_index(drop=True), requests=requests)
