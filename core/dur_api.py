"""식약처 DUR 성분정보 오픈 API 클라이언트 (data.go.kr 1471000/DURIrdntInfoService03).

7개 금기/주의 유형을 REST(JSON)로 제공. 페이지네이션으로 전량 수집.

⚠️ 서비스키는 시크릿 — 코드에 하드코딩하지 않는다. 호출 시 인자/환경변수(DUR_API_KEY)로 주입.
공개 데이터(환자정보 없음). 응답 구조: body.items[].item, totalCount.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BASE = "https://apis.data.go.kr/1471000/DURIrdntInfoService03"

# 표시명 → 오퍼레이션. dur_type 라벨로도 사용.
OPERATIONS = {
    "병용금기": "getUsjntTabooInfoList02",
    "연령금기": "getSpcifyAgrdeTabooInfoList02",
    "임부금기": "getPwnmTabooInfoList02",
    "용량주의": "getCpctyAtentInfoList02",
    "투여기간주의": "getMdctnPdAtentInfoList02",
    "노인주의": "getOdsnAtentInfoList02",
    "효능군중복": "getEfcyDplctInfoList02",
}


def fetch_all(operation: str, service_key: str, num_rows: int = 100,
              max_pages: int = 2000, sleep: float = 0.0, base: str = BASE) -> list:
    """data.go.kr 서비스 오퍼레이션 전량 수집 → item dict 리스트. totalCount 도달 시 종료.

    base는 서비스 엔드포인트(기본 DUR성분정보). item 래퍼 유무 모두 처리(e약은요 등 호환).
    """
    items: list = []
    page = 1
    holes: list[int] = []          # 200 OK인데 빈 배열이 온 중간 페이지
    while page <= max_pages:
        params = {"serviceKey": service_key, "pageNo": page,
                  "numOfRows": num_rows, "type": "json"}
        url = base + "/" + operation + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 (gov API, https)
            d = json.load(r)
        body = d.get("body") or {}
        rows = body.get("items") or []
        if isinstance(rows, dict):
            rows = [rows]
        for it in rows:
            rec = it.get("item") if isinstance(it, dict) and "item" in it else it
            if rec:
                items.append(rec)
        try:
            total = int(body.get("totalCount") or 0)
        except (TypeError, ValueError):
            total = 0
        if total and page * num_rows >= total:
            break                       # 정상 종료 — totalCount를 다 받았다
        if not rows:
            # 🔴 2026-08-06: 치료재료 API에서 **중간 페이지가 200 OK + 빈 배열**을 주는데
            # `if not rows: break`가 그것을 끝으로 읽어 59,000/91,377행에서 조기 종료했고
            # 정본 32,377행이 조용히 사라졌다. 같은 형태가 여기에도 있었다.
            # totalCount가 남아 있는데 비었다면 **끝이 아니라 구멍**이다.
            if not total:
                break                   # totalCount 미상 — 끝으로 볼 수밖에 없다
            holes.append(page)
        page += 1
        if sleep:
            time.sleep(sleep)

    for p in holes:                     # 구멍 재시도(일과성이면 여기서 회복)
        if sleep:
            time.sleep(sleep)
        try:
            params = {"serviceKey": service_key, "pageNo": p,
                      "numOfRows": num_rows, "type": "json"}
            url = base + "/" + operation + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
                d = json.load(r)
            rows = (d.get("body") or {}).get("items") or []
            if isinstance(rows, dict):
                rows = [rows]
        except Exception as e:  # noqa: BLE001 — 재시도 실패는 비차단(경고 후 진행)
            logger.warning("[dur-api] %s page %d 재시도 실패: %s", operation, p, str(e)[:80])
            continue
        if not rows:
            logger.warning("[dur-api] %s page %d 재시도 후에도 빈 응답 — 누락 가능",
                           operation, p)
            continue
        for it in rows:
            rec = it.get("item") if isinstance(it, dict) and "item" in it else it
            if rec:
                items.append(rec)
    return items
