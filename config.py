"""설정·상수 모듈.

.env 의 API 키와 진단 임계값을 한곳에서 관리합니다.
"""
import os

from dotenv import load_dotenv

load_dotenv()

try:
    import streamlit as _st
    for _k, _v in _st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass


# ===== API 키 =====
JUSO_API_KEY = os.getenv("JUSO_API_KEY", "")
BUILDING_LEDGER_API_KEY = os.getenv("BUILDING_LEDGER_API_KEY", "")
LAND_USE_API_KEY = os.getenv("LAND_USE_API_KEY", "")
HOUSING_PRICE_API_KEY = os.getenv("HOUSING_PRICE_API_KEY", "")
RTMS_API_KEY = os.getenv("RTMS_API_KEY", "")

# ===== 거래유형 =====
DEAL_TYPES = ["전세", "월세", "매매"]

# ===== 신호등 임계값 (구현 단계에서 최신 기준으로 확정 예정) =====
# 부채비율(%) = (선순위 채권최고액 + 보증금) / 추정 시세
DEBT_RATIO_WARN = 70     # 이상이면 주의(노랑)
DEBT_RATIO_DANGER = 85   # 이상이면 위험(빨강)
