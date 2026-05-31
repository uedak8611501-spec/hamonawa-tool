"""
延縄（ハモ）操業データ統合管理ツール
Streamlit メインアプリ
"""
import json
from datetime import datetime, date, time

import streamlit as st
import pandas as pd

from ocr_extractor import extract_from_image, validate_and_fill_defaults
from gps_processor import load_gps_csv, filter_by_time, split_into_hachi, merge_catch_to_segments

st.set_page_config(
    page_title="延縄操業データ管理",
    page_icon="🎣",
    layout="wide",
)

st.title("🎣 延縄（ハモ）操業データ統合管理ツール")

# ── セッション初期化 ──────────────────────────────────────────
if "ocr_data" not in st.session_state:
    st.session_state.ocr_data = None
if "gps_df" not in st.session_state:
    st.session_state.gps_df = None
if "segments" not in st.session_state:
    st.session_state.segments = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: 操業メモ写真アップロード & OCR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 1 　操業メモ写真のアップロードとデータ抽出")

col_img, col_ocr = st.columns([1, 1])

with col_img:
    uploaded_img = st.file_uploader(
        "操業メモの写真をアップロード",
        type=["jpg", "jpeg", "png", "heic", "webp"],
        key="memo_img",
    )
    if uploaded_img:
        st.image(uploaded_img, caption="アップロードされたメモ", use_container_width=True)

    if uploaded_img and st.button("📷 Claude Vision で読み取る", type="primary"):
        with st.spinner("Claude Visionで解析中...（数秒かかります）"):
            img_bytes = uploaded_img.read()
            media_type = f"image/{uploaded_img.type.split('/')[-1]}"
            if media_type == "image/heic":
                media_type = "image/jpeg"
            raw = extract_from_image(img_bytes, media_type)
            st.session_state.ocr_data = validate_and_fill_defaults(raw)

        if "error" in st.session_state.ocr_data:
            st.error(f"読み取りエラー: {st.session_state.ocr_data['error']}")
            if "raw" in st.session_state.ocr_data:
                st.text_area("APIの生レスポンス", st.session_state.ocr_data["raw"], height=200)
        else:
            st.success("読み取り完了！右側で内容を確認・修正してください。")

with col_ocr:
    st.subheader("抽出データの確認・修正")

    # OCRデータがない場合は空のフォームを用意
    d = st.session_state.ocr_data or {}
    ctd = d.get("ctd") or {}

    with st.form("ocr_edit_form"):
        st.markdown("**基本情報**")

        fc1, fc2 = st.columns(2)
        with fc1:
            # 日付
            raw_date = d.get("date")
            default_date = date.today()
            if raw_date:
                try:
                    default_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                except ValueError:
                    pass
            op_date = st.date_input("操業日", value=default_date)

            bait = st.text_input("エサ", value=d.get("bait") or "")

        with fc2:
            # 開始・終了時刻
            def _parse_time(s):
                if not s:
                    return time(6, 0)
                try:
                    return datetime.strptime(s, "%H:%M").time()
                except ValueError:
                    return time(6, 0)

            start_time = st.time_input("投入開始時刻", value=_parse_time(d.get("start_time")))
            end_time = st.time_input("揚げ終了時刻", value=_parse_time(d.get("end_time")))

        total_hachi = st.number_input(
            "総鉢数", min_value=1, max_value=500,
            value=int(d.get("total_hachi") or 1)
        )

        st.markdown("---")
        st.markdown("**CTD環境データ**")

        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            surface_temp = st.number_input(
                "表層水温 (℃)", value=float(ctd.get("surface_temp") or 0.0), format="%.1f"
            )
            bottom_temp = st.number_input(
                "底水温 (℃)", value=float(ctd.get("bottom_temp") or 0.0), format="%.1f"
            )
        with ec2:
            surface_sal = st.number_input(
                "表層塩分 (psu)", value=float(ctd.get("surface_salinity") or 0.0), format="%.2f"
            )
            bottom_sal = st.number_input(
                "底層塩分 (psu)", value=float(ctd.get("bottom_salinity") or 0.0), format="%.2f"
            )
        with ec3:
            max_depth = st.number_input(
                "実測最大水深 (m)", value=float(ctd.get("max_depth") or 0.0), format="%.1f"
            )

        st.markdown("---")
        st.markdown("**鉢ごとの釣果**")
        st.caption("読み取り結果を確認し、誤りがあれば修正してください。")

        catch_list = d.get("catch_per_hachi") or []
        # total_hachi に合わせて行数を調整
        while len(catch_list) < int(total_hachi):
            catch_list.append({"hachi": len(catch_list) + 1, "count": 0})
        catch_list = catch_list[: int(total_hachi)]

        catch_df = pd.DataFrame(catch_list)
        if "hachi" not in catch_df.columns:
            catch_df["hachi"] = range(1, len(catch_df) + 1)
        if "count" not in catch_df.columns:
            catch_df["count"] = 0
        catch_df = catch_df[["hachi", "count"]].rename(
            columns={"hachi": "鉢番号", "count": "釣果（匹）"}
        )

        edited_catch = st.data_editor(
            catch_df,
            num_rows="fixed",
            use_container_width=True,
            hide_index=True,
            column_config={
                "鉢番号": st.column_config.NumberColumn(disabled=True),
                "釣果（匹）": st.column_config.NumberColumn(min_value=0),
            },
        )

        notes = st.text_area("備考", value=d.get("notes") or "", height=80)

        submitted = st.form_submit_button("✅ この内容で確定する", type="primary")

    if submitted:
        # フォームの値でセッションのOCRデータを更新
        catch_records = [
            {"hachi": int(row["鉢番号"]), "count": int(row["釣果（匹）"])}
            for _, row in edited_catch.iterrows()
        ]
        st.session_state.ocr_data = {
            "date": op_date.strftime("%Y-%m-%d"),
            "bait": bait,
            "start_time": start_time.strftime("%H:%M"),
            "end_time": end_time.strftime("%H:%M"),
            "total_hachi": int(total_hachi),
            "catch_per_hachi": catch_records,
            "ctd": {
                "surface_temp": surface_temp,
                "bottom_temp": bottom_temp,
                "surface_salinity": surface_sal,
                "bottom_salinity": bottom_sal,
                "max_depth": max_depth,
            },
            "notes": notes,
        }
        st.success("操業データを確定しました。STEP 2 に進んでください。")

    # 確定済みデータのサマリー表示
    if st.session_state.ocr_data and "error" not in st.session_state.ocr_data:
        od = st.session_state.ocr_data
        total_catch = sum(x["count"] for x in od.get("catch_per_hachi", []))
        ctd_d = od.get("ctd", {})
        temp_diff = None
        if ctd_d.get("surface_temp") and ctd_d.get("bottom_temp"):
            temp_diff = round(ctd_d["surface_temp"] - ctd_d["bottom_temp"], 1)

        with st.expander("確定済みデータのサマリー", expanded=False):
            st.json(od)
        st.info(
            f"📊 総釣果: **{total_catch} 匹** / {od.get('total_hachi')} 鉢 "
            f"| 水温差: **{temp_diff if temp_diff is not None else 'N/A'} ℃**"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2: GPSログ アップロード & 鉢分割
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 2 　GPSログのアップロードと鉢分割")

if not st.session_state.ocr_data or "error" in st.session_state.ocr_data:
    st.warning("先にSTEP 1で操業データを確定してください。")
else:
    od = st.session_state.ocr_data
    gps_file = st.file_uploader("GPS ログ CSV をアップロード", type=["csv", "txt"], key="gps_csv")

    if gps_file:
        try:
            gps_df = load_gps_csv(gps_file)
            st.session_state.gps_df = gps_df
            st.success(f"GPSログ読み込み完了: {len(gps_df)} ポイント")
            st.caption(
                f"時刻範囲: {gps_df['timestamp'].min()} 〜 {gps_df['timestamp'].max()}"
            )

            with st.expander("GPSデータのプレビュー（先頭10行）"):
                st.dataframe(gps_df.head(10), use_container_width=True)
        except ValueError as e:
            st.error(str(e))
            st.session_state.gps_df = None

    if st.session_state.gps_df is not None:
        if st.button("🔪 GPSトラックを鉢ごとに分割する", type="primary"):
            try:
                op_date_obj = datetime.strptime(od["date"], "%Y-%m-%d").date()
                filtered = filter_by_time(
                    st.session_state.gps_df,
                    op_date_obj,
                    od["start_time"],
                    od["end_time"],
                )
                st.info(
                    f"操業時間帯({od['start_time']}〜{od['end_time']})のGPSポイント: {len(filtered)} 件"
                )

                segments = split_into_hachi(filtered, od["total_hachi"])
                segments = merge_catch_to_segments(segments, od.get("catch_per_hachi", []))
                st.session_state.segments = segments
                st.success(f"{od['total_hachi']} 鉢への分割完了！")

            except ValueError as e:
                st.error(str(e))

    if st.session_state.segments:
        st.subheader("分割結果プレビュー")
        preview_rows = []
        for seg in st.session_state.segments:
            preview_rows.append(
                {
                    "鉢番号": seg["hachi_no"],
                    "釣果（匹）": seg["catch"],
                    "中心緯度": round(seg["center_lat"], 5),
                    "中心経度": round(seg["center_lon"], 5),
                    "区間距離(m)": round(seg["length_m"]),
                    "開始時刻": seg["start_time"].strftime("%H:%M:%S"),
                    "終了時刻": seg["end_time"].strftime("%H:%M:%S"),
                    "GPSポイント数": len(seg["points"]),
                }
            )
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

        # JSONエクスポート
        export_data = []
        for seg in st.session_state.segments:
            export_data.append(
                {
                    "hachi_no": seg["hachi_no"],
                    "catch": seg["catch"],
                    "center_lat": seg["center_lat"],
                    "center_lon": seg["center_lon"],
                    "length_m": seg["length_m"],
                    "start_time": seg["start_time"].isoformat(),
                    "end_time": seg["end_time"].isoformat(),
                    "ctd": od.get("ctd"),
                }
            )
        export_json = json.dumps(
            {"operation": od, "segments": export_data},
            ensure_ascii=False,
            indent=2,
        )
        st.download_button(
            "⬇️ 分割データをJSONでエクスポート",
            data=export_json.encode("utf-8"),
            file_name=f"operation_{od['date']}.json",
            mime="application/json",
        )

        st.info("🗺️ 地図可視化（STEP 3: Foliumヒートマップ）は次のフェーズで実装します。")
