"""등기부등본 PDF 파싱 — 선순위 채권(근저당 채권최고액)·권리 위험 추출."""
import re

from pypdf import PdfReader

# 갑구·을구 권리 위험 키워드
FLAG_KEYWORDS = ["신탁", "가압류", "압류", "경매개시", "임의경매", "강제경매", "가처분", "전세권"]


def parse_register(file) -> dict:
    """등기부 PDF에서 채권최고액·위험 키워드 추출."""
    try:
        reader = PdfReader(file)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        return {"ok": False, "error": f"PDF 읽기 실패: {exc}"}

    if not text.strip():
        return {"ok": False, "error": "텍스트를 추출하지 못했습니다(스캔본이면 불가)."}

    geunjeo = []
    for m in re.finditer(r"채권최고액[^0-9]*([\d,]+)\s*원", text):
        digits = m.group(1).replace(",", "")
        if digits.isdigit():
            geunjeo.append(int(digits))

    flags = [kw for kw in FLAG_KEYWORDS if kw in text]
    senior_manwon = sum(geunjeo) // 10000

    return {
        "ok": True,
        "geunjeo": geunjeo,
        "senior_manwon": senior_manwon,
        "flags": flags,
    }
