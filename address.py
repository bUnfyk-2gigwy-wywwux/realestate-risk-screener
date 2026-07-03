"""도로명주소 API 연동 모듈.

주소 문자열을 검색해 건축물대장 등 공부 조회에 필요한
법정동코드·번지 코드로 변환합니다.

추가 기능(2026-07-03):
  - load_bjd(): 전국 법정동코드 CSV → {시도: {시군구: {읍면동: 법정동코드10}}}
  - find_by_name(): 읍면동 + 단지명으로 juso 정밀 검색 (단지명 축약 폴백 포함)

※ juso API는 지역명 단독 키워드로는 건물을 열거하지 않으므로(실측 1건),
   단지 목록은 실거래가(RTMS)에서 추출하고 juso는 번지 확정에만 사용한다.

API: business.juso.go.kr/addrlink/addrLinkApi.do
"""
import csv
import os
import re

import requests

JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
BJD_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bjd_code.csv")


def search_address(keyword: str, confm_key: str, page: int = 1, per_page: int = 10) -> dict:
    """주소 검색.

    반환:
        성공 → {"ok": True, "total": int, "items": [parsed, ...]}
        실패 → {"ok": False, "error": "사유"}
    """
    if not keyword or not keyword.strip():
        return {"ok": False, "error": "검색어를 입력하세요."}
    if not confm_key:
        return {"ok": False, "error": "도로명주소 승인키(JUSO_API_KEY)가 설정되지 않았습니다."}

    params = {
        "confmKey": confm_key,
        "currentPage": page,
        "countPerPage": per_page,
        "keyword": keyword.strip(),
        "resultType": "json",
    }

    try:
        resp = requests.get(JUSO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"API 호출 실패: {exc}"}
    except ValueError:
        return {"ok": False, "error": "응답 파싱 실패(JSON 아님). 승인키·네트워크를 확인하세요."}

    common = data.get("results", {}).get("common", {})
    if common.get("errorCode") != "0":
        return {
            "ok": False,
            "error": f"[{common.get('errorCode')}] {common.get('errorMessage')}",
        }

    juso_list = data.get("results", {}).get("juso") or []
    items = [_parse(j) for j in juso_list]
    return {"ok": True, "total": int(common.get("totalCount", 0)), "items": items}


def load_bjd(path: str = BJD_CSV) -> dict:
    """전국 법정동코드 CSV를 계층 dict로 로드한다.

    반환: {시도: {시군구: {읍면동: 법정동코드 10자리}}}
        세종특별자치시처럼 시군구가 없는 경우 시군구 키는 "" 이다.
    """
    tree = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tree.setdefault(row["sido"], {}) \
                .setdefault(row["sigungu"], {})[row["emd"]] = row["code"]
    return tree


def name_variants(name: str, max_variants: int = 5) -> list:
    """실거래 단지명 → juso 검색용 축약 변형 목록.

    실거래 등록명과 도로명주소 건물명이 다른 경우(예: 한신그랜드힐 vs
    한신그랜드힐빌리지)를 위해 괄호 제거 → 차수/단지 접미 제거 →
    말단 1자씩 축약(최소 3자) 순으로 폴백 후보를 만든다.
    """
    base = (name or "").strip()
    variants = []

    def _add(v):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)

    _add(base)
    _add(re.sub(r"\(.*?\)", "", base))                        # 괄호 제거
    _add(re.sub(r"\s*(제?\d+(차|단지|동))$", "", variants[-1]))  # 차수·단지 접미 제거
    cur = variants[-1]
    while len(cur) > 3 and len(variants) < max_variants:      # 말단 축약
        cur = cur[:-1]
        _add(cur)
    return variants[:max_variants]


def find_by_name(sigungu: str, emd: str, name: str, confm_key: str) -> dict:
    """읍면동 + 단지명으로 juso를 검색해 번지 후보를 확정한다.

    - 단지명 원형부터 축약 변형 순으로 시도, 첫 매칭에서 중단
    - 읍면동명 일치 필터 + 공동주택(bdKdcd=1) 우선
    - 후보가 여럿(같은 단지 복수 번지)이면 전부 반환 → 앱에서 선택

    반환:
        성공 → {"ok": True, "items": [...], "keyword": "사용된 검색어"}
        실패 → {"ok": False, "error": "사유"} (API 오류)
        미검출 → {"ok": True, "items": [], "keyword": ""}
    """
    last_err = None
    for v in name_variants(name):
        keyword = " ".join(x for x in [sigungu, emd, v] if x)
        r = search_address(keyword, confm_key, per_page=50)
        if not r["ok"]:
            last_err = r["error"]
            continue
        matched = [i for i in r["items"] if not emd or not i.get("emd_nm") or i["emd_nm"] == emd]
        apt = [i for i in matched if i.get("bd_kdcd") == "1"] or matched
        if apt:
            return {"ok": True, "items": apt, "keyword": keyword}
    if last_err:
        return {"ok": False, "error": last_err}
    return {"ok": True, "items": [], "keyword": ""}


def _parse(j: dict) -> dict:
    """juso 항목에서 공부 조회용 코드를 추출한다."""
    adm_cd = j.get("admCd", "") or ""
    jibun = j.get("jibunAddr", "") or ""

    return {
        "road_addr": j.get("roadAddr", ""),
        "jibun_addr": jibun,
        "bd_name": j.get("bdNm", ""),
        "zip_no": j.get("zipNo", ""),
        "adm_cd": adm_cd,                 # 법정동코드 10자리
        "sigungu_cd": adm_cd[:5],         # 시군구코드
        "bjdong_cd": adm_cd[5:10],        # 법정동코드(읍면동) 5자리
        "bun": _pad(j.get("lnbrMnnm", "")),   # 지번 본번
        "ji": _pad(j.get("lnbrSlno", "")),    # 지번 부번
        # 산 여부: 지번주소에 독립 토큰 '산'이 있으면 1(산), 없으면 0(대지)
        "plat_gb_cd": "1" if "산" in jibun.split() else "0",
        "bd_kdcd": j.get("bdKdcd", ""),   # 공동주택여부 (1: 공동주택)
        "emd_nm": j.get("emdNm", ""),     # 읍면동명
    }


def _pad(num_str: str) -> str:
    """번지 숫자를 4자리 0채움 문자열로 변환."""
    try:
        return str(int(num_str)).zfill(4)
    except (ValueError, TypeError):
        return "0000"
