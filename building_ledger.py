"""건축물대장 조회 모듈 (표제부 + 전유공용면적)."""
import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

BR_TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
BR_EXPOS_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrExposPubuseAreaInfo"

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
    return {
        "serviceKey": key,
        "sigunguCd": codes.get("sigungu_cd", ""),
        "bjdongCd": codes.get("bjdong_cd", ""),
        "platGbCd": codes.get("plat_gb_cd", "0"),
        "bun": codes.get("bun", ""),
        "ji": codes.get("ji", ""),
        "numOfRows": "100", "pageNo": "1",
    }


def _fetch(url, params):
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


def get_title_info(codes, service_key, dong=""):
    if not service_key:
        return {"ok": False, "error": "건축물대장 인증키(BUILDING_LEDGER_API_KEY)가 없습니다."}
    params = _base_params(codes, urllib.parse.unquote(service_key))
    root, err = _fetch(BR_TITLE_URL, params)
    if err:
        return {"ok": False, "error": err}
    items = root.findall(".//item")
    if not items:
        return {"ok": True, "records": [], "note": "표제부 기록이 없습니다."}
    records = [{c.tag: (c.text or "").strip() for c in it} for it in items]
    if dong:  # 정확히 일치하는 동만 (부분일치 방지)
        exact = [r for r in records if (r.get("dongNm") or "") == dong]
        if exact:
            records = exact
    return {"ok": True, "records": records}


def get_expos_area(codes, service_key, dong="", ho=""):
    if not service_key:
        return {"ok": False, "error": "건축물대장 인증키가 없습니다."}
    params = _base_params(codes, urllib.parse.unquote(service_key))
    params["dongNm"] = dong
    if ho:
        params["hoNm"] = ho
    root, err = _fetch(BR_EXPOS_URL, params)
    if err:
        return {"ok": False, "error": err}
    items = root.findall(".//item")
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
