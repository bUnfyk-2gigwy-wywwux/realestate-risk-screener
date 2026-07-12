"""위험 판정 모듈. 수집한 공부 데이터로 신호등 신호를 만든다."""
from datetime import datetime

EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⬜"}


def assess_building(rec: dict) -> list:
    signals = []
    purps, etc = rec.get("mainPurpsCdNm", ""), rec.get("etcPurps", "")
    text = purps + etc
    if any(k in text for k in ["주택", "공동주택", "아파트"]):
        signals.append(("용도 적정성", "green", f"{purps}({etc}) · 주거용"))
    elif any(k in purps for k in ["근린생활", "업무", "판매", "공장", "숙박"]):
        signals.append(("용도 적정성", "red", f"{purps} · 주거 외 용도, 전입·대출 제약 위험"))
    else:
        signals.append(("용도 적정성", "yellow", f"{purps}({etc}) · 용도 확인 필요"))

    apr = rec.get("useAprDay", "")
    if len(apr) >= 4 and apr[:4].isdigit():
        years = datetime.now().year - int(apr[:4])
        if years < 20:
            signals.append(("노후도", "green", f"사용승인 {apr[:4]}년 · {years}년 경과"))
        elif years <= 35:
            signals.append(("노후도", "yellow", f"사용승인 {apr[:4]}년 · {years}년 경과(노후)"))
        else:
            signals.append(("노후도", "red", f"사용승인 {apr[:4]}년 · {years}년 경과(재건축 검토 구간)"))
    else:
        signals.append(("노후도", "yellow", "사용승인일 확인 불가"))

    if rec.get("rserthqkDsgnApplyYn", "") == "1":
        signals.append(("내진설계", "green", "적용"))
    else:
        signals.append(("내진설계", "yellow", "미적용(구축은 통상 미적용)"))

    signals.append(("위반건축물", "gray", "API 미제공 · 발급본 육안 확인 필요"))
    return signals


def assess_jeonse_ratio(deposit, market, senior_debt=0):
    """전세가율/부채비율 신호. 단위: 만원.

    WHY: 판정 결과가 안전(녹색)인데도 항목명이 항상 '깡통전세'로 표시되면
    부정적 인상을 준다. level별로 라벨을 분기해 안전 시 '깡통전세' 단어를 제거한다.
    """
    if not market or market <= 0:
        return ("전세가율 판정 불가", "gray", "시세 추정 불가 · 실거래가 확인 필요")
    ratio = (deposit + senior_debt) / market * 100
    detail = f"(보증금 {deposit:,} + 선순위 {senior_debt:,}) / 시세 {market:,} = {ratio:.0f}%"
    if ratio < 70:
        return ("전세가율 안전", "green", f"부채비율 {ratio:.0f}% 안전 · {detail}")
    if ratio < 85:
        return ("전세가율 주의", "yellow", f"부채비율 {ratio:.0f}% 주의 · {detail}")
    return ("깡통전세 위험", "red", f"부채비율 {ratio:.0f}% 위험 · {detail}")