"""행안부 인구 API 응답 → 행정동별 고령인구(f0.sources.population_rows_to_frame)."""
import pytest

from f0.apiclient import load_api_config, parse_response
from f0.sources import AGE65_METHOD, population_rows_to_frame

FM = load_api_config()["population"]["field_map"]
AGES = [f"{s}{a}AgeNmprCnt" for s in ("male", "feml") for a in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)]

XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><head><resultCode>0</resultCode><resultMsg>NORMAL_SERVICE</resultMsg><totalCount>2</totalCount>
<numOfRows>100</numOfRows><pageNo>1</pageNo></head><items>
<item><statsYm>202610</statsYm><admmCd>4159700000</admmCd><ctpvNm>경기도</ctpvNm><sggNm>화성시 동탄구</sggNm>
<dongNm></dongNm><totNmprCnt>1000</totNmprCnt>{sgg}</item>
<item><statsYm>202610</statsYm><admmCd>4159751000</admmCd><ctpvNm>경기도</ctpvNm><sggNm>화성시 동탄구</sggNm>
<dongNm>동탄1동</dongNm><totNmprCnt>500</totNmprCnt>{dong}</item>
</items></response>"""


def _ages(val60, val70):
    return "".join(f"<{k}>{val60 if '60Age' in k else val70 if any(x in k for x in ('70Age', '80Age', '90Age', '100Age')) else 1}</{k}>"
                   for k in AGES)


def test_parse_and_sum():
    items, total = parse_response(XML.format(sgg=_ages(99, 99), dong=_ages(11, 5)), "totalCount")
    assert total == 2
    df = population_rows_to_frame(items, FM)
    assert list(df["dong"]) == ["동탄1동"]          # 행정동명이 빈 시군구 합계 행은 뺀다
    r = df.iloc[0]
    assert r["age_70_plus"] == 5 * 8                 # 남녀 × 70·80·90·100세 이상
    assert r["age_60s"] == 22
    assert r["age_65_plus"] == 40 + 11               # 대략값: 60대 절반
    assert r["age_65_plus_method"] == AGE65_METHOD and r["age_65_plus_method"].startswith("estimate")
    assert r["admm_cd"] == "4159751000" and r["stats_ym"] == "202610"


def test_blank_count_is_error_not_zero():
    items, _ = parse_response(XML.format(sgg="", dong=_ages(11, "")), "totalCount")
    with pytest.raises(ValueError):
        population_rows_to_frame(items, FM)
