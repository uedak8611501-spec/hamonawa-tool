"""
延縄（ハモ）操業データ統合管理ツール
Streamlit メインアプリ
"""
import json
from datetime import datetime, date, time

import streamlit as st
import pandas as pd
import folium
from folium.plugins import Draw
from streamlit_folium import st_folium

from gps_processor import load_gps_csv, filter_by_time, split_into_hachi, merge_catch_to_segments, polyline_to_track
from database import init_db, save_operation, list_operations, load_operation, delete_operation

init_db()

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
if "total_hachi" not in st.session_state:
    st.session_state.total_hachi = 1
if "last_center" not in st.session_state:
    st.session_state.last_center = [33.0, 132.2]  # 初期表示位置（後で実データで上書き）


def _parse_time(s):
    if not s:
        return time(6, 0)
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return time(6, 0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: 操業データの入力
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 1 　操業データの入力")

with st.container():
    st.subheader("操業データを入力してください")

    d = st.session_state.ocr_data or {}
    ctd = d.get("ctd") or {}

    # ── 基本情報フォーム ──
    with st.form("basic_form"):
        st.markdown("**基本情報**")
        fc1, fc2 = st.columns(2)
        with fc1:
            raw_date = d.get("date")
            default_date = date.today()
            if raw_date:
                try:
                    default_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                except ValueError:
                    pass
            op_date = st.date_input("操業日", value=default_date)
            location = st.text_input("場所", value=d.get("location") or "")
            bait = st.text_input("エサ", value=d.get("bait") or "")

        with fc2:
            start_time = st.time_input("投入開始時刻", value=_parse_time(d.get("start_time")), step=60)
            end_time = st.time_input("投入終了時刻", value=_parse_time(d.get("end_time")), step=60)

        st.markdown("---")
        st.markdown("**CTD環境データ**")
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            surface_temp = st.number_input("表層水温 (℃)", value=float(ctd.get("surface_temp") or 0.0), format="%.1f")
            bottom_temp = st.number_input("底水温 (℃)", value=float(ctd.get("bottom_temp") or 0.0), format="%.1f")
        with ec2:
            surface_sal = st.number_input("表層塩分 (psu)", value=float(ctd.get("surface_salinity") or 0.0), format="%.2f")
            bottom_sal = st.number_input("底層塩分 (psu)", value=float(ctd.get("bottom_salinity") or 0.0), format="%.2f")
        with ec3:
            max_depth = st.number_input("実測最大水深 (m)", value=float(ctd.get("max_depth") or 0.0), format="%.1f")

        notes = st.text_area("備考", value=d.get("notes") or "", height=60)
        basic_submitted = st.form_submit_button("基本情報を保存", type="secondary")

    # ── 総鉢数（フォーム外 → 即時反映） ──
    st.markdown("---")
    st.markdown("**総鉢数と釣果入力**")

    new_hachi = st.number_input(
        "総鉢数を入力してください",
        min_value=1, max_value=500,
        value=st.session_state.total_hachi,
        step=1,
        key="hachi_input"
    )
    # 鉢数が変わったらセッションを更新
    if new_hachi != st.session_state.total_hachi:
        st.session_state.total_hachi = new_hachi

    # 釣果表（鉢数に合わせてリアルタイム更新）
    catch_list = d.get("catch_per_hachi") or []
    # 現在の鉢数に合わせて行数を調整
    while len(catch_list) < st.session_state.total_hachi:
        catch_list.append({"hachi": len(catch_list) + 1, "count": 0})
    catch_list = catch_list[:st.session_state.total_hachi]

    catch_df = pd.DataFrame(catch_list).rename(columns={"hachi": "鉢番号", "count": "釣果（匹）"})

    st.caption(f"👇 {st.session_state.total_hachi} 鉢分の釣果を入力してください（セルをクリックして数字を入力）")
    edited_catch = st.data_editor(
        catch_df,
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
        column_config={
            "鉢番号": st.column_config.NumberColumn(disabled=True, width="small"),
            "釣果（匹）": st.column_config.NumberColumn(min_value=0, width="medium"),
        },
    )

    # ── 確定ボタン ──
    if st.button("✅ この内容で確定する", type="primary"):
        catch_records = [
            {"hachi": int(row["鉢番号"]), "count": int(row["釣果（匹）"])}
            for _, row in edited_catch.iterrows()
        ]

        # basic_formが未送信の場合は現在のd(OCRデータ)から引き継ぐ
        save_date = op_date if basic_submitted or True else datetime.strptime(d.get("date", date.today().strftime("%Y-%m-%d")), "%Y-%m-%d").date()

        st.session_state.ocr_data = {
            "date": op_date.strftime("%Y-%m-%d"),
            "location": location,
            "bait": bait,
            "start_time": start_time.strftime("%H:%M"),
            "end_time": end_time.strftime("%H:%M"),
            "total_hachi": st.session_state.total_hachi,
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
        st.success("✅ 操業データを確定しました！STEP 2 に進んでください。")

    # 確定済みサマリー
    if st.session_state.ocr_data and "error" not in st.session_state.ocr_data:
        od = st.session_state.ocr_data
        total_catch = sum(x["count"] for x in od.get("catch_per_hachi", []))
        ctd_d = od.get("ctd", {})
        temp_diff = None
        if ctd_d.get("surface_temp") and ctd_d.get("bottom_temp"):
            temp_diff = round(ctd_d["surface_temp"] - ctd_d["bottom_temp"], 1)
        st.info(
            f"📊 確定済み → 総釣果: **{total_catch} 匹** / {od.get('total_hachi')} 鉢 "
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

    gps_method = st.radio(
        "GPSデータの取得方法を選んでください",
        ["📂 CSVファイルをアップロード", "✏️ 地図に手で描く（GPS取り忘れた時）"],
        horizontal=True,
        key="gps_method",
    )

    # ─────────────────────────────────────────────────────
    # 方法A：CSVアップロード
    # ─────────────────────────────────────────────────────
    if gps_method.startswith("📂"):
        gps_file = st.file_uploader("GPS ログ CSV をアップロード", type=["csv", "txt"], key="gps_csv")

        if gps_file:
            # ── デバッグ：生CSVの列名と先頭3行を表示 ──
            try:
                raw_bytes = gps_file.read()
                for enc in ("utf-8-sig", "utf-8", "shift_jis", "cp932"):
                    try:
                        raw_text = raw_bytes.decode(enc)
                        break
                    except Exception:
                        continue
                import io as _io
                raw_df = pd.read_csv(_io.StringIO(raw_text), nrows=3)
                with st.expander("🔍 デバッグ：生CSV（先頭3行）", expanded=True):
                    st.write("列名:", list(raw_df.columns))
                    st.write("先頭3行:")
                    st.dataframe(raw_df.iloc[:, :6])
                gps_file.seek(0)
            except Exception as e:
                st.warning(f"デバッグ表示エラー: {e}")
                try:
                    gps_file.seek(0)
                except Exception:
                    pass

            try:
                gps_df = load_gps_csv(gps_file)
                st.session_state.gps_df = gps_df
                st.success(f"GPSログ読み込み完了: {len(gps_df)} ポイント")
                st.caption(f"時刻範囲: {gps_df['timestamp'].min()} 〜 {gps_df['timestamp'].max()}")
                with st.expander("GPSデータのプレビュー（先頭10行）"):
                    st.dataframe(gps_df.head(10), use_container_width=True)
            except ValueError as e:
                st.error(str(e))
                st.session_state.gps_df = None

    # ─────────────────────────────────────────────────────
    # 方法B：地図に手で描く
    # ─────────────────────────────────────────────────────
    else:
        st.caption(
            "🖊️ 左の線ツールを選び、投入したルートを地図上でクリックしていきます。"
            "最後はダブルクリックで線を確定 → 下の「この線をGPSとして使う」を押してください。"
        )
        draw_map = folium.Map(location=st.session_state.last_center, zoom_start=13, control_scale=True)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="衛星写真",
        ).add_to(draw_map)
        Draw(
            export=False,
            draw_options={
                "polyline": True, "polygon": False, "rectangle": False,
                "circle": False, "marker": False, "circlemarker": False,
            },
            edit_options={"edit": True, "remove": True},
        ).add_to(draw_map)

        draw_out = st_folium(draw_map, use_container_width=True, height=500, key="draw_map")

        drawings = (draw_out or {}).get("all_drawings") or []
        line_coords = None
        for d in reversed(drawings):
            geom = d.get("geometry", {})
            if geom.get("type") == "LineString":
                # GeoJSONは[経度,緯度]なので[緯度,経度]に変換
                line_coords = [[c[1], c[0]] for c in geom["coordinates"]]
                break

        if line_coords:
            st.success(f"線を認識しました（{len(line_coords)} 点）。下のボタンで確定してください。")
            if st.button("✅ この線をGPSとして使う"):
                try:
                    track = polyline_to_track(
                        line_coords, od["date"], od["start_time"], od["end_time"]
                    )
                    st.session_state.gps_df = track
                    st.success(f"手描きルートをGPS軌跡に変換しました（{len(track)} 点）。")
                except ValueError as e:
                    st.error(str(e))
        else:
            st.info("まだ線が描かれていません。地図左上の線ツールで描いてください。")

    # ─────────────────────────────────────────────────────
    # 鉢分割（CSV・手描き 共通）
    # ─────────────────────────────────────────────────────
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
                st.info(f"操業時間帯({od['start_time']}〜{od['end_time']})のGPSポイント: {len(filtered)} 件")
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
            preview_rows.append({
                "鉢番号": seg["hachi_no"],
                "釣果（匹）": seg["catch"],
                "中心緯度": round(seg["center_lat"], 5),
                "中心経度": round(seg["center_lon"], 5),
                "区間距離(m)": round(seg["length_m"]),
                "開始時刻": seg["start_time"].strftime("%H:%M:%S"),
                "終了時刻": seg["end_time"].strftime("%H:%M:%S"),
                "GPSポイント数": len(seg["points"]),
            })
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

        export_data = []
        for seg in st.session_state.segments:
            export_data.append({
                "hachi_no": seg["hachi_no"],
                "catch": seg["catch"],
                "center_lat": seg["center_lat"],
                "center_lon": seg["center_lon"],
                "length_m": seg["length_m"],
                "start_time": seg["start_time"].isoformat(),
                "end_time": seg["end_time"].isoformat(),
                "ctd": od.get("ctd"),
            })
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3: 地図可視化（Foliumヒートマップ）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 3 　地図で見る（釣果ヒートマップ）")

if not st.session_state.segments:
    st.warning("先にSTEP 2でGPSを分割してください。")
else:
    segs = st.session_state.segments
    od   = st.session_state.ocr_data
    ctd  = od.get("ctd", {}) or {}

    # ── 釣果に応じた色を計算（絶対値で5段階に固定）──────────
    def _catch_color(catch):
        """釣果数の絶対値で色を決める（その日の良し悪しに左右されない）"""
        if catch >= 25:
            return "#d7191c"  # 赤：最高
        elif catch >= 20:
            return "#fd8d3c"  # 橙：高
        elif catch >= 15:
            return "#ffd700"  # 黄：良
        elif catch >= 10:
            return "#74add1"  # 水色：まあまあ
        else:
            return "#2c7bb6"  # 青：ダメ（0〜9匹）

    def _catch_weight(catch):
        """釣果の段階が上がるほど線を太く"""
        if catch >= 25:
            return 10
        elif catch >= 20:
            return 8
        elif catch >= 15:
            return 6
        elif catch >= 10:
            return 5
        else:
            return 3

    # ── 地図の中心 ────────────────────────────────────────
    all_lats = [s["center_lat"] for s in segs]
    all_lons = [s["center_lon"] for s in segs]
    center   = [sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)]
    st.session_state.last_center = center  # 手描き地図の初期表示に再利用

    # ── Folium地図を作成 ──────────────────────────────────
    m = folium.Map(location=center, zoom_start=13, control_scale=True)

    # ベースマップ切替
    folium.TileLayer("OpenStreetMap",   name="標準地図").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="衛星写真", overlay=False
    ).add_to(m)
    folium.TileLayer(
        tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
        attr="OpenSeaMap", name="海図レイヤー", overlay=True
    ).add_to(m)

    # ── 各鉢を線 + ポップアップで描画 ────────────────────
    for seg in segs:
        pts = seg["points"]
        coords = list(zip(pts["lat"].tolist(), pts["lon"].tolist()))
        if len(coords) < 2:
            continue

        color  = _catch_color(seg["catch"])
        weight = _catch_weight(seg["catch"])

        # ポップアップHTML
        st_temp  = ctd.get("surface_temp")
        bt_temp  = ctd.get("bottom_temp")
        st_sal   = ctd.get("surface_salinity")
        bt_sal   = ctd.get("bottom_salinity")
        depth    = ctd.get("max_depth")
        temp_diff = round(st_temp - bt_temp, 1) if st_temp and bt_temp else "N/A"

        popup_html = f"""
        <div style="font-family:sans-serif; min-width:200px;">
          <h4 style="margin:4px 0; color:{color};">第{seg['hachi_no']}鉢</h4>
          <hr style="margin:4px 0;">
          <b>🐟 釣果：</b>{seg['catch']} 匹<br>
          <b>📏 区間距離：</b>{round(seg['length_m'])} m<br>
          <b>⏱ 時刻：</b>{seg['start_time'].strftime('%H:%M')}〜{seg['end_time'].strftime('%H:%M')}<br>
          <hr style="margin:4px 0;">
          <b>🌡 水温：</b>表層 {st_temp}℃ / 底 {bt_temp}℃（差 {temp_diff}℃）<br>
          <b>🧂 塩分：</b>表層 {st_sal} psu / 底 {bt_sal} psu<br>
          <b>🌊 水深：</b>{depth} m<br>
        </div>
        """

        folium.PolyLine(
            locations=coords,
            color=color,
            weight=weight,
            opacity=0.85,
            tooltip=f"第{seg['hachi_no']}鉢：{seg['catch']}匹",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(m)

        # 区間の中心にマーカー
        folium.CircleMarker(
            location=[seg["center_lat"], seg["center_lon"]],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            tooltip=f"第{seg['hachi_no']}鉢",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # ── 凡例 ─────────────────────────────────────────────
    legend_html = """
    <div style="position:fixed; bottom:30px; left:30px; z-index:1000;
                background:white; padding:10px; border-radius:8px;
                border:1px solid #ccc; font-family:sans-serif; font-size:13px;">
      <b>釣果の凡例（匹数で固定）</b><br>
      <span style="color:#d7191c;">●</span> 25匹以上（最高）<br>
      <span style="color:#fd8d3c;">●</span> 20〜24匹（高）<br>
      <span style="color:#ffd700;">●</span> 15〜19匹（良）<br>
      <span style="color:#74add1;">●</span> 10〜14匹（まあまあ）<br>
      <span style="color:#2c7bb6;">●</span> 0〜9匹（ダメ）<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # ── 地図を表示 ────────────────────────────────────────
    st.caption("線をクリックすると水温・塩分・水深が表示されます")
    st_folium(m, use_container_width=True, height=550, returned_objects=[])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 4: データ保存 & 過去の操業履歴
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 4 　データを保存する・過去の操業を見る")

# ── 今日のデータを保存 ───────────────────────────────────
# GPS分割（segments）が無くても、STEP 1の操業データだけで保存できる
if st.session_state.ocr_data and "error" not in st.session_state.ocr_data:
    od = st.session_state.ocr_data
    has_gps = bool(st.session_state.segments)

    st.subheader("💾 今日の操業データを保存")
    st.info(
        f"📅 {od.get('date')}　"
        f"🐟 総釣果: {sum(x['count'] for x in od.get('catch_per_hachi',[]))} 匹 / "
        f"{od.get('total_hachi')} 鉢"
    )

    if not has_gps:
        st.warning(
            "⚠️ GPSログがありません（STEP 2未実施）。"
            "釣果・水温などの記録だけ保存します。地図表示はできませんが、データは残ります。"
        )

    if st.button("📥 このデータをDBに保存する", type="primary"):
        try:
            segs = st.session_state.segments or []
            op_id = save_operation(od, segs)
            st.success(f"✅ 保存しました！（操業ID: {op_id}）")
            st.rerun()
        except Exception as e:
            st.error(f"保存エラー: {e}")
else:
    st.info("STEP 1 で操業データを確定すると、ここから保存できます（GPSは無くてもOK）。")

st.markdown("---")

# ── 過去の操業履歴 ───────────────────────────────────────
st.subheader("📋 過去の操業履歴")

ops = list_operations()

if not ops:
    st.write("まだデータが保存されていません。")
else:
    # 一覧テーブル
    df_ops = pd.DataFrame([{
        "ID":       o["id"],
        "操業日":   o["op_date"],
        "場所":     o["location"] or "—",
        "エサ":     o["bait"] or "—",
        "鉢数":     o["total_hachi"],
        "総釣果":   o["total_catch"],
        "開始":     o["start_time"],
        "終了":     o["end_time"],
        "表層水温": f"{o['surface_temp']}℃" if o["surface_temp"] else "—",
        "底水温":   f"{o['bottom_temp']}℃"  if o["bottom_temp"]  else "—",
    } for o in ops])
    st.dataframe(df_ops, use_container_width=True, hide_index=True)

    # 読み込みと削除
    col_load, col_del = st.columns([2, 1])
    with col_load:
        selected_id = st.selectbox(
            "操業を選んで地図で見る",
            options=[o["id"] for o in ops],
            format_func=lambda i: next(
                f"{o['op_date']} ({o['total_catch']}匹/{o['total_hachi']}鉢)"
                for o in ops if o["id"] == i
            ),
        )
        if st.button("🗺️ 選択した操業を読み込む"):
            loaded_ocr, loaded_segs = load_operation(selected_id)
            st.session_state.ocr_data  = loaded_ocr
            st.session_state.segments  = loaded_segs
            st.success("読み込みました！STEP 3 の地図が更新されます。")
            st.rerun()

    with col_del:
        if st.button("🗑️ 選択した操業を削除", type="secondary"):
            delete_operation(selected_id)
            st.warning("削除しました。")
            st.rerun()
