"""도로명주소 API 연동 모듈.

주소 문자열을 검색해 건축물대장 등 공부 조회에 필요한
법정동코드·번지 코드로 변환합니다.

API: business.juso.go.kr/addrlink/addrLinkApi.do
"""
import requests

JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


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
    }


def _pad(num_str: str) -> str:
    """번지 숫자를 4자리 0채움 문자열로 변환."""
    try:
        return str(int(num_str)).zfill(4)
    except (ValueError, TypeError):
        return "0000"
