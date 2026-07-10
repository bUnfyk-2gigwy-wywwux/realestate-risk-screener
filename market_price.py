"""실거래가 조회 모듈 (국토교통부). 시군구 단위로 매매 거래를 가져온다.

담당 도메인
  체결된 매매 실거래 데이터의 수집과 시세 추정. 호가·심리지표는 다루지
  않으며, 이 모듈의 산출값은 모두 체결 사실 데이터에서만 나온다.

제공 기능
  - get_trades(): 시군구코드(LAWD_CD) + 최근 N개월 범위로 매매 거래를 수집.
    아파트는 RTMSDataSvcAptTradeDev, 연립다세대는 RTMSDataSvcRHTrade를 쓰며
    단지명 태그가 각각 aptNm / mhouseNm으로 달라 NAME_TAG로 분기한다.
  - estimate_price(): 거래 목록의 중앙값으로 시세를 추정. area를 주면
    전용면적 ±area_tol(기본 15%) 이내 거래만 사용한다.

설계 메모
  - PER_PAGE=100: 실거래가 서버는 numOfRows가 크면 500을 반환하므로 작게
    잡고 totalCount 기반으로 페이지를 분할한다.
  - 에러 메시지에 요청 URL이 섞여 나올 수 있어 _mask()로 serviceKey를 가린다.
  - 표본이 없으면 근사치로 메꾸지 않고 estimate_price()가 None을 돌려준다.
  - 반환 계약: get_trades()는 실패 시 {"ok": False, "error": ...},
    성공 시 {"ok": True, "trades": [...], "raw_first": {...}}.

소비처
  app.py(시세 표시·전세가율 계산). 조회 키는 config.RTMS_API_KEY.
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

ENDPOINTS = {
    "아파트": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "연립다세대": "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
}
NAME_TAG = {"아파트": "aptNm", "연립다세대": "mhouseNm"}
PER_PAGE = 100  # 실거래가 서버는 큰 값에서 500을 내므로 작게 + 페이지 분할

# 에러 메시지 등에 URL이 섞여 나올 때 serviceKey 값을 가린다(라이브 키 노출 방지).
_KEY_RE = re.compile(r"(serviceKey=)[^&\s]+", re.I)


def _mask(text):
    return _KEY_RE.sub(r"\1***", str(text))


def _recent_months(n):
    y, m, out = datetime.now().year, datetime.now().month, []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def get_trades(lawd_cd, service_key, house_type="아파트", months=12):
    if not service_key:
        return {"ok": False, "error": "실거래가 인증키(RTMS_API_KEY)가 없습니다."}
    url = ENDPOINTS.get(house_type)
    name_tag = NAME_TAG.get(house_type, "aptNm")
    key = urllib.parse.unquote(service_key)

    trades, raw_first = [], {}
    first_error = None

    for ymd in _recent_months(months):
        page = 1
        while page <= 30:  # 안전장치
            params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": ymd,
                      "numOfRows": str(PER_PAGE), "pageNo": str(page)}
            try:
                resp = requests.get(url, params=params, timeout=15)
            except requests.exceptions.RequestException as exc:
                first_error = first_error or _mask(f"요청 실패: {exc}")
                break
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                first_error = first_error or _mask(
                    f"HTTP {resp.status_code} · 응답이 XML이 아닙니다. 본문 일부: {resp.text[:300]}")
                break
            code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
            msg = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "").strip()
            if code and code not in ("00", "000", "0"):
                first_error = first_error or _mask(f"HTTP {resp.status_code} · [{code}] {msg}")
                break

            items = root.findall(".//item")
            for it in items:
                if not raw_first:
                    raw_first = {c.tag: (c.text or "").strip() for c in it}
                amt = (it.findtext("dealAmount") or "").replace(",", "").strip()
                try:
                    amt_val = int(amt)
                except ValueError:
                    continue
                trades.append({
                    "단지": (it.findtext(name_tag) or "").strip(),
                    "전용면적": (it.findtext("excluUseAr") or "").strip(),
                    "거래금액(만원)": amt_val,
                    "층": (it.findtext("floor") or "").strip(),
                    "건축년도": (it.findtext("buildYear") or "").strip(),
                    "년월": f"{(it.findtext('dealYear') or '').strip()}.{(it.findtext('dealMonth') or '').strip()}",
                    "법정동": (it.findtext("umdNm") or "").strip(),
                })

            try:
                total = int((root.findtext(".//totalCount") or "0").strip() or "0")
            except ValueError:
                total = 0
            if not items or page * PER_PAGE >= total:
                break
            page += 1

    if not trades and first_error:
        return {"ok": False, "error": first_error, "raw_first": raw_first}
    return {"ok": True, "trades": trades, "raw_first": raw_first}


def estimate_price(trades, area=None, area_tol=0.15):
    vals = []
    for t in trades:
        if area:
            try:
                a = float(t["전용면적"])
                if a <= 0 or abs(a - area) / area > area_tol:
                    continue
            except ValueError:
                pass
        vals.append(t["거래금액(만원)"])
    if not vals:
        return None
    vals.sort()
    n, mid = len(vals), len(vals) // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) // 2
