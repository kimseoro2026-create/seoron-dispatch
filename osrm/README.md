# OSRM 경로 서버

가상 구의 노드(기관·인력·어르신·병원) 사이 **차량 이동시간 행렬**을 무료로 계산하기 위한 서버입니다.

## 준비 (최초 1회, 20~40분)

```bash
cd osrm
./prepare.sh            # 지도 내려받기 → 경로 그래프 생성
docker compose up -d    # 서버 실행 (포트 5000)
curl "http://localhost:5000/route/v1/driving/127.075,37.2;127.06,37.21"   # 동작 확인
```

- 필요: Docker, 디스크 2GB, 메모리 4GB 이상(osmium으로 자르지 않으면 6GB 이상)
- `osmium-tool`이 있으면 경기 남부만 잘라 처리해 훨씬 빠릅니다 (`brew install osmium-tool` 또는 `apt install osmium-tool`)
- 지도 갱신은 한 달에 한 번 `prepare.sh`를 다시 실행하면 됩니다

## 서버를 켠 뒤

프로젝트 루트의 `.env`에 `OSRM_URL=http://localhost:5000`을 넣고 `python -m f0 matrix`를 실행하면
`travel_matrix.csv`의 `source` 열이 `osrm`으로 바뀝니다.

## 주의

OSRM 자동차 프로필은 **막히지 않는 도로 기준** 소요시간이라 실제보다 짧게 나옵니다.
카카오모빌리티 길찾기로 20~30개 구간을 샘플 검증한 뒤, 평균 비율을 `.env`의 `OSRM_TIME_FACTOR`(예: 1.3)로 넣어 보정합니다.
