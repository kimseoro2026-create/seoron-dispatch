"""서로ON 돌봄배정 AI — 화면 진입점.

Streamlit Cloud 의 Main file 은 이 파일(app/main.py)이다. 페이지:
  - 오늘의 현황 (status_board.py): 날짜별 요청 상태 집계, 먼저 볼 것(첫 화면)
  - 하루 배정 비교 (day_compare.py): 세 방식 비교, 타임라인, 미배정 사유, 승인, 30일 실험
  - 취소·지연 대응 (events.py): 확정 일정에 취소·지연 등록 → 바뀔 일정 제안 → 담당자 적용
  - 자료 불러오기 (import_data.py): CSV → 검토 전 요청 → 담당자 확인
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

# 배포 서버가 push 전의 planner·intake 모듈을 붙잡고 있으면 다시 읽는다(app/_fresh.py 참고)
from app._fresh import reload_stale  # noqa: E402

reload_stale(ROOT)

APP_DIR = Path(__file__).resolve().parent
nav = st.navigation([
    st.Page(str(APP_DIR / "status_board.py"), title="오늘의 현황", icon="🏠", default=True),
    st.Page(str(APP_DIR / "day_compare.py"), title="하루 배정 비교", icon="📅"),
    st.Page(str(APP_DIR / "events.py"), title="취소·지연 대응", icon="🔁"),
    st.Page(str(APP_DIR / "import_data.py"), title="자료 불러오기", icon="📥"),
    st.Page(str(APP_DIR / "request_cards.py"), title="요청 카드 검토", icon="📝"),
    st.Page(str(APP_DIR / "shortage_report.py"), title="부족 자원·비용 리포트", icon="📊"),
])
nav.run()
