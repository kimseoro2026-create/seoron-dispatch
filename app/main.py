"""서로ON 돌봄배정 AI — 하루 비교 화면 (M1).

기능:
  - 날짜·방식(신청순·근거리·최적화) 선택
  - 인력별 타임라인 (이동·서비스·휴게 구분)
  - 세 방식 비교표
  - 미배정 목록과 사유

"가상 데이터 기반 실험" 을 상단에 표시하고 데이터 출처(estimate / osrm / mock)를 보여준다.
모든 숫자는 엔진·기준선 계산에서 나온다. 화면에서 직접 수치를 만들지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent

st.set_page_config(page_title="서로ON 돌봄배정 AI", layout="wide")

# ── 상단 배너 ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 실제 어르신·인력 정보를 사용하지 않습니다.
    </div>
    """,
    unsafe_allow_html=True,
)

st.title("돌봄배정 하루 비교")


# ── 데이터 로딩 ───────────────────────────────────────────────────────────
def _data_stamp() -> str:
    """travel_matrix.csv 수정시각을 캐시 키로 쓴다. 파일이 바뀌면 캐시가 무효화된다."""
    try:
        return str((ROOT / "data" / "processed" / "travel_matrix.csv").stat().st_mtime)
    except FileNotFoundError:
        return "missing"


@st.cache_data(show_spinner="데이터 불러오는 중…")
def _load_travel(stamp: str):  # stamp 는 캐시 키 역할, 실제로 사용하지 않는다
    from planner.data import load_travel
    return load_travel(ROOT / "data" / "processed")


@st.cache_data(show_spinner="배정 계산 중…")
def _run(date: str, algo: str, stamp: str):
    from planner.data import load_day
    from planner.optimize import optimize
    from verifier import verify

    inst = load_day(ROOT / "data" / "processed", date, travel=_load_travel(stamp))
    instance = inst.as_instance()

    if algo == "신청순":
        from planner.baseline import request_order as fn
        r = fn(instance)
        assignments = r.assignments
        unassigned  = [u.as_dict() for u in r.unassigned]
    elif algo == "근거리":
        from planner.baseline import nearest as fn
        r = fn(instance)
        assignments = r.assignments
        unassigned  = [u.as_dict() for u in r.unassigned]
    else:
        r = optimize(instance, time_limit_sec=30)
        assignments = r.assignments
        unassigned  = r.unassigned

    rep = verify(instance, assignments)
    timelines = [t.__dict__ for t in rep.timelines]
    return inst, assignments, unassigned, timelines, rep


@st.cache_data(show_spinner=False)
def _dates():
    from planner.data import service_dates
    return service_dates(ROOT / "data" / "processed")


@st.cache_data(show_spinner=False)
def _source():
    try:
        s = json.loads((ROOT / "data" / "processed" / "summary.json").read_text(encoding="utf-8"))
        return s.get("assumption_note", "")
    except Exception:
        return ""


@st.cache_data(show_spinner=False)
def _matrix_source(stamp: str):  # stamp 는 캐시 키 역할
    try:
        df = pd.read_csv(ROOT / "data" / "processed" / "travel_matrix.csv", nrows=1)
        return df["source"].iloc[0] if "source" in df.columns else "unknown"
    except Exception:
        return "unknown"


# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("설정")
    dates = _dates()
    if not dates:
        st.error("data/processed/requests.csv 가 없습니다. python -m f0 all --offline 을 먼저 실행하세요.")
        st.stop()
    date = st.selectbox("날짜", dates, index=0)
    algos = ["신청순", "근거리", "최적화(OR-Tools)"]
    selected = st.multiselect("비교할 방식", algos, default=algos[:2])
    if not selected:
        st.warning("방식을 하나 이상 선택하세요.")
        st.stop()

    st.divider()
    stamp = _data_stamp()
    src = _matrix_source(stamp)
    src_label = {"osrm": "OSRM 도로", "estimate": "직선거리 대략값", "kakao": "카카오 길찾기"}.get(src, src)
    st.caption(f"이동시간 출처: **{src_label}**")
    st.caption(_source())


# ── 각 방식 계산 ──────────────────────────────────────────────────────────
def _algo_key(a: str) -> str:
    return {"신청순": "신청순", "근거리": "근거리", "최적화(OR-Tools)": "최적화"}[a]


results = {}
stamp = _data_stamp()
for a in selected:
    with st.spinner(f"{a} 계산 중…"):
        try:
            inst, assignments, unassigned, timelines, rep = _run(date, _algo_key(a), stamp)
            results[a] = dict(inst=inst, assignments=assignments,
                              unassigned=unassigned, timelines=timelines, rep=rep)
        except Exception as e:
            st.error(f"{a} 계산 중 오류: {e}")


# ── 비교표 ────────────────────────────────────────────────────────────────
st.subheader("방식별 비교")
if results:
    rows = []
    for a, d in results.items():
        tl = d["timelines"]
        assigned = len(d["assignments"])
        total = len(d["inst"].requests)
        unassigned_n = len(d["unassigned"])
        violations = len(d["rep"].violations)
        # 고유 인원: 같은 사람이 2건 배정되어도 1명으로 센다(PRD 4.1)
        req_by_id = {r["request_id"]: r for r in d["inst"].requests}
        served = {req_by_id[x["request_id"]]["beneficiary_id"] for x in d["assignments"]}
        all_people = {r["beneficiary_id"] for r in d["inst"].requests}
        travel_sum = sum(t.get("travel_min", 0) for t in tl)
        work_sum = sum(d["rep"].work_minutes.values()) if d["rep"].work_minutes else 0
        cap_sum = sum(max(0, w["shift_end_min"] - w["shift_start_min"] - w["break_minutes"])
                      for w in d["inst"].workers)
        rows.append({
            "방식": a,
            "전체 요청": total,
            "배정": assigned,
            "미배정": unassigned_n,
            "적기 배정률": f"{100*assigned/total:.0f}%" if total else "-",
            "고유 인원": f"{len(served)}/{len(all_people)}",
            "총 이동(분)": int(travel_sum),
            "활용률": f"{100*work_sum/cap_sum:.0f}%" if cap_sum else "-",
            "위반": violations,
        })
    df_cmp = pd.DataFrame(rows).set_index("방식")
    st.dataframe(df_cmp, use_container_width=True)
    st.caption("고유 인원 = 배정받은 사람 수 / 요청이 있는 전체 사람 수 (같은 사람 2건 = 1명)")

    if any(r["rep"].violations for r in results.values()):
        st.warning("⚠️ 하드 제약 위반이 있는 방식은 배정안으로 사용할 수 없습니다.")


# ── 인력별 타임라인 ───────────────────────────────────────────────────────
COLORS = {
    "이동": "#93c5fd",
    "서비스(가사)": "#86efac",
    "서비스(동행)": "#f9a8d4",
    "휴게": "#fde68a",
    "미배정": "#e5e7eb",
}

def _timeline_html(worker_id: str, w: dict, timelines: list[dict]) -> str:
    """인력 한 명의 하루를 한 줄 막대로 그린다.

    분 단위로 칠한다. 구간을 그냥 이어 붙이면 휴게와 서비스가 겹칠 때
    폭 합이 100%를 넘어 막대가 넘쳐 보인다(중복을 먼저 지우고 칠해야 한다).
    우선순위: 서비스 > 이동 > 휴게 > 빈 시간.
    """
    shift_s = int(w["shift_start_min"])
    shift_e = int(w["shift_end_min"])
    brk_s = int(w["break_start_min"])
    brk_e = brk_s + int(w["break_minutes"])
    span = max(1, shift_e - shift_s)

    # 0=빈시간 1=휴게 2=이동 3=서비스가사 4=서비스동행
    RANK = {"미배정": 0, "휴게": 1, "이동": 2, "서비스(가사)": 3, "서비스(동행)": 4}
    paint = [0] * span

    def fill(s: int, e: int, kind: str) -> None:
        rank = RANK[kind]
        a = max(shift_s, min(s, shift_e))
        b = max(shift_s, min(e, shift_e))
        for i in range(a - shift_s, b - shift_s):
            if paint[i] < rank:
                paint[i] = rank

    fill(brk_s, brk_e, "휴게")
    for t in timelines:
        if t.get("worker_id") != worker_id:
            continue
        arrive = int(t["arrive_min"])
        end = int(t["end_min"])
        fill(arrive - int(t["travel_min"]), arrive, "이동")
        kind = "서비스(동행)" if t.get("service_type") == "hospital_escort" else "서비스(가사)"
        fill(arrive, end, kind)

    # 휴게가 업무와 겹쳐 덮인 구간은 실제로 쉰 게 아니다. 덮인 부분은 지운다.
    # (휴게는 하드 제약이라 정상 배정에서는 겹치지 않지만, 위반 배정을 보여줄 때도 깨지지 않게)
    NAME_OF = {0: "미배정", 1: "휴게", 2: "이동", 3: "서비스(가사)", 4: "서비스(동행)"}
    CLR = {0: "#e5e7eb", 1: "#fde68a", 2: "#93c5fd", 3: "#86efac", 4: "#f9a8d4"}

    bars = ""
    i = 0
    while i < span:
        v = paint[i]
        j = i
        while j < span and paint[j] == v:
            j += 1
        pct = (j - i) / span * 100
        label = NAME_OF[v]
        bars += (f'<div style="display:inline-block;width:{pct:.3f}%;height:28px;'
                 f'background:{CLR[v]};vertical-align:top;" '
                 f'title="{label} {shift_s + i}~{shift_s + j}분"></div>')
        i = j

    return (f'<div style="font-size:0.78rem;margin-bottom:4px;">'
            f'<b>{worker_id}</b> <span style="color:#94a3b8;">'
            f'{shift_s}~{shift_e}분</span></div>'
            f'<div style="height:28px;background:#f1f5f9;border-radius:4px;'
            f'overflow:hidden;white-space:nowrap;">{bars}</div>'
            f'<div style="margin-bottom:8px;"></div>')


tab_names = list(results.keys())
if tab_names:
    tabs = st.tabs(tab_names)
    for tab, a in zip(tabs, tab_names):
        with tab:
            d = results[a]
            # 타임라인
            st.markdown("**인력별 타임라인**")
            legend = "　".join(
                f'<span style="display:inline-block;width:14px;height:14px;background:{c};'
                f'border-radius:2px;vertical-align:middle;"></span> {k}'
                for k, c in COLORS.items()
            )
            st.markdown(f'<div style="font-size:0.78rem;margin-bottom:10px;">{legend}</div>',
                        unsafe_allow_html=True)
            workers_map = {w["worker_id"]: w for w in d["inst"].workers}
            html_rows = ""
            for wid, w in workers_map.items():
                html_rows += _timeline_html(wid, w, d["timelines"])
            st.markdown(html_rows, unsafe_allow_html=True)

            # 미배정 목록
            if d["unassigned"]:
                st.markdown("**미배정 목록**")
                udf = pd.DataFrame(d["unassigned"])
                cert_label = {"verified": "확인됨", "diagnostic": "추정", "unknown": "미상"}
                if "certainty" in udf.columns:
                    udf["certainty"] = udf["certainty"].map(cert_label).fillna(udf["certainty"])
                rename = {"request_id": "요청", "reason": "사유", "certainty": "확실성", "detail": "상세"}
                udf = udf.rename(columns={k: v for k, v in rename.items() if k in udf.columns})
                st.dataframe(udf, use_container_width=True, hide_index=True)
            else:
                st.success("미배정 요청이 없습니다.")

            # 위반
            if d["rep"].violations:
                st.error("**하드 제약 위반 — 이 배정안은 사용할 수 없습니다.**")
                vdf = pd.DataFrame([v.as_dict() for v in d["rep"].violations])
                st.dataframe(vdf, use_container_width=True, hide_index=True)


# ── 30일 실험 결과 ────────────────────────────────────────────────────────
st.divider()
st.subheader("30일 실험 결과")

@st.cache_data(show_spinner=False)
def _load_experiment_summary() -> pd.DataFrame | None:
    exp_dir = ROOT / "data" / "experiments"
    files = sorted(exp_dir.glob("summary-full-*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return pd.read_csv(files[0])

summ = _load_experiment_summary()
if summ is None:
    st.info("아직 실험 결과가 없습니다. run_simulator_full.bat 을 실행하세요.")
else:
    st.caption("부하율(ρ)별 3방식 비교 — 30일 × 3시드(각 90관측), 위반 건수가 있는 방식은 사용 불가")

    algo_kr = {"nearest": "근거리", "request_order": "신청순", "optimize": "최적화(OR-Tools)"}
    summ = summ.copy()
    summ["방식"] = summ["algorithm"].map(algo_kr).fillna(summ["algorithm"])
    summ["ρ(부하율)"] = summ["target_rho"]
    summ["적기 배정률"] = (summ["적기배정률_평균"] * 100).round(1).astype(str) + "% ± " + \
                         (summ["적기배정률_표준편차"] * 100).round(1).astype(str) + "%p"
    summ["고유인원"] = summ["고유인원_평균"].round(1)
    summ["총이동(분)"] = summ["총이동_평균"].round(0).astype(int)
    summ["활용률"] = (summ["활용률_평균"] * 100).round(1).astype(str) + "%"
    summ["위반"] = summ["위반_합계"].astype(int)

    show = summ[["ρ(부하율)", "방식", "적기 배정률", "고유인원", "총이동(분)", "활용률", "위반"]]
    st.dataframe(show.set_index(["ρ(부하율)", "방식"]), use_container_width=True)

    with st.expander("해석 안내"):
        st.markdown("""
- **적기 배정률**: 배정 건수 ÷ 전체 요청 건수. 높을수록 좋다.
- **고유인원**: 배정받은 서로 다른 어르신 수 (같은 분이 2건이면 1명으로 센다).
- **위반 ≠ 0**: 하드 제약(근무·휴게·자격·예약)을 어겼다는 뜻. 그 배정안은 쓸 수 없다.
- **이동시간 출처**: 사이드바에 표시된 값. OSRM 도로 기준이 가장 정확하다.
- **한계**: 인구 데이터가 아직 모의값이며, 동행 시간창이 20분으로 좁게 생성된 문제가 있다.
        """)

    viol_total = int(summ["위반"].sum())
    if viol_total > 0:
        st.warning(f"⚠️ 위반 합계 {viol_total}건. 해당 방식 결과는 배정안으로 사용할 수 없습니다.")
    else:
        st.success("모든 방식에서 위반 0건 — 하드 제약을 지켰습니다.")
