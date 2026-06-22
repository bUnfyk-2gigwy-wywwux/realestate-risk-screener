import re

import streamlit as st

import config
import address
import building_ledger
import market_price
import risk


def _num_key(s):
    parts = re.split(r"(\d+)", s or "")
    return [int(p) if p.isdigit() else p for p in parts]


def _to_float(s):
    try:
        return float(s) if s and s.strip() else None
    except ValueError:
        return None


st.set_page_config(page_title="사전 위험 진단기", page_icon="🏠", layout="centered")
st.title("🏠 사전 위험 진단기")
st.caption("계약 전 매물 위험을 신호등으로 진단합니다")
st.info("표제부+전유부+시세 연동 단계. 선순위 채권(등기부)은 다음 단계에서 반영됩니다.")

deal_type = st.radio("거래유형", config.DEAL_TYPES, horizontal=True)
keyword = st.text_input("주소 입력", placeholder="예: 인천 서구 가정동 546")
if st.button("주소 검색", type="primary"):
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
            st.session_state["addr_items"] = result["items"]
            for k in ["br_all", "expos_cache", "all_trades", "est_price", "expos_area", "bld_nm"]:
                st.session_state.pop(k, None)
            st.success(f"{result['total']}건 검색됨")

items = st.session_state.get("addr_items")
if items:
    labels = [i["road_addr"] + (f" ({i['bd_name']})" if i["bd_name"] else "") for i in items]
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
            st.dataframe(ho_rows, use_container_width=True)
            if expos_area:
                st.session_state["expos_area"] = expos_area
        else:
            st.info("전유부 호 정보가 없습니다.")
        with st.expander("전유부 원본 항목 보기"):
            st.json(ex.get("raw_first", {}))

    st.divider()
    st.subheader("시세 · 깡통전세")
    htype = st.radio("주택유형(실거래가)", list(market_price.ENDPOINTS.keys()), horizontal=True)
    months = st.slider("조회 기간(개월)", 3, 24, 12)

    if st.button("실거래가 불러오기"):
        with st.spinner("실거래가 조회 중..."):
            mp = market_price.get_trades(chosen["sigungu_cd"], config.RTMS_API_KEY,
                                         house_type=htype, months=months)
        st.session_state["all_trades"] = mp["trades"] if mp["ok"] else None
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
            name_filter = st.text_input("단지명 필터 (부분일치)", value=st.session_state.get("bld_nm", ""))
            matched = [t for t in all_trades if name_filter.strip() in t["단지"]] if name_filter.strip() else []
            if name_filter.strip() and not matched:
                st.info(f"'{name_filter}' 매칭 0건. 아래 전체 목록에서 실제 단지명을 확인하세요.")
                st.dataframe(all_trades, use_container_width=True)
            elif matched:
                st.write(f"매칭 {len(matched)}건")
                st.dataframe(matched, use_container_width=True)
                area_default = str(st.session_state.get("expos_area", "")) if st.session_state.get("expos_area") else ""
                area_in = st.text_input("전용면적(㎡, 선택)", value=area_default, placeholder="예: 59.8")
                est = market_price.estimate_price(matched, area=_to_float(area_in))
                if est:
                    st.metric("추정 시세(중앙값)", f"{est:,} 만원")
                    st.session_state["est_price"] = est
                else:
                    st.warning("면적 조건에 맞는 거래가 없습니다. 전용면적을 비우거나 넓혀보세요.")
            else:
                st.info("단지명 필터를 입력하면 해당 단지 거래만 추려 시세를 추정합니다.")
                st.dataframe(all_trades, use_container_width=True)
        with st.expander("원본 응답 항목 보기"):
            st.json(st.session_state.get("trades_raw", {}))

    est = st.session_state.get("est_price")
    if est:
        deposit = st.number_input("보증금(만원)", min_value=0, step=1000, value=0)
        if deposit > 0:
            name, level, detail = risk.assess_jeonse_ratio(deposit, est, senior_debt=0)
            st.write(f"{risk.EMOJI[level]} **{name}** — {detail}")
            st.caption("※ 선순위 채권(등기부)은 0으로 가정. 다음 단계에서 반영됩니다.")
