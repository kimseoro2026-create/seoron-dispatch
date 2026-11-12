"""부족 자원·비용 리포트 페이지 (PRD W10).

같은 요청에서 휴게 운영·인력만 바꿔 다시 계산한 실험(tools/run_shortage_report.py)의
최신 결과를 읽어 보여준다. 숫자는 모두 tools/shortage_report.build() 계산값이다.
단가가 없으면 금액은 '계산 불가'로 둔다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from tools.shortage_report import ALGO_KO, CERT, IDLE, NA, REASON, SERVICE, build, latest_pair, to_markdown

EXP = Path(os.environ.get("SEORON_EXP_DIR") or ROOT / "data" / "experiments")  # 테스트용 덮어쓰기
DATA = ROOT / "data" / "processed"

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 합성 데이터 시뮬레이션으로 계산한 추정입니다. 실제 기관의 효과가 아닙니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("부족 자원·비용 리포트")
st.caption("같은 날·같은 요청에서 **휴게 운영과 인력만 바꿔** 다시 배정했습니다. "
           "무엇을 바꾸면 제때 돌봄받는 어르신이 늘어나는지, 비용은 얼마나 드는지 봅니다.")

try:
    d_path, _ = latest_pair(EXP)
except SystemExit as e:
    st.info(f"아직 리포트용 실험 결과가 없습니다. `python -m tools.run_shortage_report` 를 먼저 실행하세요. ({e})")
    st.stop()


@st.cache_data(show_spinner="리포트를 계산하는 중...")
def _report(name: str) -> dict:  # name 은 캐시 키(새 실험 파일이면 다시 계산)
    return build(EXP, DATA)


r = _report(d_path.name)
c = r["cost"]


def won(x) -> str:
    return NA if x is None or pd.isna(x) else f"{x:,.0f}원"


with st.expander("실험 조건과 비용 가정", expanded=False):
    st.markdown(
        f"- 실험 파일: `{r['days_path'].name}` · 요청별 결과 `{r['outcomes_path'].name}`\n"
        f"- 부하율 ρ {r['rho']} · {r['n_dates']}일 × 시드 {r['seeds']} · 하드 제약 위반 합계 "
        f"**{int(r['days']['violations'].sum())}건**\n"
        f"- 시급 {won(c.get('hourly_krw'))} (가정, `config/region.yaml`) · 유급시간 가정 `{c['paid_time']}` "
        f"(근무시간에서 무급 휴게를 뺀 시간)\n"
        f"- 월급(고정급) 모델 단가: {won(c.get('monthly_salary_krw'))} · 이동 1분당 교통비: "
        f"{won(c.get('transport_krw_per_min'))}\n"
        "- 추가 인력은 기존 동행 인력의 근무시간·휴게·출발지를 그대로 복제해 다시 배정했습니다. "
        "부족한 시간을 '몇 명 부족'으로 환산하지 않습니다.\n"
        "- 휴게 분산(11·12·13·14시 시작)은 유급시간이 같아 추가 인건비를 0으로 계산했습니다. "
        "근무 규정상 가능한지는 확인하지 않았습니다.")

algo = st.radio("배정 방식", ["optimize", "nearest"], horizontal=True,
                format_func={"optimize": "최적화", "nearest": "근거리(참고)"}.get)
t = r["tables"].get(algo)
if t is None:
    st.warning("이 방식의 결과가 실험 파일에 없습니다.")
    st.stop()

st.subheader("1. 무엇을 바꾸면 얼마나 늘어나나 (하루 평균)")
show = pd.DataFrame({
    "시나리오": t["label"],
    "적기 배정(건)": t["assigned"].round(1),
    "적기 지원 고유 인원(명)": t["uniq"].round(1),
    "현재 대비(명)": t["d_unique"].round(1),
    "요청 모두 충족 인원(명)": t["fully_met"].round(1),
    "높은 우선순위 미충족률": (t["high_unmet_rate"] * 100).round(1).astype(str) + "%",
    "병원동행 배정/요청": t["escort"].round(1).astype(str) + " / " + t["requests_escort"].round(1).astype(str),
    "총 이동(분)": t["travel"].round(0).astype(int),
    "추가 인건비/일": t["extra_cost_per_day"].map(won),
    "늘어난 1명당 추가 비용/일": t["cost_per_added_person"].map(won),
})
st.dataframe(show, hide_index=True, width="stretch")
st.altair_chart(
    alt.Chart(show).mark_bar().encode(
        y=alt.Y("시나리오:N", sort=list(show["시나리오"]), title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("적기 지원 고유 인원(명):Q"),
        tooltip=["시나리오", "적기 지원 고유 인원(명)", "현재 대비(명)"]),
    width="stretch")
st.caption("'늘어난 1명당 추가 비용'은 추가 인건비 ÷ 현재 대비 늘어난 고유 인원입니다. "
           "늘어난 인원이 0 이하이거나 단가가 없으면 계산하지 않습니다.")

st.subheader(f"2. 지금 조건에서 미배정은 어디서 생기나 ({ALGO_KO.get(r['unmet_algo'])}, 현재)")
u = r["unmet"]
idle = u["idle_check"].assign(진단=lambda x: x.idle_check.map(IDLE).fillna(x.idle_check),
                              서비스=lambda x: x.service_type.map(SERVICE))
st.markdown("**미배정 진단** — 빈 일정의 자격 인력이라면 맡을 수 있었는지 다시 시험했습니다.")
st.dataframe(pd.DataFrame({"진단": idle["진단"], "서비스": idle["서비스"],
                           "미배정/일": idle["unmet_per_day"].round(1),
                           "비중": (idle["share"] * 100).round(1).astype(str) + "%"}),
             hide_index=True, width="stretch")
st.caption("'인력 여유 부족'은 인력·배치로, '휴게 시각과 충돌'은 휴게 운영으로, "
           "'시간창·근무시간 불일치'는 요청 조건 조정으로만 줄일 수 있습니다. 엔진의 사유 코드와 별개인 진단용 분류입니다.")


def _rate_table(df: pd.DataFrame, key: str, label: str, fmt=lambda v: v) -> pd.DataFrame:
    return pd.DataFrame({label: df[key].map(fmt), "요청/일": df["requests_per_day"].round(1),
                         "미배정/일": df["unmet_per_day"].round(1),
                         "미충족률": (df["unmet_rate"] * 100).round(1).astype(str) + "%"})


tabs = st.tabs(["서비스별", "우선순위별", "시간대별", "권역별", "엔진 사유"])
with tabs[0]:
    st.dataframe(_rate_table(u["service_type"], "service_type", "서비스", SERVICE.get), hide_index=True)
with tabs[1]:
    st.dataframe(_rate_table(u["priority"], "priority", "우선순위", lambda v: f"{int(v)}"), hide_index=True)
    st.caption("우선순위 2 이상 = 퇴원 14일 이내 등 먼저 돌봐야 하는 요청")
with tabs[2]:
    st.dataframe(_rate_table(u["start_hour"], "start_hour", "희망 시작", lambda v: f"{int(v)}시"), hide_index=True)
with tabs[3]:
    st.dataframe(_rate_table(u["area"], "area", "권역(가상 행정동)"), hide_index=True)
    st.caption("가까운 곳만 배정되는지 확인하려는 표입니다. 권역 이름은 가상입니다.")
with tabs[4]:
    rs = u["reason"]
    st.dataframe(pd.DataFrame({"사유": rs["reason"].map(lambda v: REASON.get(v, v)),
                               "확실성": rs["certainty"].map(lambda v: CERT.get(v, v)),
                               "미배정/일": rs["unmet_per_day"].round(1),
                               "비중": (rs["share"] * 100).round(1).astype(str) + "%"}), hide_index=True)
    st.caption("최적화는 미배정 원인을 증명하지 못해 '계산 중 미해결'로 남깁니다(PRD 6.5). 원인은 위 진단 표를 보세요.")

st.subheader("3. 내려받기")
col1, col2, col3 = st.columns(3)
col1.download_button("리포트(Markdown)", to_markdown(r).encode("utf-8"), "shortage_report.md", "text/markdown")
sc = pd.concat([x.assign(algorithm=k) for k, x in r["tables"].items()]).assign(
    source=r["days_path"].name, paid_time=c["paid_time"], hourly_krw=c.get("hourly_krw"))
col2.download_button("시나리오 표(CSV)", sc.to_csv(encoding="utf-8-sig").encode("utf-8-sig"),
                     "shortage_scenarios.csv", "text/csv")
um = pd.concat([x.assign(by=k) for k, x in u.items()], ignore_index=True).assign(source=r["outcomes_path"].name)
col3.download_button("미배정 집계(CSV)", um.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                     "shortage_unmet.csv", "text/csv")
st.caption("인쇄용: 브라우저 인쇄(Ctrl+P)에서 'PDF로 저장'을 고르면 이 화면을 PDF로 저장할 수 있습니다. "
           "모든 내려받기 파일에 실험 파일 이름과 가정이 함께 들어갑니다.")
