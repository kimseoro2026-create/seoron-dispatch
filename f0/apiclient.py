"""디지털융합플랫폼 API 공통 클라이언트.

- config/apis.yaml의 URL·요청변수 템플릿을 채워 호출한다.
- 인증키는 환경변수 KOREACONNECT_API_USER_KEY_ID에서 읽어 헤더로 보낸다.
- 응답이 JSON이든 XML이든 'item' 목록을 꺼내 dict 리스트로 돌려준다.
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "apis.yaml"


class ApiNotConfigured(RuntimeError):
    """apis.yaml에 URL이 아직 채워지지 않았을 때."""


def load_api_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fill(template: Any, values: dict) -> Any:
    if isinstance(template, str) and template.startswith("{") and template.endswith("}"):
        key = template[1:-1]
        if key not in values:
            raise KeyError(f"요청변수 값 '{key}'가 필요합니다")
        return values[key]
    return template


def _find_items_json(obj: Any) -> list[dict]:
    """JSON 어디에 있든 'item'(또는 'items' 리스트)을 찾아 꺼낸다."""
    if isinstance(obj, dict):
        if "item" in obj:
            item = obj["item"]
            return item if isinstance(item, list) else [item]
        for v in obj.values():
            found = _find_items_json(v)
            if found:
                return found
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        return obj
    return []


def _find_value_json(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_value_json(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_value_json(v, key)
            if found is not None:
                return found
    return None


def parse_response(text: str, total_field: str | None = None) -> tuple[list[dict], int | None]:
    """응답 본문 → (item 목록, 전체 건수)."""
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        items = _find_items_json(data)
        total = _find_value_json(data, total_field) if total_field else None
        return items, int(total) if total is not None else None
    root = ET.fromstring(text)
    items = [{child.tag: (child.text or "").strip() for child in el} for el in root.iter("item")]
    total = None
    if total_field:
        node = root.find(f".//{total_field}")
        if node is not None and node.text:
            total = int(node.text)
    return items, total


class PlatformClient:
    def __init__(self, api_key: str | None = None, config: dict | None = None):
        self.config = config or load_api_config()
        self.api_key = api_key or os.environ.get("KOREACONNECT_API_USER_KEY_ID", "")
        self.session = requests.Session()

    def call(self, api_name: str, **values) -> tuple[list[dict], int | None]:
        spec = self.config[api_name]
        url = spec.get("url", "")
        if not url or url.startswith("TODO"):
            raise ApiNotConfigured(
                f"config/apis.yaml의 '{api_name}.url'을 플랫폼 상세 화면의 호출 URL로 채워주세요"
            )
        if not self.api_key:
            raise ApiNotConfigured(".env의 KOREACONNECT_API_USER_KEY_ID(플랫폼 인증키)가 비어 있습니다")
        params = {k: _fill(v, values) for k, v in (spec.get("params") or {}).items()}
        headers = {spec.get("key_header", self.config["key_header"]): self.api_key}
        timeout = self.config.get("timeout_sec", 20)
        resp = self._request_with_retry(spec, url, params, headers, timeout)
        # XML 응답은 UTF-8을 선언하지만 requests가 ISO-8859-1로 추정할 수 있어
        # content(bytes)를 직접 UTF-8로 디코딩한다.
        content_type = resp.headers.get("Content-Type", "")
        body = resp.content.decode("utf-8") if "xml" in content_type.lower() else resp.text
        # XML 응답의 resultCode가 00이 아니면 오류로 처리한다.
        if "xml" in content_type.lower():
            _root = ET.fromstring(body)
            _code = _root.findtext(".//resultCode") or ""
            if _code and _code != "00":
                _msg = _root.findtext(".//resultMsg") or ""
                raise RuntimeError(f"API 오류 resultCode={_code}: {_msg}")
        return parse_response(body, spec.get("total_count_field"))

    def _request_with_retry(self, spec: dict, url: str, params: dict,
                            headers: dict, timeout: int) -> requests.Response:
        """플랫폼이 빠른 연속 요청에 503을 돌려준다. 지수 백오프로 다시 시도한다."""
        retries = int(spec.get("retries", self.config.get("retries", 4)))
        backoff = float(spec.get("retry_backoff_sec", self.config.get("retry_backoff_sec", 3)))
        last_err = ""
        for attempt in range(retries + 1):
            try:
                if spec.get("method", "GET").upper() == "POST":
                    resp = self.session.post(url, json=params, headers=headers, timeout=timeout)
                else:
                    resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                resp = None
            if resp is not None and resp.status_code < 500:
                return resp
            if resp is not None:
                last_err = f"HTTP {resp.status_code}"
            if attempt < retries:
                sleep_for = backoff * (2 ** attempt)
                print(f"  retry {attempt + 1}/{retries} ({last_err}) - {sleep_for:.0f}sec")
                time.sleep(sleep_for)
        raise RuntimeError(f"{retries + 1}회 시도했지만 실패했습니다: {last_err}")

    def call_all_pages(self, api_name: str, max_pages: int = 50,
                       page_delay_sec: float | None = None, **values) -> list[dict]:
        """pageNo를 올려가며 전체 건수만큼 받는다.

        빈 페이지가 와도 곧바로 멈추지 않고 지금까지 모은 건수와 totalCount를 비교한다.
        서버 오류는 call()이 재시도하고, 끝내 실패하면 예외로 멈춘다.
        """
        delay = page_delay_sec if page_delay_sec is not None else float(
            self.config.get("page_delay_sec", 1.0)
        )
        rows: list[dict] = []
        total: int | None = None
        empty_streak = 0
        for page in range(1, max_pages + 1):
            items, page_total = self.call(api_name, page=page, **values)
            if page_total is not None:
                total = page_total
            rows.extend(items)
            if total is not None and len(rows) >= total:
                break
            if not items:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0
            if page < max_pages:
                time.sleep(delay)
        if total is not None and len(rows) < total:
            print(f"  경고: {len(rows)}건만 받았습니다(전체 {total}건). 페이지 수집이 중간에 끝났습니다.")
        return rows
