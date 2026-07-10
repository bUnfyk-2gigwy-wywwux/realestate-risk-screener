# =============================================================================
# 파일: address.py
# 목적: 사람이 입력한 주소·단지명을 공부(公簿) 조회용 코드로 확정한다.
#       (주소 문자열 → 법정동코드 10자리 · 시군구코드 · 지번 본번/부번)
#
# 담당 도메인: 주소 해석 계층 (Address Resolution)
#   - 외부 의존: 도로명주소 API(business.juso.go.kr) · bjd_code.csv(전국 법정동코드)
#   - 공개 함수: search_address / find_by_name / load_bjd / bjd_codes_for_emd
#                name_match / name_variants
#   - 하위 소비자: building_ledger.py(건축물대장 조회 키), app.py(주소 검색 UI)
#
# 도메인 경계 (여기서 하지 않는 일):
#   - 실거래가 조회·단지 목록 열거 → market_price.py
#     (juso API는 지역명 단독 키워드로 건물을 열거하지 않음. 실측 확인됨)
#   - 위험 판정·신호등 산출 → risk.py
#   - 등기부 조회 → register.py
#
# 설계상 유의점:
#   - 행정구역 개편(예: 인천 서구 분구)으로 juso의 신코드와 건축HUB가 요구하는
#     구코드가 어긋난다. 그래서 CSV 역조회(bjd_codes_for_emd)와 시군구 없는
#     검색어 폴백(find_by_name)이 존재하며, 둘 다 제거하면 조회가 0건이 된다.
#   - 표기 편차(실거래명 ↔ 대장명 ↔ 도로명 건물명)는 name_match의 정규화
#     양방향 부분일치로 흡수한다. 자동 배제가 아니라 우선순위 조정만 한다.
# =============================================================================
"""도로명주소 API 연동 모듈.

주소 문자열을 검색해 건축물대장 등 공부 조회에 필요한
법정동코드·번지 코드로 변환합니다.

추가 기능(2026-07-03):
  - load_bjd(): 전국 법정동코드 CSV → {시도: {시군구: {읍면동: 법정동코드10}}}
  - find_by_name(): 읍면동 + 단지명으로 juso 정밀 검색 (단지명 축약 폴백 포함)

개선(2026-07-08):
  - name_match(): 정규화 양방향 부분일치 (실거래명 "한신그랜드힐" ↔
    대장/도로명 "한신그랜드힐빌리지"처럼 어느 쪽이 길어도 매칭)
  - find_by_name(): 시군구 없는 검색어 폴백 추가 — 행정구역 개편으로
    juso상 구 명칭이 바뀐 경우(예: 인천 서구 분구 개편) "구명 포함" 검색이
    0건이 되는 문제 대응. 읍면동 일치 필터가 있어 오지역 매칭은 차단됨.

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


def bjd_codes_for_emd(emd_nm: str, sido_prefix: str = "", path: str = BJD_CSV) -> list:
    """법정동 CSV에서 동명이 일치하는 법정동코드 10자리 목록.

    행정구역 개편(구 분할 등)으로 juso가 주는 신코드와 개편 미반영
    시스템(건축HUB 등)이 요구하는 구코드가 다를 때, CSV(개편 전 코드)를
    동명으로 역조회해 폴백 후보를 얻는다. sido_prefix(예: "28"=인천)로
    같은 시도 내 동명이인만 좁힌다.
    """
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = (row.get("code") or "").strip()
                if row.get("emd") == emd_nm and code and \
                        (not sido_prefix or code.startswith(sido_prefix)):
                    if code not in out:
                        out.append(code)
    except OSError:
        pass
    return out


def _norm(s: str) -> str:
    """단지명 비교용 정규화: 괄호내용·공백·하이픈·점 제거, 소문자화.

    예) "한신 그랜드힐(1차)" → "한신그랜드힐1차"... 괄호는 내용째 제거되므로
        "한신그랜드힐". 도로명·대장·실거래 간 표기 편차를 흡수한다.
    """
    s = re.sub(r"\(.*?\)", "", s or "")
    s = re.sub(r"[\s\-–·.']", "", s)
    return s.lower()


def name_match(a: str, b: str, min_len: int = 2) -> bool:
    """정규화 양방향 부분일치.

    정규화 후 짧은 쪽이 긴 쪽에 포함되면 매칭(방향 무관).
    예) "한신그랜드힐" ↔ "한신그랜드힐빌리지" → True (어느 쪽이 길어도)
        "루원" ↔ "루원시티푸르지오" → True
    오매칭 방지: 짧은 쪽이 min_len(기본 2자) 미만이면 불일치 처리.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) < min_len:
        return False
    return short in long_


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
    - 각 변형마다 [시군구+읍면동] → [읍면동만] 두 검색어를 시도
      (행정구역 개편으로 juso상 구 명칭이 바뀐 경우 대응 —
       읍면동 일치 필터가 있어 타지역 동명이인 매칭은 차단)
    - 읍면동명 일치 필터 + 공동주택(bdKdcd=1) 우선
    - juso 건물명(bdNm)이 요청 단지명과 양방향 매칭되는 후보를 우선 반환
      (없으면 전체 반환 — 자동 배제 대신 우선순위만 조정)

    반환:
        성공 → {"ok": True, "items": [...], "keyword": "사용된 검색어"}
        실패 → {"ok": False, "error": "사유"} (API 오류)
        미검출 → {"ok": True, "items": [], "keyword": ""}
    """
    last_err = None
    tried = set()
    for v in name_variants(name):
        for parts in ([sigungu, emd, v], [emd, v]):
            keyword = " ".join(x for x in parts if x)
            if not keyword or keyword in tried:
                continue
            tried.add(keyword)
            r = search_address(keyword, confm_key, per_page=50)
            if not r["ok"]:
                last_err = r["error"]
                continue
            matched = [i for i in r["items"] if not emd or not i.get("emd_nm") or i["emd_nm"] == emd]
            apt = [i for i in matched if i.get("bd_kdcd") == "1"] or matched
            if apt:
                named = [i for i in apt if name_match(name, i.get("bd_name", ""))]
                return {"ok": True, "items": named or apt, "keyword": keyword}
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
