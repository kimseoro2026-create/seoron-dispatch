"""효과 검증 실험실 (W09) — 저장된 실험 결과 보기·내려받기, 가정을 바꿔 새 실험 돌리기, 다음 주 수요.

PRD FR-36(기본 프리셋: 인력 20명·평일 30일 가상 권역, 시드·취소율·부하율·이동·체류 배율), FR-37(시드별 비교, 평균·범위·실패 횟수,
입력 가정 내려받기), FR-39(퇴원 예정 의뢰와 접수된 수요를 나눠 다음 주 필요 시간 표시). 숫자는 모두 계산 결과다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from simulator.lab import ALGO_KO, LabConfig, run_lab, seed_summary
from store import Store

DATA = ROOT / "data" / "processed"
# 실험 결과 폴더. 시험할 때는 SEORON_EXP_DIR 로 임시 폴더를 쓴다(저장소 파일을 건드리지 않게).
EXP = Path(os.environ.get("SEORON_EXP_DIR") or ROOT / "data" / "experiments")
NEED = {"target_rho", "algorithm", "seed", "service_date", "unique_beneficiaries", "on_time_rate",
        "travel_minutes", "violations", "status", "assigned", "requests"}

# 가정 파일이 없는 명령줄 실험의 종류(파일 이름 앞부분). 한 파일에 여러 조건이 있으면 SCENARIO_COLS 로 나눠 본다.
PREFIX_DESC = {"30day": "기본 30일 비교", "full": "기본 비교", "cancel30": "취소 재배정 30일", "delay30": "지연 재배정 30일",
               "delaycheck": "지연 점검", "optcheck": "최적화 점검", "window": "동행 픽업 시간창 폭 실험",
               "staffing": "인력·휴게 시나리오 실험", "shortage": "부족 자원 시나리오 실험", "lab": "실험실 실험"}
SCENARIO_COLS = ("scenario", "escort_window", "add", "retrain", "breaks", "household_extra")

store = Store()

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 결과는 가정 위에서 계산한 값입니다. 실제 기관 효과로 단정하지 않습니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("효과 검증 실험실")
st.caption("같은 요청·같은 인력에서 배정 방식만 바꿨을 때의 차이를 여러 날·여러 시드로 확인합니다. "
           "결과와 함께 실험 가정을 저장해, 같은 조건으로 다시 돌릴 수 있게 합니다.")

tab1, tab2, tab3, tab4 = st.tabs(["저장된 실험 결과", "새 실험 돌리기", "다음 주 필요한 서비스 시간", "공동 동행 실험(조건부)"])


@st.cache_data(show_spinner=False)
def _files(_n: int) -> list[str]:          # _n: 파일 수(늘면 다시 읽음)
    out = []
    for p in sorted(EXP.glob("*.csv"), key=lambda x: x.name, reverse=True):
        try:
            cols = set(pd.read_csv(p, nrows=0, encoding="utf-8-sig").columns)
        except Exception:
            continue
        if NEED <= cols:
            out.append(p.name)
    return out


def _downloads(raw: pd.DataFrame, summ: pd.DataFrame, per_seed: pd.DataFrame, meta: dict, stem: str, key: str):
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("원본 CSV", raw.to_csv(index=False).encode("utf-8-sig"), f"{stem}.csv", "text/csv", key=f"r_{key}")
    c2.download_button("요약 CSV", summ.to_csv(index=False).encode("utf-8-sig"), f"{stem}-summary.csv", "text/csv",
                       key=f"s_{key}")
    c3.download_button("시드별 CSV", per_seed.to_csv(index=False).encode("utf-8-sig"), f"{stem}-seeds.csv", "text/csv",
                       key=f"p_{key}")
    c4.download_button("입력 가정 JSON", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
                       f"{stem}-assumptions.json", "application/json", key=f"a_{key}")


def _show(raw: pd.DataFrame, meta: dict, stem: str, key: str):
    per_seed, summ = seed_summary(raw)
    st.markdown("**방식·부하율별 요약** — 시드마다 하루 평균을 낸 뒤, 시드 사이의 평균·최소·최대를 보여줍니다.")
    st.dataframe(summ, hide_index=True, width="stretch")
    fails = int(summ["실패 횟수"].sum())
    (st.warning if fails else st.caption)(
        f"실패(규칙 위반·계산 실패·배정 0건) {fails}회." + (" 실패한 날은 결과에서 빼지 않고 그대로 셉니다." if fails else ""))
    with st.expander("시드별 결과"):
        st.dataframe(per_seed, hide_index=True, width="stretch")
    _downloads(raw, summ, per_seed, meta, stem, key)


# ── 저장된 결과 ──────────────────────────────────────────────────────────
with tab1:
    files = _files(len(list(EXP.glob("*.csv")))) if EXP.exists() else []
    if not files:
        st.info("저장된 실험 결과가 없습니다. '새 실험 돌리기'에서 실험을 돌려 보세요.")
    else:
        name = st.selectbox("실험 파일", files, help="파일 이름의 날짜·시각 순(최근 위)")
        raw = pd.read_csv(EXP / name, encoding="utf-8-sig")
        side = EXP / name.replace(".csv", "-assumptions.json")
        if side.exists():
            meta = json.loads(side.read_text(encoding="utf-8"))
            cfg = meta.get("config", {})
            st.caption(f"가정: 날짜 {len(meta.get('dates', []))}일 · 시드 {cfg.get('seeds')} · 부하율 {cfg.get('rhos')} · "
                       f"이동 ×{cfg.get('travel_factor')} · 병원 체류 ×{cfg.get('stay_factor')} · "
                       f"취소율 {'자료 그대로' if cfg.get('cancel_rate') is None else cfg.get('cancel_rate')} · "
                       f"엔진 {meta.get('engine_version')}")
        else:
            kind = next((v for k, v in PREFIX_DESC.items() if name.startswith(k)), "명령줄 실험")
            meta = {"file": name, "kind": kind,
                    "note": "명령줄(python -m simulator.run, tools/run_*.py)로 만든 실험이라 가정 파일이 없습니다. "
                            "자세한 가정은 docs/STATUS.md 의 같은 날짜 기록을 보세요.",
                    "dates": sorted(raw["service_date"].unique().tolist()),
                    "seeds": sorted(raw["seed"].unique().tolist()), "rhos": sorted(raw["target_rho"].unique().tolist())}
            st.caption(f"가정 파일이 없는 명령줄 실험입니다 — 종류: **{kind}**. 날짜 {len(meta['dates'])}일 · "
                       f"시드 {meta['seeds']} · 부하율 {meta['rhos']}. 자세한 가정은 docs/STATUS.md 참고.")
        # 한 파일에 여러 실험 조건(시나리오)이 섞여 있으면 조건마다 따로 본다(섞어서 평균내지 않는다)
        scen_cols = [c for c in SCENARIO_COLS if c in raw.columns]
        if scen_cols:
            raw["_cond"] = raw[scen_cols].astype(str).agg(" · ".join, axis=1)
            conds = sorted(raw["_cond"].unique())
            cond = st.selectbox(f"실험 조건({', '.join(scen_cols)})", conds)
            meta = dict(meta, condition={c: v for c, v in zip(scen_cols, cond.split(" · "))})
            raw = raw[raw["_cond"] == cond].drop(columns="_cond")
        _show(raw, meta, name.replace(".csv", ""), "saved")

# ── 새 실험 ──────────────────────────────────────────────────────────────
with tab2:
    st.markdown("**기본 프리셋**: 인력 20명 · 평일 30일 가상 권역(지금 앱의 데이터). 아래 가정을 바꿔 돌립니다.")
    c1, c2, c3 = st.columns(3)
    n_days = c1.number_input("날짜 수(앞에서부터)", 1, 30, 3)
    seeds = c2.multiselect("시드", list(range(42, 52)), default=[42])
    rhos = c3.multiselect("인력 바쁨(부하율)", [0.6, 0.9, 1.1], default=[0.9],
                          help="부하율 = (요청 점유 + 예상 이동) ÷ 인력 가용시간")
    algos = st.multiselect("방식", list(ALGO_KO), default=["request_order", "nearest", "optimize"],
                           format_func=lambda a: ALGO_KO[a],
                           help="'+취소 재배정'은 취소 통보가 온 시각에만 빈자리를 다시 채웁니다(미래 정보 금지).")
    c4, c5, c6, c7 = st.columns(4)
    travel_f = c4.selectbox("이동시간 배율", [0.8, 1.0, 1.3], index=1)
    stay_f = c5.selectbox("병원 체류 배율", [0.8, 1.0, 1.3], index=1)
    cancel = c6.selectbox("취소율", ["자료 그대로", 0.0, 0.1, 0.2],
                          format_func=lambda v: v if isinstance(v, str) else f"{int(v * 100)}%",
                          help="취소율을 정하면 통보 시각은 30·60·120·240분 전 중 하나로 정합니다(가정). "
                               "취소율은 '+취소 재배정·반영' 방식에서만 결과에 영향을 줍니다.")
    tl = c7.number_input("AI 계산 제한(초)", 2, 10, 5)
    n_opt = n_days * len(seeds) * len(rhos) * (1 if "optimize" in algos else 0)
    est = n_opt * tl + n_days * len(seeds) * len(rhos) * max(0, len(algos) - (1 if "optimize" in algos else 0)) * 0.5
    st.caption(f"예상 계산 시간: 약 {int(est)}초 (AI 최적화 {n_opt}회)")
    too_big = est > 300
    if too_big:
        st.warning("화면에서 돌리기엔 깁니다(5분 넘음). 날짜·시드를 줄이거나 명령줄 `python -m simulator.run` 을 쓰세요.")
    actor = st.text_input("처리자", key="lab_actor", placeholder="담당자 이름")
    if st.button("실험 돌리기", type="primary", disabled=too_big or not (seeds and rhos and algos)):
        if not actor.strip():
            st.error("처리자를 입력해 주세요.")
        else:
            cfg = LabConfig(n_days=int(n_days), seeds=[int(x) for x in seeds], rhos=[float(x) for x in rhos],
                            algorithms=list(algos), time_limit_sec=int(tl), travel_factor=float(travel_f),
                            stay_factor=float(stay_f), cancel_rate=None if isinstance(cancel, str) else float(cancel))
            total = n_days * len(seeds) * len(rhos) * len(algos)
            bar = st.progress(0.0, text="실험 중…")
            done = [0]

            def _p(msg):
                done[0] += 1
                bar.progress(min(1.0, done[0] / max(1, total)), text=msg)

            out = run_lab(cfg, DATA, out_dir=EXP, store=store, actor=actor.strip(), progress=_p)
            st.session_state["lab_last"] = {"raw_path": str(out["raw_path"]), "meta": out["meta"]}
            _files.clear()
    last = st.session_state.get("lab_last")
    if last and Path(last["raw_path"]).exists():
        st.success(f"저장했습니다: {Path(last['raw_path']).name} (가정 파일 함께 저장)")
        _show(pd.read_csv(last["raw_path"], encoding="utf-8-sig"), last["meta"], Path(last["raw_path"]).stem, "new")
    st.caption("인력을 늘리는 효과는 이 실험에 섞지 않습니다('부족 자원·비용 리포트'에서 따로 계산). "
               "배포 화면에 저장한 실험 파일은 앱을 다시 켜면 사라질 수 있으니 내려받아 두세요.")

# ── 다음 주 수요 ─────────────────────────────────────────────────────────
with tab3:
    from planner.data import service_dates
    from simulator.demand import next_week_demand
    ref = st.selectbox("기준 날짜(이날부터 7일)", service_dates(DATA), key="demand_ref")
    with st.spinner("집계 중…"):
        dem = next_week_demand(store, DATA, ref)
    if dem["days"]:
        df = pd.DataFrame([{"날짜": x["service_date"], "접수된 요청(건)": x["requests"],
                            "필요한 서비스 시간(시간)": x["service_hours"], "경로 없음(건)": x["no_route"]}
                           for x in dem["days"]])
        st.markdown(f"**접수된 수요**: {len(df)}일 · 요청 {int(df['접수된 요청(건)'].sum())}건 · "
                    f"서비스 {df['필요한 서비스 시간(시간)'].sum():.1f}시간")
        st.dataframe(df, hide_index=True, width="stretch")
        st.caption("병원동행은 이동(집→병원) + 병원 체류 + 이동(병원→집)으로 셉니다. 경로가 없는 요청은 시간에 넣지 않고 따로 셉니다.")
    else:
        st.info("이 기간에는 서비스일이 없습니다.")
    st.markdown(f"**퇴원 예정 의뢰(아직 요청 아님)**: {len(dem['referrals'])}명")
    if dem["referrals"]:
        st.dataframe(pd.DataFrame([{"카드": r["card_id"], "대상자": r["beneficiary_id"],
                                    "퇴원 예정일": r["planned_discharge_date"],
                                    "서비스": {"hospital_escort": "병원동행", "household_support": "가사지원"}.get(
                                        r["service_type"], r["service_type"] or "")} for r in dem["referrals"]]),
                     hide_index=True, width="stretch")
    st.caption("퇴원 예정 의뢰는 요청 시간이 정해지지 않아 필요 시간을 지어내지 않습니다(건수만). "
               "아직 접수되지 않은 수요의 예측은 하지 않습니다.")

# ── 공동 동행 실험(EXP) ──────────────────────────────────────────────────
with tab4:
    from simulator.joint import JointConfig, run_joint_experiment
    st.warning("**조건부 실험입니다.** 기본 운영은 1:1 전담이며, 이 결과는 1:1 성과에 더하지 않습니다. "
               "운영·보험·정산 조건은 확인되지 않았고, 실제 여러 분 동행을 허용한다는 뜻이 아닙니다.")
    st.caption("같은 병원·비슷한 예약 시각의 병원동행 두 건을 한 인력이 함께 모신다고 가정합니다. 같은 요청으로 1:1과 공동 동행을 "
               "각각 계산해 나란히 비교합니다. 인력 시간은 한 번만 세고, 어르신별 추가 기다림(돌아가는 길·병원 대기)은 따로 봅니다.")
    base = JointConfig.from_config()
    j1, j2 = st.columns(2)
    mode = j1.radio("방식", ["simultaneous", "ride_share"], horizontal=True, key="joint_mode",
                    format_func=lambda m: {"simultaneous": "병원 내 동시 지원", "ride_share": "이동만 함께(병원에서는 한 분씩)"}[m])
    consent = j2.checkbox("동의가 확인된 분만(권장)", value=base.require_consent, key="joint_consent",
                          help="끄면 동의를 가정한 '가능성' 계산이 됩니다. 결과에 가정으로 표시됩니다.")
    mob = st.multiselect("함께 모실 수 있는 이동조건", ["independent", "walker", "wheelchair"], default=base.allowed_mobility,
                         format_func=lambda m: {"independent": "자립 보행", "walker": "보행기", "wheelchair": "휠체어"}[m],
                         key="joint_mob")
    k1, k2, k3, k4, k5 = st.columns(5)
    gap = k1.number_input("예약 시각 차이 상한(분)", 0, 180, base.max_appointment_gap_min, step=10, key="joint_gap")
    detour = k2.number_input("더 타는 시간 상한(분)", 0, 90, base.max_detour_min, step=5, key="joint_detour")
    wait = k3.number_input("병원 대기 상한(분)", 0, 240, base.max_wait_min, step=10, key="joint_wait")
    jdays = k4.number_input("날짜 수", 1, 30, 10, key="joint_days")
    jalgo = k5.selectbox("방식(배정)", ["nearest", "request_order", "optimize"], key="joint_algo",
                         format_func=lambda a: ALGO_KO[a])
    jactor = st.text_input("처리자", key="joint_actor", placeholder="담당자 이름")
    if st.button("공동 동행 실험 돌리기", type="primary"):
        if not jactor.strip():
            st.error("처리자를 입력해 주세요.")
        elif not mob:
            st.error("이동조건을 하나 이상 고르세요.")
        else:
            cfg = JointConfig(enabled=True, mode=mode, require_consent=consent, allowed_mobility=list(mob),
                              max_appointment_gap_min=int(gap), max_detour_min=int(detour), max_wait_min=int(wait))
            with st.spinner("계산 중…"):
                jout = run_joint_experiment(DATA, cfg, n_days=int(jdays), algorithm=jalgo, out_dir=EXP, store=store,
                                            actor=jactor.strip())
            st.session_state["joint_last"] = {"raw_path": str(jout["raw_path"]), "meta": jout["meta"]}
    jl = st.session_state.get("joint_last")
    if jl and Path(jl["raw_path"]).exists():
        jdf = pd.read_csv(jl["raw_path"], encoding="utf-8-sig")
        cfgj = jl["meta"]["config"]
        if not cfgj.get("require_consent", True):
            st.warning("동의를 가정한 계산입니다(동의 확인 없음).")
        agg = jdf.groupby("mode").agg(**{
            "날짜 수": ("service_date", "nunique"), "모신 어르신(하루 평균)": ("unique_beneficiaries", "mean"),
            "병원동행 모심(하루 평균)": ("escort_served", "mean"), "인력 근무(분, 하루 합)": ("worker_minutes", "mean"),
            "짝 지음(하루 평균)": ("pairs_formed", "mean"), "짝 배정(하루 평균)": ("pairs_assigned", "mean"),
            "규칙 위반": ("violations", "sum")}).round(2).reset_index().rename(columns={"mode": "방식"})
        st.dataframe(agg, hide_index=True, width="stretch")
        jj = jdf[jdf["mode"] == "공동 동행"]
        ex = {c: pd.to_numeric(jj[c], errors="coerce").mean() for c in
              ("excluded_consent_mobility", "excluded_no_partner", "excluded_time_route")}
        st.markdown(f"**짝에서 빠진 병원동행(하루 평균)**: 동의·이동조건 {ex['excluded_consent_mobility']:.1f}건 · "
                    f"같은 병원 짝 없음 {ex['excluded_no_partner']:.1f}건 · 시간·경로 조건 {ex['excluded_time_route']:.1f}건")
        waits = pd.to_numeric(jj["avg_extra_wait_min"], errors="coerce").dropna()
        st.caption(("공동 동행한 어르신의 추가 기다림 평균 " + f"{waits.mean():.1f}분" if len(waits) else
                    "배정된 공동 동행이 없어 기다림을 계산할 수 없습니다.")
                   + " 짝이 거의 없으면 효과도 없습니다 — 그 결과도 그대로 보고합니다.")
        st.download_button("공동 동행 실험 CSV", jdf.to_csv(index=False).encode("utf-8-sig"), Path(jl["raw_path"]).name,
                           "text/csv", key="joint_dl")
        st.download_button("가정 JSON", json.dumps(jl["meta"], ensure_ascii=False, indent=2).encode("utf-8"),
                           Path(jl["raw_path"]).stem + "-assumptions.json", "application/json", key="joint_dl2")
