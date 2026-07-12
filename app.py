import re
from collections import Counter

import streamlit as st

import config
import address
import building_ledger
import market_price
import register
import risk

import pandas as pd

# ── 상담 유입(리드) 설정 ─────────────────────────────────────────────
# 안심전세앱(정부) 대비 포지셔닝: 진단은 정부가, 해석·상담·거래 연결은 계양부동산.
CONSULT_LANDING = "https://hongskier-99491.waveon.me"  # Waveon 상담폼(8필드)
CONSULT_KAKAO = "https://pf.kakao.com/_NmKTX/chat"       # 카카오 1:1 상담
CONSULT_TEL = "010-2769-2799"

RESET_KEYS = ["br_all", "expos_cache", "all_trades", "est_price",
              "expos_area", "bld_nm", "reg", "trade_nm",
              "trades_raw", "trades_error"]
COMPLEX_FILTER_THRESHOLD = 30   # 단지 수가 이 값을 넘으면 보조 필터 노출
TRADES_CAP = 5000               # 세션에 담는 실거래 최대 건수(메모리 누적 방지)


def _to_df(rows, limit=1000):
    """st.dataframe 안전 변환.

    WHY: 딕셔너리 리스트를 st.dataframe에 직접 넘기면, 혼합타입 object 컬럼을
    pyarrow가 직렬화하다 대량(연립다세대 등)에서 세그폴트로 앱이 죽는다.
    pandas로 명시 변환 + 숫자/문자 타입 정규화 + 행수 제한으로 크래시를 차단한다.
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows[:limit])
    num_cols = {"거래금액(만원)", "면적(㎡)"}
    for c in df.columns:
        if c in num_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = df[c].astype(str)
    return df


def _num_key(s):
    parts = re.split(r"(\d+)", s or "")
    return [int(p) if p.isdigit() else p for p in parts]


def _to_float(s):
    try:
        return float(s) if s and s.strip() else None
    except ValueError:
        return None


def _reset_downstream():
    for k in RESET_KEYS:
        st.session_state.pop(k, None)


def _label(i):
    return i["road_addr"] + (f" ({i['bd_name']})" if i["bd_name"] else "")


@st.cache_data
def _load_bjd():
    return address.load_bjd()


@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def _trades_cached(sgg_cd, htype, months):
    return market_price.get_trades(sgg_cd, config.RTMS_API_KEY,
                                   house_type=htype, months=months)


st.set_page_config(page_title="사전 위험 진단기", page_icon="🏠", layout="centered")
st.title("🏠 사전 위험 진단기")
st.caption("계약 전 매물 위험을 신호등으로 진단합니다")
st.markdown("🔴 **위험** 🟡 **주의** 🟢 **안전**")
st.info("표제부 + 전유부 + 시세 + 등기부(선순위 채권) 연동")

deal_type = st.radio("거래유형", config.DEAL_TYPES, horizontal=True)

# src 유입경로 태깅 (판단기준서 5-5: 유입면 → leads 릴레이)
src_tag = st.query_params.get("src", "app")
landing_url = f"{CONSULT_LANDING}?src={src_tag}"


def _consult_cta(context: str):
    """상담 CTA 2버튼(랜딩 폼 + 카카오)을 렌더. context는 src에 부기."""
    url = f"{CONSULT_LANDING}?src={src_tag}_{context}"
    b1, b2 = st.columns(2)
    with b1:
        st.link_button("🛡️ 무료 안심 상담 신청", url, use_container_width=True)
    with b2:
        st.link_button("💬 카카오 1:1 상담", CONSULT_KAKAO, use_container_width=True)


# 상단 상시 안내 배너
with st.container():
    st.markdown(
        "🛡️ **안심전세앱에서 '주의·위험'이 나왔거나 계약 전 확인이 필요하신가요?** "
        "정부 앱은 위험을 알려주고, 실제 계약 가능 여부·안전한 대안은 계양부동산이 함께 봅니다.")
    _consult_cta("top")
    st.divider()

tab_step, tab_direct = st.tabs(["📍 단계 선택 (공동주택)", "⌨️ 직접 입력"])

# ── 탭 1: 시/도 → 시군구 → 읍면동 → 단지(실거래 추출) 캐스케이드 ──────
with tab_step:
    try:
        bjd = _load_bjd()
    except FileNotFoundError:
        bjd = None
        st.error("bjd_code.csv 파일이 없습니다. 앱 폴더에 배치 후 새로고침하세요.")

    if bjd:
        sido_list = list(bjd.keys())
        sido_idx = sido_list.index("인천광역시") if "인천광역시" in sido_list else 0
        c1, c2, c3 = st.columns(3)
        with c1:
            sido = st.selectbox("시/도", sido_list, index=sido_idx)
        sgg_list = list(bjd[sido].keys())
        if sgg_list == [""]:                       # 세종 등 시군구 없는 광역
            sigungu = ""
            with c2:
                st.selectbox("시/군/구", ["(해당 없음)"], disabled=True)
        else:
            sgg_idx = sgg_list.index("계양구") if "계양구" in sgg_list else 0
            with c2:
                sigungu = st.selectbox("시/군/구", sgg_list, index=sgg_idx)
        with c3:
            emd = st.selectbox("읍/면/동", list(bjd[sido][sigungu].keys()))
        sgg_cd = bjd[sido][sigungu][emd][:5]

        c4, c5 = st.columns(2)
        with c4:
            cx_htype = st.radio("주택유형", list(market_price.ENDPOINTS.keys()),
                                horizontal=True, key="cx_htype")
        with c5:
            cx_months = st.radio("단지 수집 기간(개월)", [12, 24],
                                 horizontal=True, key="cx_months")
        st.caption("※ 최근 실거래(매매)가 있는 단지만 표시됩니다. 해당 동에 없으면 시군구 전체로 자동 확장됩니다.")

        if st.button("단지 불러오기", type="primary"):
            # 행정구역 개편(예: 2026-07 인천 서구→서구·검단구)으로 CSV의 시군구코드가
            # RTMS와 어긋날 수 있어, juso 라이브 코드를 우선 사용하고 CSV는 폴백으로 둔다.
            with st.spinner("지역 코드 확인 중..."):
                rr = address.search_address(
                    " ".join(x for x in [sido, sigungu, emd] if x),
                    config.JUSO_API_KEY, per_page=10)
            live_cd = ""
            if rr["ok"] and rr["items"]:
                cds = [i["sigungu_cd"] for i in rr["items"] if i.get("sigungu_cd")]
                if cds:
                    live_cd = Counter(cds).most_common(1)[0][0]
            use_cd = live_cd or sgg_cd
            with st.spinner(f"{emd} {cx_htype} 실거래 수집 중..."):
                mp = _trades_cached(use_cd, cx_htype, cx_months)
            if not mp["ok"]:
                st.session_state.pop("cx_names", None)
                st.error(mp["error"])
            else:
                all_tr = mp["trades"]
                emd_tr = [t for t in all_tr if (t.get("법정동") or "").strip() == emd.strip()]
                emd_names = Counter(t["단지"] for t in emd_tr if t.get("단지")).most_common()
                all_names = Counter(t["단지"] for t in all_tr if t.get("단지")).most_common()
                dong_dist = Counter((t.get("법정동") or "(미상)") for t in all_tr).most_common(20)
                st.session_state["cx_names"] = emd_names
                st.session_state["cx_all"] = all_names
                st.session_state["cx_dong_dist"] = dong_dist
                st.session_state["cx_meta"] = {
                    "emd": emd, "htype": cx_htype, "months": cx_months,
                    "sigungu": sigungu, "sido": sido, "total_all": len(all_tr),
                    "used_cd": use_cd, "csv_cd": sgg_cd,
                }

        cx_names = st.session_state.get("cx_names")
        if cx_names is not None:
            meta = st.session_state.get("cx_meta", {})
            m_emd = meta.get("emd", "")
            m_sgg = meta.get("sigungu", "")
            total_all = meta.get("total_all", 0)
            used_cd = meta.get("used_cd", "")
            csv_cd = meta.get("csv_cd", "")
            code_note = f" · 코드 {used_cd}" if used_cd == csv_cd else f" · 코드 {used_cd}(개편반영, CSV {csv_cd})"
            st.caption(f"{m_emd} 거래 단지 {len(cx_names)}개 · "
                       f"{m_sgg} 전체 {total_all}건 "
                       f"(최근 {meta.get('months',12)}개월 {meta.get('htype','')}{code_note})")

            # 진단: 시군구 내 법정동별 거래 분포
            with st.expander("거래가 있는 법정동 분포 보기 (진단)"):
                dist = st.session_state.get("cx_dong_dist", [])
                if dist:
                    st.write(" · ".join(f"{d} {c}건" for d, c in dist))
                else:
                    st.write("거래 없음")

            # 표시 소스 결정: 해당 동 우선, 0건이면 시군구 전체 폴백
            use_emd = m_emd
            source = cx_names
            fallback = False
            if not cx_names and total_all > 0:
                source = st.session_state.get("cx_all", [])
                use_emd = ""       # 동 무관 검색
                fallback = True
                st.warning(f"'{m_emd}' 매매 거래는 0건이지만 {m_sgg} 전체 {total_all}건이 "
                           f"있습니다. 아래 {m_sgg} 전체 단지에서 선택하세요.")

            if not source:
                if total_all == 0:
                    st.info("이 시군구·기간·유형 실거래(매매)가 0건입니다. "
                            "기간을 24개월로 늘리거나 주택유형을 바꾸거나, '직접 입력' 탭을 이용하세요.")
                else:
                    st.info("표시할 단지가 없습니다. '직접 입력' 탭을 이용하세요.")
            else:
                shown = source
                if len(source) > COMPLEX_FILTER_THRESHOLD:
                    flt = st.text_input("단지명 필터 (부분일치)", key="cx_filter",
                                        placeholder="예: 루원시티")
                    if flt.strip():
                        shown = [c for c in source if flt.strip() in c[0]]
                if not shown:
                    st.info("필터와 일치하는 단지가 없습니다.")
                else:
                    cx_idx = st.selectbox(
                        "단지 선택", range(len(shown)),
                        format_func=lambda i: f"{shown[i][0]} (거래 {shown[i][1]}건)")
                    if st.button("이 단지로 진단 시작"):
                        pick = shown[cx_idx][0]
                        with st.spinner("단지 주소 확정 중..."):
                            fr = address.find_by_name(m_sgg, use_emd, pick, config.JUSO_API_KEY)
                        if not fr["ok"]:
                            st.error(fr["error"])
                        elif not fr["items"]:
                            st.warning(f"'{pick}' 주소를 찾지 못했습니다. "
                                       "'직접 입력' 탭에서 지번으로 검색하세요.")
                        else:
                            _reset_downstream()
                            st.session_state["addr_items"] = fr["items"]
                            st.session_state["trade_nm"] = pick
                            tag = " · 시군구 전체 검색" if fallback else ""
                            st.success(f"선택됨: {pick} — 주소 {len(fr['items'])}건"
                                       f"{tag} (검색어: {fr['keyword']})")

# ── 탭 2: 기존 키워드 직접 입력 (단독·다가구 등) ─────────────────────
with tab_direct:
    keyword = st.text_input("주소 입력", placeholder="예: 인천 서구 가정동 546")
    if st.button("주소 검색"):
        if not keyword.strip():
            st.warning("주소를 입력하세요.")
        else:
            with st.spinner("주소 검색 중..."):
                result = address.search_address(keyword, config.JUSO_API_KEY)
            if not result["ok"]:
                st.error(result["error"])
            elif result["total"] == 0:
                st.info("검색 결과가 없습니다.")
            else:
                _reset_downstream()
                st.session_state["addr_items"] = result["items"]
                st.success(f"{result['total']}건 검색됨")

items = st.session_state.get("addr_items")
if items:
    labels = [_label(i) for i in items]
    idx = st.selectbox("대상 주소 선택", range(len(items)), format_func=lambda i: labels[i])
    chosen = items[idx]
    st.session_state["chosen_addr"] = chosen

    st.divider()
    st.subheader("건축물대장 (표제부 + 전유부)")
    if st.button("건축물대장 조회"):
        with st.spinner("표제부 조회 중..."):
            br = building_ledger.get_title_info(chosen, config.BUILDING_LEDGER_API_KEY)
        if not br["ok"]:
            st.session_state["br_all"] = None
            st.error(br["error"])
        else:
            st.session_state["br_all"] = br["records"]
            st.session_state.pop("expos_cache", None)

    br_all = st.session_state.get("br_all")
    if br_all:
        dong_opts = sorted({r.get("dongNm", "") for r in br_all if r.get("dongNm")}, key=_num_key) or ["(동 없음)"]
        sel_dong = st.selectbox("동 선택 (클릭)", dong_opts)
        rec = next((r for r in br_all if r.get("dongNm") == sel_dong), br_all[0])

        cache = st.session_state.get("expos_cache", {})
        if sel_dong not in cache:
            with st.spinner(f"{sel_dong} 전유부 조회 중..."):
                cache[sel_dong] = building_ledger.get_expos_area(
                    chosen, config.BUILDING_LEDGER_API_KEY, dong=sel_dong)
            st.session_state["expos_cache"] = cache
        ex = cache.get(sel_dong, {})

        ho_opts = sorted({r["호"] for r in ex.get("rows", []) if r.get("호")}, key=_num_key) or ["(호 정보 없음)"]
        sel_ho = st.selectbox("호 선택 (클릭)", ho_opts)

        st.markdown(f"**표제부** · {rec.get('bldNm','')} {sel_dong}")
        disp = {label: rec.get(tag, "") for tag, label in building_ledger.FIELDS.items()}
        st.table({"항목": list(disp.keys()), "값": list(disp.values())})
        for name, level, detail in risk.assess_building(rec):
            st.write(f"{risk.EMOJI[level]} **{name}** — {detail}")
        st.session_state["bld_nm"] = rec.get("bldNm", "")

        if not ex.get("ok"):
            st.warning("전유부: " + ex.get("error", ""))
        elif sel_ho != "(호 정보 없음)":
            ho_rows = [r for r in ex["rows"] if r["호"] == sel_ho]
            expos_area = round(sum(r["면적(㎡)"] for r in ho_rows if r["구분"] == "전유"), 2)
            st.markdown(f"**전유부** · {sel_ho} 전용면적 **{expos_area} ㎡**")
            st.dataframe(_to_df(ho_rows), use_container_width=True)
            if expos_area:
                st.session_state["expos_area"] = expos_area
        else:
            st.info("전유부 호 정보가 없습니다.")
        with st.expander("전유부 원본 항목 보기"):
            st.json(ex.get("raw_first", {}))

    st.divider()
    st.subheader("시세 · 위험진단")
    # WHY: 단독·다가구는 물건마다 고유해 실거래 유사비교 시세가 부정확하다.
    # 아파트·연립은 실거래 조회, 단독·다가구는 감정가·공시가 직접 입력으로 분기한다.
    price_mode = st.radio(
        "시세 확인 방식",
        ["실거래가 조회 (아파트·연립다세대)", "직접 입력 (단독·다가구 등)"],
        horizontal=True, key="price_mode")

    if price_mode.startswith("실거래가"):
        htype = st.radio("주택유형(실거래가)", list(market_price.ENDPOINTS.keys()), horizontal=True)
        months = st.slider("조회 기간(개월)", 3, 24, 12)

        if st.button("실거래가 불러오기"):
            with st.spinner("실거래가 조회 중..."):
                mp = _trades_cached(chosen["sigungu_cd"], htype, months)
            # 세션엔 상한까지만 담아 메모리 누적을 막는다(필터·추정엔 충분).
            st.session_state["all_trades"] = mp["trades"][:TRADES_CAP] if mp["ok"] else None
            st.session_state["trades_error"] = None if mp["ok"] else mp["error"]
            st.session_state["trades_raw"] = mp.get("raw_first", {}) if mp["ok"] else {}

        if st.session_state.get("trades_error"):
            st.error(st.session_state["trades_error"])

        all_trades = st.session_state.get("all_trades")
        if all_trades is not None:
            st.caption(f"{htype} · 최근 {months}개월 전체 {len(all_trades)}건 조회됨")
            if not all_trades:
                st.info("이 시군구·기간·유형에 거래가 없습니다. 주택유형을 바꾸거나 기간을 늘려보세요.")
            else:
                filter_default = st.session_state.get("trade_nm") or st.session_state.get("bld_nm", "")
                name_filter = st.text_input("단지명 필터 (부분일치)", value=filter_default)
                matched = [t for t in all_trades if name_filter.strip() in t["단지"]] if name_filter.strip() else []
                if name_filter.strip() and not matched:
                    st.info(f"'{name_filter}' 매칭 0건. 아래 전체 목록에서 실제 단지명을 확인하세요.")
                    st.dataframe(_to_df(all_trades), use_container_width=True)
                elif matched:
                    st.write(f"매칭 {len(matched)}건")
                    st.dataframe(_to_df(matched), use_container_width=True)
                    area_default = str(st.session_state.get("expos_area", "")) if st.session_state.get("expos_area") else ""
                    area_in = st.text_input("전용면적(㎡, 선택)", value=area_default, placeholder="예: 59.8")
                    est_calc = market_price.estimate_price(matched, area=_to_float(area_in))
                    if est_calc:
                        st.metric("추정 시세(중앙값)", f"{est_calc:,} 만원")
                        st.session_state["est_price"] = est_calc
                    else:
                        st.warning("면적 조건에 맞는 거래가 없습니다. 전용면적을 비우거나 넓혀보세요.")
                else:
                    st.info("단지명 필터를 입력하면 해당 단지 거래만 추려 시세를 추정합니다.")
                    st.dataframe(_to_df(all_trades), use_container_width=True)
            with st.expander("원본 응답 항목 보기"):
                st.json(st.session_state.get("trades_raw", {}))
    else:
        st.caption("단독·다가구는 실거래 유사비교가 어렵습니다. "
                   "감정가·개별주택 공시가격·인근 유사 매매가를 기준으로 시세를 직접 입력하세요.")
        manual = st.number_input("시세 직접 입력(만원)", min_value=0, step=1000,
                                 value=int(st.session_state.get("est_price", 0) or 0))
        if manual > 0:
            st.session_state["est_price"] = manual
            st.metric("적용 시세(직접 입력)", f"{manual:,} 만원")
        else:
            st.info("시세를 입력하면 아래에서 보증금 대비 위험을 진단합니다.")

    est = st.session_state.get("est_price")
    if est:
        st.divider()
        st.markdown("**등기부등본 업로드 (선순위 채권 반영)**")
        st.caption("등기사항전부증명서 PDF(텍스트 본)를 올리면 근저당 채권최고액·위험 표시를 자동 추출합니다.")
        pdf = st.file_uploader("등기부 PDF", type=["pdf"])
        if pdf is not None:
            with st.spinner("등기부 분석 중..."):
                reg = register.parse_register(pdf)
            if not reg["ok"]:
                st.error(reg["error"])
                st.session_state.pop("reg", None)
            else:
                st.session_state["reg"] = reg

        reg = st.session_state.get("reg")
        senior_default = 0
        if reg:
            won_list = reg["geunjeo"]
            if won_list:
                st.write("추출된 채권최고액 " + str(len(won_list)) + "건: "
                         + ", ".join(f"{w:,}원" for w in won_list))
            else:
                st.info("근저당 채권최고액을 찾지 못했습니다. 직접 입력하세요.")
            senior_default = reg["senior_manwon"]
            if reg["flags"]:
                st.error("⚠️ 권리 위험 키워드: " + ", ".join(reg["flags"]) + " — 등기부 원본 확인 필수")
            st.caption("※ 말소된 근저당이 포함됐을 수 있습니다. 유효 채권만 남기고 아래 금액을 조정하세요.")

        senior = st.number_input("선순위 채권 합계(만원)", min_value=0, step=1000, value=int(senior_default))
        deposit = st.number_input("보증금(만원)", min_value=0, step=1000, value=0)
        if deposit > 0:
            name, level, detail = risk.assess_jeonse_ratio(deposit, est, senior_debt=senior)
            st.write(f"{risk.EMOJI[level]} **{name}** — {detail}")
            st.divider()
            st.markdown(
                "**이 결과가 '주의·위험'이라면** — 선순위·근저당·체납을 반영해 "
                "실제 계약해도 되는지 사례로 판단하고, 위험하면 안전한 대체 매물과 "
                "최악의 경우 보증금 회수액(경매)까지 함께 검토해 드립니다.")
            _consult_cta("result")
