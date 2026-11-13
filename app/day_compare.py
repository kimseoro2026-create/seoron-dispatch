"""서로ON 돌봄배정 AI — 하루 배정 비교 페이지 (M1). app/main.py 가 이 파일을 페이지로 띄운다.

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
import sys
from pathlib import Path

# Streamlit Cloud 에서는 app/main.py 를 루트로 실행하고, 이 파일은 그 안의 페이지로 실행된다.
# planner/, verifier/, f0/ 패키지는 프로젝트 루트 아래 있으므로 경로를 추가한다.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from store import Store

# 운영 데이터 저장소: 확인된 요청 카드, 계산 실행 기록, 승인. 새로고침·다른 화면에서도 같은 값을 본다.
_store = Store()

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
st.caption("같은 날, 같은 요청과 같은 인력으로 **배정 방식만 바꿔** 계산했습니다. "
           "기존 방식(신청순·근거리)과 AI 최적화 중 어느 쪽이 더 많은 어르신을 제때 돌보는지 비교합니다.")


# ── 데이터 로딩 ───────────────────────────────────────────────────────────
def _data_stamp() -> str:
    """이동시간 행렬 파일(원본 또는 .gz) 수정시각을 캐시 키로 쓴다. 파일이 바뀌면 캐시가 무효화된다."""
    from planner.data import matrix_path
    p = matrix_path(ROOT / "data" / "processed")
    return f"{p.name}:{p.stat().st_mtime}" if p else "missing"


@st.cache_data(show_spinner="데이터 불러오는 중…")
def _load_travel(stamp: str):  # stamp 는 캐시 키 역할, 실제로 사용하지 않는다
    from planner.data import load_travel
    return load_travel(ROOT / "data" / "processed")


@st.cache_data(show_spinner="배정 계산 중…")
def _run(date: str, algo: str, stamp: str, cards_json: str = "[]", exclude_json: str = "[]"):
    # cards_json: 담당자가 확인한 요청 카드(요청 카드 검토 화면). 캐시 키가 되도록 JSON 문자열로 받는다.
    import json
    from planner.data import load_day
    from planner.optimize import optimize
    from verifier import verify

    # travel=None 으로 두면 load_day 가 그날 필요한 노드만으로 행렬을 만든다.
    # 전체 519,120구간 대신 70개 노드(~4,900구간)만 계산하므로 훨씬 빠르다.
    inst = load_day(ROOT / "data" / "processed", date, travel=None, extra_requests=json.loads(cards_json),
                    exclude_ids=json.loads(exclude_json))
    instance = inst.as_instance()

    if algo == "신청순":
        from planner.baseline import request_order as fn
        r = fn(instance)
        assignments = r.assignments
        unassigned  = [u.as_dict() for u in r.unassigned]
        meta = {"status": "feasible", "solve_seconds": None, "config": {}}
    elif algo == "근거리":
        from planner.baseline import nearest as fn
        r = fn(instance)
        assignments = r.assignments
        unassigned  = [u.as_dict() for u in r.unassigned]
        meta = {"status": "feasible", "solve_seconds": None, "config": {}}
    else:
        r = optimize(instance, time_limit_sec=8)
        assignments = r.assignments
        unassigned  = r.unassigned
        meta = {"status": r.status, "solve_seconds": r.solve_seconds, "config": {"time_limit_sec": 8}}

    rep = verify(instance, assignments)
    timelines = [t.__dict__ for t in rep.timelines]
    return inst, assignments, unassigned, timelines, rep, meta


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
    """이동시간 출처. travel_matrix.csv 가 없으면 추정 행렬을 쓰므로 'estimate' 다.

    파일이 없는데 unknown 이라고 적으면, 실제로는 추정값을 쓰면서 출처를 숨기는 셈이 된다
    (CLAUDE.md 절대원칙 3: 대략값은 estimate 로 표시한다).
    """
    from planner.data import matrix_path
    path = matrix_path(ROOT / "data" / "processed")
    if path is None:
        return "estimate"
    try:
        df = pd.read_csv(path, nrows=1)
        return df["source"].iloc[0] if "source" in df.columns else "estimate"
    except Exception:
        return "estimate"


@st.cache_data(show_spinner=False)
def _raw_sources() -> dict[str, str]:
    """병원·인구 원천이 실제 API 수집인지 모의(mock)인지 데이터 자체로 판정한다.

    모의 생성기(f0/mock.py)는 병원 ID 를 'MOCK-', 행정동 이름을 '가상동' 으로 만든다.
    판정할 수 없으면 '확인 불가' 로 둔다(실제라고 가정하지 않는다).
    """
    out: dict[str, str] = {}
    try:
        h = pd.read_csv(ROOT / "data" / "processed" / "hospitals.csv")
        is_mock = h["hospital_id"].astype(str).str.startswith("MOCK").any()
        out["병원"] = f"모의 데이터 {len(h)}곳" if is_mock else f"심평원 병원기본목록 API {len(h)}곳"
    except Exception:
        out["병원"] = "확인 불가"
    try:
        p = pd.read_csv(ROOT / "data" / "raw" / "population.csv")
        is_mock = p["dong"].astype(str).str.startswith("가상동").any()
        out["고령인구"] = "모의 데이터" if is_mock else "행안부 행정동별 인구 API"
    except Exception:
        out["고령인구"] = "확인 불가"
    return out


# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("보기 설정")
    dates = _dates()
    if not dates:
        st.error("data/processed/requests.csv 가 없습니다. python -m f0 all --offline 을 먼저 실행하세요.")
        st.stop()
    date = st.selectbox("날짜", dates, index=0)
    algos = ["신청순", "근거리", "AI 최적화"]
    selected = st.multiselect("비교할 방식", algos, default=algos,
                              help="신청순: 먼저 신청한 요청부터 배정(기존 방식)  \n"
                                   "근거리: 급한 요청부터 가장 가까운 인력에게(기존 방식)  \n"
                                   "AI 최적화: 근무·휴게·자격·병원 예약을 모두 지키면서 더 많은 요청을 배정")
    if not selected:
        st.warning("방식을 하나 이상 선택하세요.")
        st.stop()

    st.divider()
    stamp = _data_stamp()
    src = _matrix_source(stamp)
    src_label = {"osrm": "OSRM 도로", "estimate": "직선거리 대략값", "kakao": "카카오 길찾기"}.get(src, src)
    st.markdown("**데이터 출처**")
    st.caption(f"이동시간: **{src_label}**")
    for _label, _text in _raw_sources().items():
        st.caption(f"{_label}: **{_text}**")
    st.caption("어르신·인력·요청은 모두 가상으로 만든 데이터입니다.")

    # 담당자가 '확인 완료'한 추가 요청: 요청 카드 검토(카드-) + 자료 불러오기(가져옴-). 앱 저장소에서 읽는다.
    _all_cards = _store.ready_requests()
    day_cards = sorted((c for c in _all_cards if c["service_date"] == date), key=lambda c: c["request_id"])
    if _all_cards:
        st.divider()
        st.markdown("**담당자가 확인·변경한 요청** (요청 카드·가져온 자료·취소/지연 대응)")
        if day_cards:
            st.caption(f"이 날짜에 **{len(day_cards)}건**을 배정 계산에 넣었습니다: "
                       + ", ".join(c["request_id"] for c in day_cards))
        other = sorted({c["service_date"] for c in _all_cards} - {date})
        if other:
            st.caption("다른 날짜의 요청: " + ", ".join(other) + " (위 '날짜'에서 고르세요)")


# ── 각 방식 계산 ──────────────────────────────────────────────────────────
def _algo_key(a: str) -> str:
    return {"신청순": "신청순", "근거리": "근거리", "AI 최적화": "최적화"}[a]


results = {}
stamp = _data_stamp()
for a in selected:
    with st.spinner(f"{a} 계산 중…"):
        try:
            inst, assignments, unassigned, timelines, rep, meta = _run(
                date, _algo_key(a), stamp, json.dumps(day_cards, ensure_ascii=False, sort_keys=True),
                json.dumps(sorted(_store.cancelled_ids(date)), ensure_ascii=False))
            # 실행 기록(FR-23): 입력 스냅샷 해시·엔진 버전·설정. 같은 입력이면 같은 실행 ID.
            run_id = _store.record_run(
                date, a, inst.as_instance(), config=meta["config"], status=meta["status"],
                solve_seconds=meta["solve_seconds"],
                metrics={"assigned": len(assignments), "unassigned": len(unassigned),
                         "violations": len(rep.violations)},
                # 검증을 통과한 결과만 제안으로 남긴다(위반이 있으면 배정안으로 보여주지 않는다, CLAUDE.md 원칙 2)
                assignments=None if rep.violations else assignments,
                unassigned=None if rep.violations else unassigned)
            results[a] = dict(inst=inst, assignments=assignments, unassigned=unassigned,
                              timelines=timelines, rep=rep, run_id=run_id, meta=meta)
        except Exception as e:
            st.error(f"{a} 계산 중 오류: {e}")


# ── 핵심 결과와 비교표 ───────────────────────────────────────────────────
def _hhmm(m) -> str:
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


SERVICE_KO = {"hospital_escort": "병원동행", "household_support": "가사지원"}
REASON_KO = {
    "TIME_WINDOW_CONFLICT": "시간이 맞는 인력 없음", "NO_CAPACITY": "인력 일정이 꽉 참",
    "NO_SKILL_MATCH": "자격 맞는 인력 없음", "ROUTE_UNKNOWN": "이동경로 확인 불가",
    "SOLVER_UNRESOLVED": "계산 중 배정 못 함", "MISSING_REQUIRED": "필수 정보 부족",
}
CERT_KO = {"verified": "확인됨", "diagnostic": "추정", "unknown": "미상"}

if results:
    rows, served_n = [], {}
    for a, d in results.items():
        tl = d["timelines"]
        assigned = len(d["assignments"])
        total = len(d["inst"].requests)
        # 고유 인원: 같은 사람이 2건 배정되어도 1명으로 센다(PRD 4.1)
        req_by_id = {r["request_id"]: r for r in d["inst"].requests}
        served = {req_by_id[x["request_id"]]["beneficiary_id"] for x in d["assignments"]}
        all_people = {r["beneficiary_id"] for r in d["inst"].requests}
        served_n[a] = (len(served), len(all_people))
        travel_sum = sum(t.get("travel_min", 0) for t in tl)
        work_sum = sum(d["rep"].work_minutes.values()) if d["rep"].work_minutes else 0
        cap_sum = sum(max(0, w["shift_end_min"] - w["shift_start_min"] - w["break_minutes"])
                      for w in d["inst"].workers)
        rows.append({
            "방식": a,
            "제때 돌봄받은 어르신(명)": f"{len(served)} / {len(all_people)}",
            "배정한 요청(건)": f"{assigned} / {total}",
            "못 맡은 요청(건)": len(d["unassigned"]),
            "인력 이동 합계": f"{int(travel_sum) // 60}시간 {int(travel_sum) % 60}분",
            "인력이 일한 비율": f"{100*work_sum/cap_sum:.0f}%" if cap_sum else "-",
            "규칙 위반(건)": len(d["rep"].violations),
        })

    st.subheader(f"{date} 결과")
    if day_cards:
        st.info(f"담당자가 확인한 요청 **{len(day_cards)}건**을 넣어 계산했습니다. "
                "번호가 '카드-'(요청 카드 검토) 또는 '가져옴-'(자료 불러오기)으로 시작하는 요청입니다. "
                "같은 어르신·같은 서비스 요청이 원래 목록에 있으면 새로 확인한 값으로 바꿨습니다.")
    cols = st.columns(len(results))
    base = "신청순" if "신청순" in served_n else None
    for col, a in zip(cols, results):
        n, tot = served_n[a]
        delta = None if base is None or a == base else f"{n - served_n[base][0]:+d}명 (신청순 대비)"
        col.metric(f"{a} · 제때 돌봄받은 어르신", f"{n}명", delta=delta,
                   help=f"요청이 있던 어르신 {tot}명 중. 같은 분이 2건을 받아도 1명으로 셉니다.")
    if base and "AI 최적화" in served_n:
        diff = served_n["AI 최적화"][0] - served_n[base][0]
        if diff > 0:
            st.success(f"같은 인력으로 AI 최적화가 신청순보다 **어르신 {diff}명을 더** 제때 돌봤습니다.")
        elif diff == 0:
            st.info("이 날은 AI 최적화와 신청순이 돌본 어르신 수가 같습니다.")
        else:
            st.warning(f"이 날은 AI 최적화가 신청순보다 {-diff}명 적습니다. "
                       "높은 우선순위(퇴원 직후 등) 요청을 먼저 배정한 결과일 수 있습니다.")

    with st.expander("방식별 자세히 보기", expanded=False):
        st.dataframe(pd.DataFrame(rows).set_index("방식"), width="stretch")
        st.caption("규칙 위반 = 근무시간·휴게·자격·병원 예약 시각을 어긴 건수. 모든 배정은 별도 검증기로 다시 검사합니다.")

    if any(r["rep"].violations for r in results.values()):
        st.warning("⚠️ 규칙 위반이 있는 방식은 배정안으로 사용할 수 없습니다.")
    else:
        st.caption("✅ 모든 방식이 근무시간·휴게·자격·병원 예약 시각을 지켰습니다(위반 0건).")


# ── 인력별 하루 일정 ─────────────────────────────────────────────────────
# 색은 dataviz 기본 팔레트(범주형 1~4번)를 쓰고 validate_palette.js 로 밝은·어두운 화면 모두 검사했다.
# 밝은 화면에서 노랑·청록은 배경 대비가 낮으므로 범례 + 마우스 설명 + '표로 보기'를 함께 둔다.
def _is_dark() -> bool:
    try:
        return (st.context.theme.type or "light") == "dark"
    except Exception:
        return False


DARK = _is_dark()
KINDS = ["가사지원", "병원동행", "이동", "휴게", "빈 시간"]
PALETTE = ({"가사지원": "#3987e5", "병원동행": "#d95926", "이동": "#199e70", "휴게": "#c98500", "빈 시간": "#2c2c2a"}
           if DARK else
           {"가사지원": "#2a78d6", "병원동행": "#eb6834", "이동": "#1baf7a", "휴게": "#eda100", "빈 시간": "#e8e7e3"})
MUTED = "#9a998f" if DARK else "#6b6a64"
SKILL_KO = {"hospital_escort": "병원동행", "household_support": "가사"}


def _segments(worker_id: str, w: dict, timelines: list[dict]) -> list[tuple[int, int, str, str]]:
    """인력 한 명의 하루를 (시작분, 끝분, 종류, 요청) 구간으로 나눈다.

    분 단위로 칠한다. 구간을 그냥 이어 붙이면 휴게와 서비스가 겹칠 때 폭 합이 100%를 넘는다
    (중복을 먼저 지우고 칠해야 한다). 우선순위: 서비스 > 이동 > 휴게 > 빈 시간.
    """
    shift_s, shift_e = int(w["shift_start_min"]), int(w["shift_end_min"])
    brk_s = int(w["break_start_min"])
    brk_e = brk_s + int(w["break_minutes"])
    span = max(1, shift_e - shift_s)
    rank = {"빈 시간": 0, "휴게": 1, "이동": 2, "가사지원": 3, "병원동행": 4}
    paint = [0] * span
    note = [""] * span

    def fill(s_: int, e_: int, kind: str, text: str = "") -> None:
        a = max(shift_s, min(s_, shift_e)) - shift_s
        b = max(shift_s, min(e_, shift_e)) - shift_s
        for i in range(a, b):
            if paint[i] < rank[kind]:
                paint[i] = rank[kind]
                note[i] = text

    fill(brk_s, brk_e, "휴게")
    for t in timelines:
        if t.get("worker_id") != worker_id:
            continue
        arrive, end = int(t["arrive_min"]), int(t["end_min"])
        kind = "병원동행" if t.get("service_type") == "hospital_escort" else "가사지원"
        rid = str(t.get("request_id", ""))
        fill(arrive - int(t["travel_min"]), arrive, "이동", rid)
        fill(arrive, end, kind, rid)
    name = {v: k for k, v in rank.items()}
    out, i = [], 0
    while i < span:
        j = i
        while j < span and paint[j] == paint[i] and note[j] == note[i]:
            j += 1
        out.append((shift_s + i, shift_s + j, name[paint[i]], note[i]))
        i = j
    return out


def _timeline_html(worker_id: str, w: dict, timelines: list[dict]) -> str:
    shift_s, shift_e = int(w["shift_start_min"]), int(w["shift_end_min"])
    span = max(1, shift_e - shift_s)
    bars = ""
    for a, b, kind, text in _segments(worker_id, w, timelines):
        tip = f"{kind} {_hhmm(a)}~{_hhmm(b)}" + (f" · 요청 {text}" if text else "")
        bars += (f'<div style="display:inline-block;width:{(b - a) / span * 100:.3f}%;height:26px;'
                 f'background:{PALETTE[kind]};vertical-align:top;" title="{tip}"></div>')
    skills = "·".join(SKILL_KO.get(x, x) for x in w.get("skills", []))
    return (f'<div style="font-size:0.85rem;margin:2px 0 3px;"><b>{worker_id}</b> '
            f'<span style="color:{MUTED};">{skills} · 근무 {_hhmm(shift_s)}~{_hhmm(shift_e)}</span></div>'
            f'<div style="height:26px;border-radius:4px;overflow:hidden;white-space:nowrap;">{bars}</div>'
            f'<div style="margin-bottom:10px;"></div>')


def _hour_ticks(shift_s: int, shift_e: int) -> str:
    span = max(1, shift_e - shift_s)
    marks = ""
    hours = list(range((shift_s + 59) // 60, shift_e // 60 + 1))
    for h in hours:
        left = (h * 60 - shift_s) / span * 100
        shift = "0" if h == hours[0] else ("-100%" if h == hours[-1] else "-50%")
        marks += (f'<span style="position:absolute;left:{left:.2f}%;transform:translateX({shift});">'
                  f'{h}시</span>')
    return (f'<div style="position:relative;height:16px;font-size:0.72rem;color:{MUTED};'
            f'margin:0 0 2px;">{marks}</div>')


def _unassigned_rows(d: dict) -> list[dict]:
    """못 맡은 요청 표. 엔진 사유에 더해, 빈 일정의 자격 인력이면 가능했는지 다시 시험한 쉬운 설명을 붙인다."""
    import importlib

    import simulator.experiment as ex
    # 배포 서버는 프로세스를 다시 시작하지 않고 화면 파일만 다시 읽는다. 예전 simulator.experiment 가
    # 메모리에 남아 있으면 진단 함수가 없으므로 한 번 다시 읽는다(2026-11-12 배포에서 ImportError 확인).
    if not hasattr(ex, "idle_check"):
        ex = importlib.reload(ex)
    inst_d = d["inst"].as_instance()
    req_by_id = {r_["request_id"]: r_ for r_ in inst_d["requests"]}
    idle_ko = {"CAPACITY": "다른 일정과 겹침 (인력이 더 있으면 가능)",
               "BREAK": "점심 휴게 시간과 겹침",
               "WINDOW": "희망 시간이 근무시간과 안 맞음",
               "NO_SKILL": "자격 맞는 인력 없음", "ROUTE": "이동경로 확인 불가"}

    def explain(r_: dict | None) -> str:
        # 진단은 보조 정보다. 실패하면 지어내지 않고 '진단 불가'로 둔다(화면 전체를 멈추지 않음).
        if not r_:
            return "진단 불가"
        try:
            return idle_ko.get(ex.idle_check(r_, inst_d["workers"], tmap), "진단 불가")
        except Exception:
            return "진단 불가"

    try:
        tmap = ex._travel_map(inst_d["travel"])
    except Exception:
        tmap = None
    rows = []
    for u in d["unassigned"]:
        r_ = req_by_id.get(u["request_id"])
        rows.append({
            "요청": u["request_id"],
            "서비스": SERVICE_KO.get(r_["service_type"], "") if r_ else "",
            "희망 시작": f"{_hhmm(r_['earliest_start_min'])}~{_hhmm(r_['latest_start_min'])}" if r_ else "",
            "우선순위": ("높음" if int(r_.get("priority", 1)) >= 2 else "보통") if r_ else "",
            "왜 못 맡았나 (쉬운 설명)": explain(r_) if tmap is not None else "진단 불가",
            "엔진 사유": REASON_KO.get(u.get("reason"), u.get("reason", "")),
            "확실성": CERT_KO.get(u.get("certainty"), u.get("certainty", "")),
        })
    return rows


tab_names = sorted(results.keys(), key=lambda k: k != "AI 최적화")  # AI 최적화 일정을 먼저 보여준다
if tab_names:
    st.subheader("인력별 하루 일정")
    st.caption("막대 한 줄이 인력 한 명의 하루입니다. 막대에 마우스를 올리면 시각과 요청 번호가 보입니다.")
    tabs = st.tabs(tab_names)
    for tab, a in zip(tabs, tab_names):
        with tab:
            d = results[a]
            assigned_wids = {x["worker_id"] for x in d["assignments"]}
            workers_map = {w["worker_id"]: w for w in d["inst"].workers}
            busy = [wid for wid in workers_map if wid in assigned_wids]
            show_all = st.toggle("모든 인력 보기", value=False, key=f"all_{a}",
                                 help="기본은 배정이 있는 인력 8명만 보여줍니다.")
            show_wids = busy if show_all else busy[:8]

            legend = "　".join(
                f'<span style="display:inline-block;width:14px;height:14px;background:{PALETTE[k]};'
                f'border-radius:3px;vertical-align:middle;"></span> {k}' for k in KINDS)
            st.markdown(f'<div style="font-size:0.85rem;margin-bottom:8px;">{legend}</div>',
                        unsafe_allow_html=True)
            if show_wids:
                w0 = workers_map[show_wids[0]]
                html_rows = _hour_ticks(int(w0["shift_start_min"]), int(w0["shift_end_min"]))
                html_rows += "".join(_timeline_html(wid, workers_map[wid], d["timelines"]) for wid in show_wids)
                st.markdown(html_rows, unsafe_allow_html=True)
            if not show_all and len(busy) > len(show_wids):
                st.caption(f"배정이 있는 인력 {len(busy)}명 중 {len(show_wids)}명만 보여줍니다. "
                           "'모든 인력 보기'를 켜면 다 보입니다.")

            with st.expander("일정을 표로 보기"):
                rows_t = [{"인력": wid, "시작": _hhmm(sa), "끝": _hhmm(sb), "하는 일": kind, "요청": text}
                          for wid in show_wids
                          for sa, sb, kind, text in _segments(wid, workers_map[wid], d["timelines"])
                          if kind != "빈 시간"]
                st.dataframe(pd.DataFrame(rows_t), width="stretch", hide_index=True)

            if d["unassigned"]:
                urows = _unassigned_rows(d)
                st.markdown(f"**못 맡은 요청 {len(urows)}건**")
                st.dataframe(pd.DataFrame(urows), width="stretch", hide_index=True)
                st.caption("'쉬운 설명'은 빈 일정의 자격 인력에게 다시 넣어 본 진단입니다. "
                           "'엔진 사유'는 배정 엔진이 남긴 기록입니다.")
            else:
                st.success("못 맡은 요청이 없습니다.")

            if d["rep"].violations:
                st.error("**규칙 위반 — 이 배정안은 사용할 수 없습니다.**")
                vdf = pd.DataFrame([v.as_dict() for v in d["rep"].violations])
                st.dataframe(vdf, width="stretch", hide_index=True)


# ── 담당자 승인 ───────────────────────────────────────────────────────────
st.divider()
st.subheader("담당자 승인")

st.caption(
    "AI는 배정 후보와 이유만 제시합니다. 확정은 담당자가 합니다. "
    "수락하지 않은 건은 배정이 아닙니다."
)

if not results:
    st.info("위에서 방식을 하나 이상 선택하세요.")
else:
    import hashlib

    from planner.approval import ACCEPT, PENDING, REJECT, Decision

    algo_opts = list(results.keys())
    approve_algo = st.selectbox("승인할 배정안", algo_opts, key="approve_algo",
                                index=algo_opts.index("AI 최적화") if "AI 최적화" in algo_opts else 0)
    d_sel = results[approve_algo]
    proposal = d_sel["assignments"]
    run_id = d_sel["run_id"]

    # 담당자가 이 배정안을 처음 본 시점의 일정 버전. 그사이 다른 승인이 있으면 오래된 화면으로 보고 막는다(PRD 7장).
    seen_key = f"seen_version_{date}_{run_id}"
    current_version = _store.schedule_version(date)
    seen_version = st.session_state.setdefault(seen_key, current_version)
    confirmed_ids = {c["request_id"] for c in _store.confirmed(date)}

    if not proposal:
        st.warning("이 방식은 배정된 요청이 없습니다. 승인할 내용이 없습니다.")
    elif d_sel["rep"].violations:
        st.error("이 배정안은 하드 제약 위반이 있어 승인할 수 없습니다. 위반이 없는 배정안을 선택하세요.")
    else:
        if seen_version != current_version:
            st.warning("이 화면을 연 뒤에 일정이 바뀌었습니다(다른 승인·해제). "
                       "아래 '최신 일정으로 다시 보기'를 누른 뒤 결정해 주세요.")
            if st.button("최신 일정으로 다시 보기"):
                st.session_state[seen_key] = current_version
                st.rerun()
        # '모두 수락' 은 표의 기본값만 채운다. 실제 확정은 '승인 적용'을 눌러야 한다.
        fill_key = f"fill_{date}_{approve_algo}"
        fill = st.session_state.get(fill_key, "보류")
        st.markdown("**사용법**: 표의 **'결정'** 칸을 두 번 클릭해 **수락 / 거부 / 보류**를 고른 뒤 "
                    "아래 '**승인 적용**'을 누르세요. 거부할 때는 '거부 사유'를 꼭 적어 주세요.")
        b1, b2, _ = st.columns([1, 1, 1])
        if b1.button("모두 수락으로 채우기", help="표의 '결정'을 모두 '수락'으로 바꿉니다. "
                                              "거부할 건만 골라 바꾼 뒤 '승인 적용'을 누르세요."):
            st.session_state[fill_key] = "수락"
            st.rerun()
        if b2.button("모두 보류로 되돌리기"):
            st.session_state[fill_key] = "보류"
            st.rerun()
        req_by_id = {r["request_id"]: r for r in d_sel["inst"].requests}
        rows = []
        for a in proposal:
            req = req_by_id[a["request_id"]]
            done = a["request_id"] in confirmed_ids
            rows.append({
                "요청": a["request_id"],
                "대상자": req["beneficiary_id"],
                "인력": a["worker_id"],
                "도착 시각": _hhmm(a["arrive_min"]),
                "서비스": "병원동행" if req["service_type"] == "hospital_escort" else "가사지원",
                "지금 상태": "확정됨" if done else "제안",
                "결정": "보류" if done else fill,
                "거부 사유": "",
            })

        edited = st.data_editor(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            disabled=["요청", "대상자", "인력", "도착 시각", "서비스", "지금 상태"],
            column_config={
                "결정": st.column_config.SelectboxColumn(
                    "결정", options=["수락", "거부", "보류"], required=True, width="small"),
                "거부 사유": st.column_config.TextColumn(
                    "거부 사유", help="거부를 선택했다면 사유를 적어 주세요(필수)"),
            },
            key=f"editor_{date}_{approve_algo}_{fill}_{current_version}",
        )
        st.caption(f"계산 실행 ID `{run_id}` · 일정 버전 {current_version} · "
                   "'확정됨' 줄은 이미 확정되어 잠겨 있습니다(바꾸려면 아래에서 확정 해제).")

        cols = st.columns([1, 1, 3])
        apply_clicked = cols[0].button("승인 적용", type="primary")
        reset_clicked = cols[1].button("확정 초기화", help="실험용: 이 날짜의 확정을 모두 해제합니다. 기록은 남습니다.")
        approver = cols[2].text_input("승인자", value=st.session_state.get("approver", "담당자"),
                                      label_visibility="collapsed", placeholder="승인자 이름")
        st.session_state["approver"] = approver

        if reset_clicked:
            _store.reset_date(date, actor=approver or "담당자")
            st.session_state[seen_key] = _store.schedule_version(date)
            st.session_state.pop(fill_key, None)
            st.success("이 날짜의 확정을 모두 해제했습니다(기록은 남습니다).")
            st.rerun()

        if apply_clicked:
            label_to_action = {"수락": ACCEPT, "거부": REJECT, "보류": PENDING}
            decisions, errors = [], []
            for _, row in edited.iterrows():
                if row["요청"] in confirmed_ids:
                    continue                     # 확정된 건은 잠겨 있어 다시 결정하지 않는다
                action = label_to_action.get(row["결정"], PENDING)
                reason = (row.get("거부 사유") or "").strip()
                if action == REJECT and not reason:
                    errors.append(f"{row['요청']}: 거부 사유를 입력하세요.")
                    continue
                if action != PENDING:
                    decisions.append(Decision(row["요청"], action, reason))

            if errors:
                for e in errors:
                    st.error(e)
            elif not decisions:
                st.info("결정한 항목이 없습니다. '수락' 또는 '거부'를 선택하세요.")
            else:
                # 같은 화면에서 같은 결정을 두 번 눌러도 한 번만 반영되도록(AT-08) 결정 내용으로 키를 만든다.
                idem = hashlib.sha256(json.dumps(
                    [date, run_id, seen_version, [(d.request_id, d.action, d.reason) for d in decisions]],
                    ensure_ascii=False).encode()).hexdigest()
                try:
                    res = _store.approve(date, d_sel["inst"].as_instance(), proposal, decisions,
                                         expected_version=seen_version, actor=approver or "담당자",
                                         idempotency_key=idem, run_id=run_id)
                except Exception as e:
                    st.error(f"승인 처리 중 오류: {e}")
                else:
                    if res.ok:
                        st.session_state[seen_key] = res.schedule_version
                        st.success(res.message)
                    else:
                        st.error(res.message)
                    if res.violations:
                        st.dataframe(pd.DataFrame(res.violations), width="stretch", hide_index=True)

    # 확정 내역(저장소)
    confirmed = _store.confirmed(date)
    if confirmed:
        st.markdown(f"**{date} 확정된 배정 {len(confirmed)}건** (일정 버전 {_store.schedule_version(date)})")
        cdf = pd.DataFrame(confirmed)[["request_id", "worker_id", "arrive_min", "actor", "run_id"]]
        cdf["arrive_min"] = cdf["arrive_min"].map(_hhmm)
        cdf = cdf.rename(columns={"request_id": "요청", "worker_id": "인력", "arrive_min": "도착 시각",
                                  "actor": "승인자", "run_id": "계산 실행 ID"})
        st.dataframe(cdf, width="stretch", hide_index=True)
        st.download_button("확정 내역 CSV 내려받기", cdf.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"confirmed-{date}.csv", mime="text/csv")
        with st.expander("확정 해제 (사유 필수)"):
            rel_id = st.selectbox("해제할 요청", [c["request_id"] for c in confirmed], key=f"rel_{date}")
            rel_reason = st.text_input("해제 사유", key=f"rel_reason_{date}")
            if st.button("확정 해제"):
                if not rel_reason.strip():
                    st.error("해제 사유를 입력해 주세요.")
                else:
                    _store.release(date, rel_id, actor=st.session_state.get("approver") or "담당자",
                                   reason=rel_reason.strip())
                    st.session_state[seen_key] = _store.schedule_version(date)
                    st.rerun()
    else:
        st.info("아직 확정된 배정이 없습니다. 위에서 '수락'을 선택하고 승인 적용을 누르세요.")
    st.caption("승인 기록은 이 앱의 저장소에 남아 새로고침해도 유지됩니다. "
               "배포 화면은 앱을 다시 켜면 처음 상태로 돌아갑니다(가상 데이터 시연용).")


# ── 30일 실험 결과 ────────────────────────────────────────────────────────
st.divider()
st.subheader("30일 동안 비교하면")
st.caption("하루 결과는 날마다 다릅니다. 30일 × 3회 반복한 평균으로 방식을 비교합니다. "
           "'인력 바쁨'은 인력이 쓸 수 있는 시간 중 요청이 차지하는 비율(부하율)입니다.")
BUSY_KO = {0.6: "여유 (0.6)", 0.9: "바쁨 (0.9)", 1.1: "매우 바쁨 (1.1)"}

def _latest_by_name(pattern: str) -> Path | None:
    """파일 이름의 시각(…-YYYYMMDD-HHMMSS.csv)으로 가장 최근 실험 파일을 고른다.

    파일 수정 시각(mtime)으로 고르면 안 된다. 배포 서버는 저장소를 내려받을 때 모든 파일의
    수정 시각이 같아져, 예전 실험 파일이 '최신'으로 뽑힌다(2026-11-11 배포에서 확인).
    """
    files = sorted((ROOT / "data" / "experiments").glob(pattern), key=lambda p: p.name)
    return files[-1] if files else None


@st.cache_data(show_spinner=False)
def _load_experiment_summary(path_str: str) -> pd.DataFrame:  # path_str 이 캐시 키
    return pd.read_csv(path_str)

_summ_file = _latest_by_name("summary-full-*.csv")
summ = _load_experiment_summary(str(_summ_file)) if _summ_file else None
if summ is None:
    st.info("아직 실험 결과가 없습니다. scripts/run_simulator_full.bat 을 실행하세요.")
else:
    st.caption(f"각 막대는 하루 평균(90일·회)입니다 · 출처 파일: {_summ_file.name}")

    algo_kr = {"nearest": "근거리", "request_order": "신청순", "optimize": "AI 최적화"}
    summ = summ.copy()
    summ["방식"] = summ["algorithm"].map(algo_kr).fillna(summ["algorithm"])
    summ["인력 바쁨"] = summ["target_rho"].map(lambda v: BUSY_KO.get(round(float(v), 1), f"{v}"))
    summ["제때 돌봄받은 어르신(명/일)"] = summ["고유인원_평균"].round(1)
    summ["요청 중 배정 비율"] = (summ["적기배정률_평균"] * 100).round(1).astype(str) + "%"
    summ["인력 이동(분/일)"] = summ["총이동_평균"].round(0).astype(int)
    summ["인력이 일한 비율"] = (summ["활용률_평균"] * 100).round(1).astype(str) + "%"
    summ["위반"] = summ["위반_합계"].astype(int)

    # 한눈에: 바쁨별 제때 돌봄받은 어르신(방식별 막대)
    import altair as alt
    order = [m for m in ["신청순", "근거리", "AI 최적화"] if m in set(summ["방식"])]
    colors = ["#c3c2b7", "#86b6ef", "#2a78d6"] if not DARK else ["#5f5e58", "#5598e7", "#3987e5"]
    chart = alt.Chart(summ).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("방식:N", sort=order, title=None),
        x=alt.X("제때 돌봄받은 어르신(명/일):Q"),
        color=alt.Color("방식:N", scale=alt.Scale(domain=order, range=colors[-len(order):]), legend=None),
        row=alt.Row("인력 바쁨:N", sort=list(BUSY_KO.values()), title=None,
                    header=alt.Header(labelAngle=0, labelAlign="left")),
        tooltip=["인력 바쁨", "방식", "제때 돌봄받은 어르신(명/일)", "인력 이동(분/일)"],
    ).properties(height=70)
    st.altair_chart(chart)

    show = summ[["인력 바쁨", "방식", "제때 돌봄받은 어르신(명/일)", "요청 중 배정 비율",
                 "인력 이동(분/일)", "인력이 일한 비율", "위반"]]
    with st.expander("표로 보기"):
        st.dataframe(show.set_index(["인력 바쁨", "방식"]), width="stretch")

    with st.expander("해석 안내"):
        st.markdown("""
- **제때 돌봄받은 어르신**: 배정받은 서로 다른 어르신 수입니다(같은 분이 2건이면 1명).
- **요청 중 배정 비율**: 배정 건수 ÷ 전체 요청 건수입니다.
- **위반 ≠ 0**: 근무·휴게·자격·병원 예약 규칙을 어겼다는 뜻이며, 그 배정안은 쓸 수 없습니다.
- **이동시간 출처**: 왼쪽 메뉴에 표시된 값입니다.
- **한계**: 인구 데이터는 아직 모의값입니다. 못 맡은 요청의 대부분은 모든 인력의 점심 휴게가 12시로 같아서 생깁니다
  ('부족 자원·비용 리포트' 페이지 참고).
        """)

    viol_total = int(summ["위반"].sum())
    if viol_total > 0:
        st.warning(f"⚠️ 위반 합계 {viol_total}건. 해당 방식 결과는 배정안으로 사용할 수 없습니다.")
    else:
        st.success("모든 방식에서 위반 0건 — 하드 제약을 지켰습니다.")


# ── 취소·지연 대응 (30일 재생 실험) ───────────────────────────────────────
st.divider()
st.subheader("취소·지연이 생겼을 때 (30일 재생 실험)")

REPLAY_KR = {"request_order_replay": "신청순 + 재배정", "nearest_replay": "근거리 + 재배정"}


def _latest_replay_file() -> Path | None:
    return _latest_by_name("30day-*.csv")


@st.cache_data(show_spinner=False)
def _load_replay(path_str: str, _mtime: float) -> pd.DataFrame:  # _mtime 은 캐시 키
    return pd.read_csv(path_str)


rp = _latest_replay_file()
raw = _load_replay(str(rp), rp.stat().st_mtime) if rp else None
raw = raw[raw["algorithm"].isin(REPLAY_KR)] if raw is not None else None

if raw is None or raw.empty:
    st.info("아직 취소·지연 재생 실험 결과가 없습니다. python -m tools.run_replay30 을 실행하세요.")
else:
    from simulator.experiment import summarize

    rs = summarize(raw).copy()
    num = raw.copy()
    for c in ("delay_late_max", "delay_moved"):
        if c in num.columns:
            num[c] = pd.to_numeric(num[c], errors="coerce")
    gmax = num.groupby(["target_rho", "algorithm"])

    rs["방식"] = rs["algorithm"].map(REPLAY_KR)
    rs["인력 바쁨"] = rs["target_rho"].map(lambda v: BUSY_KO.get(round(float(v), 1), f"{v}"))
    rs["적기 배정률"] = (rs["적기배정률_평균"] * 100).round(1).astype(str) + "% ± " + \
                       (rs["적기배정률_표준편차"] * 100).round(1).astype(str) + "%p"
    cols_map = {
        "배정있던취소_평균": "취소(배정 있던 건)",
        "재배정성공_평균": "빈자리 다시 채움",
        "낭비이동_평균": "헛걸음 이동(분)",
        "지연_평균": "지연 통보",
        "지연지킴_평균": "지연 후에도 지킴",
        "지연상실_평균": "지연으로 놓침",
        "다른인력이동_평균": "다른 인력이 맡음",
        "지연폭_평균": "평균 늦어진 시간(분)",
    }
    shown = ["인력 바쁨", "방식", "적기 배정률"]
    for src, label in cols_map.items():
        if src in rs.columns:
            rs[label] = rs[src]
            shown.append(label)
    if "delay_late_max" in num.columns:
        mx = gmax["delay_late_max"].max().rename("가장 늦어진 시간(분)").reset_index()
        rs = rs.merge(mx, on=["target_rho", "algorithm"], how="left")
        shown.append("가장 늦어진 시간(분)")
    rs["위반"] = rs["위반_합계"].astype(int)
    shown.append("위반")

    st.dataframe(rs[shown].set_index(["인력 바쁨", "방식"]), width="stretch")
    n_obs = int(rs["관측수"].iloc[0]) if "관측수" in rs.columns else len(raw)
    st.caption(
        f"하루 평균값입니다(가장 늦어진 시간만 전체 기간 최댓값). 관측 {n_obs}일·방식, "
        f"출처 파일: {rp.name}"
    )
    if "다른인력이동_평균" not in rs.columns:
        st.caption("이 실험 파일에는 '다른 인력이 맡음' 열이 없습니다(이전 버전 실험).")

    with st.expander("해석 안내"):
        st.markdown("""
- **재생 실험**: 아침에 짠 배정을 하루 동안 실제처럼 돌리면서, 취소·지연 통보가 **온 시각에만** 반영합니다. 미래의 취소를 미리 알지 않습니다.
- **빈자리 다시 채움**: 취소로 빈 시간에 다른 미배정 요청을 넣은 건수입니다.
- **헛걸음 이동**: 인력이 이미 출발한 뒤 취소 통보가 와서 쓸모없이 움직인 시간입니다.
- **지연 후에도 지킴**: 시작이 늦어진 요청을 (원래 인력이든 다른 인력이든) 그날 안에 지킨 건수입니다. '다른 인력이 맡음'이 여기에 포함됩니다.
- **지연으로 놓침**: 늦어진 뒤 누구도 맡을 수 없게 된 건수입니다.
- **늦어진 시간**: 지킨 지연 건만 셉니다. 원래 시작 시각보다 얼마나 늦게 시작했는지입니다.
- **주의**: 위 '30일 실험 결과' 표는 취소·지연을 반영하지 않아, 취소된 요청도 배정된 것으로 셉니다. 두 표의 적기 배정률을 직접 비교하면 안 되고, 이 표 안에서 방식끼리 비교해야 합니다.
        """)

    if int(rs["위반"].sum()) > 0:
        st.warning("⚠️ 재생 실험에서 위반이 있습니다. 해당 방식 결과는 배정안으로 사용할 수 없습니다.")
