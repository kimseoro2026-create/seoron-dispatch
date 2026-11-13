"""데이터 연결·출처 (W11) — 외부 자원마다 지금 상태와 근거. 화면을 열 때 외부로 호출하지 않는다.

PRD FR-45(mock/configured/verified/degraded/disabled), FR-46(마지막 갱신·쿼터·적용 기간·이용조건 검토 상태),
FR-47(키는 서버 비밀설정에만, 화면·내보내기에 노출 금지). 검수: API 실패를 0분·빈 값·정상 연결로 바꾸지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from adapters.status import adapter_states

STATE_KO = {"verified": "✅ 확인됨", "configured": "⚙️ 설정만 됨", "mock": "🧪 모의 데이터",
            "degraded": "⚠️ 대체값 사용", "disabled": "⏸ 사용 안 함"}

st.markdown(
    """
    <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
         padding:8px 16px;margin-bottom:12px;font-size:0.875rem;color:#856404;'>
    ⚠️ <b>가상 데이터 기반 실험</b> — 어르신·인력·요청은 모두 가상입니다.
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("데이터 연결·출처")
st.caption("이 앱이 쓰는 외부 자원이 지금 어떤 상태인지 보여줍니다. 화면을 열 때 외부로 호출하지 않고, "
           "이 앱에 들어 있는 데이터와 설정으로 판단합니다. 인증키 값은 보여주지 않습니다(설정됨/없음만).")

rows = adapter_states(ROOT)
counts = pd.Series([r["state"] for r in rows]).value_counts()
cols = st.columns(5)
for col, s in zip(cols, ("verified", "configured", "mock", "degraded", "disabled")):
    col.metric(STATE_KO[s], f"{int(counts.get(s, 0))}개")

st.dataframe(pd.DataFrame([{
    "자원": r["name"], "상태": STATE_KO[r["state"]], "근거": r["evidence"], "제공처": r["provider"],
    "쓰는 곳": r["used_for"], "인증키": r["key"], "마지막 갱신": r["last_update"] or "-",
    "적용 기간": r["period"], "쿼터": r["quota"], "이용조건 검토": r["terms"],
} for r in rows]), hide_index=True, width="stretch")

st.markdown("""
**상태 뜻**
- ✅ **확인됨**: 그 자원에서 받은 값이 이 앱 데이터에 실제로 들어 있습니다.
- ⚙️ **설정만 됨**: 키·주소는 있지만 받은 값을 찾지 못했습니다.
- 🧪 **모의 데이터**: 가상으로 만든 값을 씁니다.
- ⚠️ **대체값 사용**: 받으려던 값 대신 대략값(예: 직선거리 추정 이동시간)을 씁니다. 화면에도 그렇게 표시합니다.
- ⏸ **사용 안 함**: 아직 쓰지 않습니다.
""")
st.caption("쿼터와 이용조건 검토는 아직 기록이 없어 '확인 안 됨·확인 필요'로 둡니다. 실패한 호출을 0분이나 빈 값, '정상 연결'로 "
           "바꾸지 않습니다. 키는 서버의 .env(저장소 제외)에만 둡니다.")
