"""건축물대장 조회 모듈 (표제부 + 전유공용면적).

- 에러 메시지의 serviceKey 마스킹(라이브 키 노출 방지).
- 표제부·전유부 모두 totalCount 기반 페이지네이션으로 전 항목 수집
  (기존 100행 고정 → 대단지 호수 누락 문제 해결).
- 동 정보가 없을 때 UI가 넣는 표시용 문자열은 API로 보내지 않음(전유부 0건 방지).
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

BR_TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
BR_EXPOS_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrExposPubuseAreaInfo"

PER_PAGE = 100          # 페이지당 행수
MAX_PAGES = 30          # 안전 상한 (100 × 30 = 3,000행)

# 동 정보가 없을 때 UI가 넣는 표시용 문자열 — API로 전송하면 0건이 되므로 걸러낸다.
_DONG_PLACEHOLDERS = {"(동 없음)", "(동 정보 없음)"}

# 에러 메시지 등에 URL이 섞여 나올 때 serviceKey 값을 가린다(라이브 키 노출 방지).
_KEY_RE = re.compile(r"(serviceKey=)[^&\s]+", re.I)


def _mask(text):
    return _KEY_RE.sub(r"\1***", str(text))


FIELDS = {
    "bldNm": "건물명", "dongNm": "동명칭", "mainPurpsCdNm": "주용도",
    "etcPurps": "기타용도", "strctCdNm": "구조", "useAprDay": "사용승인일",
    "grndFlrCnt": "지상층수", "ugrndFlrCnt": "지하층수", "totArea": "연면적(㎡)",
    "hhldCnt": "세대수", "rideUseElvtCnt": "승용승강기",
    "rserthqkDsgnApplyYn": "내진설계적용",
}


def _base_params(codes, key):
    """페이지 파라미터를 제외한 공통 파라미터. numOfRows/pageNo는 페이지네이션이 채운다."""
    return {
        "serviceKey": key,
        "sigunguCd": codes.get("sigungu_cd", ""),
        "bjdongCd": codes.get("bjdong_cd", ""),
        "platGbCd": codes.get("plat_gb_cd", "0"),
        "bun": codes.get("bun", ""),
        "ji": codes.get("ji", ""),
    }


def _fetch_page(url, params):
    """단일 페이지 조회 → (root, error)."""
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.exceptions.RequestException, ET.ParseError) as exc:
        return None, _mask(f"조회 실패: {exc}")
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("00", "0"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        return None, _mask(f"[{code}] {msg}")
    return root, None


def _fetch_all(url, base_params):
    """totalCount 기준으로 전 페이지 item 을 모은다 → (items, error).

    첫 페이지가 실패하면 에러를 반환하고, 2페이지 이후 실패는
    지금까지 모은 항목으로 진행한다(부분 성공 허용).
    """
    all_items = []
    page = 1
    while page <= MAX_PAGES:
        params = dict(base_params, numOfRows=str(PER_PAGE), pageNo=str(page))
        root, err = _fetch_page(url, params)
        if err:
            if page == 1:
                return None, err
            break
        items = root.findall(".//item")
        all_items.extend(items)
        try:
            total = int((root.findtext(".//totalCount") or "0").strip() or "0")
        except ValueError:
            total = 0
        if not items or page * PER_PAGE >= total:
            break
        page += 1
    return all_items, None


def get_title_info(codes, service_key, dong=""):
    if not service_key:
        return {"ok": False, "error": "건축물대장 인증키(BUILDING_LEDGER_API_KEY)가 없습니다."}
    base = _base_params(codes, urllib.parse.unquote(service_key))
    items, err = _fetch_all(BR_TITLE_URL, base)
    if err:
        return {"ok": False, "error": err}
    if not items:
        return {"ok": True, "records": [], "note": "표제부 기록이 없습니다."}
    records = [{c.tag: (c.text or "").strip() for c in it} for it in items]
    if dong and dong not in _DONG_PLACEHOLDERS:  # 정확히 일치하는 동만 (부분일치 방지)
        exact = [r for r in records if (r.get("dongNm") or "") == dong]
        if exact:
            records = exact
    return {"ok": True, "records": records}


def get_expos_area(codes, service_key, dong="", ho=""):
    if not service_key:
        return {"ok": False, "error": "건축물대장 인증키가 없습니다."}
    base = _base_params(codes, urllib.parse.unquote(service_key))
    # 표시용 placeholder("(동 없음)" 등)는 API로 보내지 않는다 → 전유부 0건 방지.
    if dong and dong not in _DONG_PLACEHOLDERS:
        base["dongNm"] = dong
    if ho:
        base["hoNm"] = ho
    items, err = _fetch_all(BR_EXPOS_URL, base)
    if err:
        return {"ok": False, "error": err}
    if not items:
        return {"ok": True, "rows": [], "raw_first": {}}
    rows = []
    raw_first = {c.tag: (c.text or "").strip() for c in items[0]}
    for it in items:
        try:
            area_val = float((it.findtext("area") or "0").strip())
        except ValueError:
            area_val = 0.0
        rows.append({
            "구분": (it.findtext("exposPubuseGbCdNm") or "").strip(),
            "호": (it.findtext("hoNm") or "").strip(),
            "용도": (it.findtext("mainPurpsCdNm") or "").strip(),
            "면적(㎡)": area_val,
        })
    return {"ok": True, "rows": rows, "raw_first": raw_first}
