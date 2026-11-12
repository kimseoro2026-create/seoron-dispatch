"""서로ON 돌봄배정 AI — 화면 진입점.

Streamlit Cloud 의 Main file 은 이 파일(app/main.py)이다. 페이지는 세 개:
  - 하루 배정 비교 (day_compare.py): 세 방식 비교, 타임라인, 미배정 사유, 승인, 30일 실험
  - 요청 카드 검토 (request_cards.py): 요청 원문 → 요청 카드 자동 정리와 담당자 확인
  - 부족 자원·비용 리포트 (shortage_report.py): 휴게 운영·인력 시나리오 비교, 미배정 진단, 비용
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

st.set_page_config(page_title="서로ON 돌봄배정 AI", layout="wide")

APP_DIR = Path(__file__).resolve().parent
nav = st.navigation([
    st.Page(str(APP_DIR / "day_compare.py"), title="하루 배정 비교", icon="📅", default=True),
    st.Page(str(APP_DIR / "request_cards.py"), title="요청 카드 검토", icon="📝"),
    st.Page(str(APP_DIR / "shortage_report.py"), title="부족 자원·비용 리포트", icon="📊"),
])
nav.run()
