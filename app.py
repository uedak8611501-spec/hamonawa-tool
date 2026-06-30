"""
延縄（ハモ）操業データ統合管理ツール
Streamlit メインアプリ
"""
import json
from datetime import datetime, date, time

import streamlit as st
import pandas as pd
import folium
from folium.plugins import Draw, HeatMap
from streamlit_folium import st_folium

from gps_processor import load_gps_csv, filter_by_time, split_into_hachi, merge_catch_to_segments, polyline_to_track
from database import init_db, save_operation, update_operation, list_operations, load_operation, delete_operation, load_all_segments
from seabed import get_depths

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
if "editing_op_id" not in st.session_state:
    st.session_state.editing_op_id = None  # 編集中の操業ID（修正モード）


def _parse_time(s, default=time(6, 0)):
    if not s:
        return default
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return default


# ── 釣果に応じた色・太さ（絶対値で5段階に固定）──────────────
def catch_color(catch):
    """釣果数の絶対値で色を決める（その日の良し悪しに左右されない）"""
    if catch >= 25:
        return "#d7191c"  # 赤：最高
    elif catch >= 20:
        return "#fd8d3c"  # 橙：高
    elif catch >= 15:
        return "#ffd700"  # 黄：良
    elif catch >= 10:
        return "#7fbf3f"  # 黄緑：まあまあ
    else:
        return "#2c7bb6"  # 青：ダメ（0〜9匹）


def catch_weight(catch):
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


CATCH_LEGEND_HTML = """
<div style="position:fixed; bottom:30px; left:30px; z-index:1000;
            background:white; padding:10px; border-radius:8px;
            border:1px solid #ccc; font-family:sans-serif; font-size:13px;">
  <span style="color:#d7191c;">●</span> 25匹以上<br>
  <span style="color:#fd8d3c;">●</span> 20〜24匹<br>
  <span style="color:#ffd700;">●</span> 15〜19匹<br>
  <span style="color:#7fbf3f;">●</span> 10〜14匹<br>
  <span style="color:#2c7bb6;">●</span> 0〜9匹<br>
</div>
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: 操業データの入力
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 1 　操業データの入力")

with st.container():
    if st.session_state.editing_op_id is not None:
        ecol1, ecol2 = st.columns([3, 1])
        with ecol1:
            st.warning(f"✏️ 編集モード：操業ID {st.session_state.editing_op_id} を修正中。"
                       "値を直して STEP 4 の「編集を保存」を押してください。")
        with ecol2:
            if st.button("✖ 編集をやめる"):
                st.session_state.editing_op_id = None
                st.session_state.ocr_data = None
                st.session_state.segments = None
                st.rerun()
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
            start_time = st.time_input("投入開始時刻", value=_parse_time(d.get("start_time"), time(3, 0)), step=60)
            end_time = st.time_input("投入終了時刻", value=_parse_time(d.get("end_time"), time(4, 0)), step=60)

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

        color  = catch_color(seg["catch"])
        weight = catch_weight(seg["catch"])

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
    m.get_root().html.add_child(folium.Element(CATCH_LEGEND_HTML))

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

    if not has_gps and st.session_state.editing_op_id is None:
        st.warning(
            "⚠️ GPSログがありません（STEP 2未実施）。"
            "釣果・水温などの記録だけ保存します。地図表示はできませんが、データは残ります。"
        )

    if st.session_state.editing_op_id is not None:
        # ── 編集モード：既存レコードを上書き ──
        if st.button("✏️ 編集を保存（上書き）", type="primary"):
            try:
                update_operation(st.session_state.editing_op_id, od)
                st.success(f"✅ 操業ID {st.session_state.editing_op_id} を更新しました！")
                st.session_state.editing_op_id = None
                st.rerun()
            except Exception as e:
                st.error(f"更新エラー: {e}")
    else:
        # ── 新規保存 ──
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

    # 操業を選択
    selected_id = st.selectbox(
        "操業を選ぶ",
        options=[o["id"] for o in ops],
        format_func=lambda i: next(
            f"ID{o['id']}　{o['op_date']} ({o['total_catch']}匹/{o['total_hachi']}鉢)"
            for o in ops if o["id"] == i
        ),
    )

    col_load, col_edit, col_del = st.columns(3)
    with col_load:
        if st.button("🗺️ 地図で見る", use_container_width=True):
            loaded_ocr, loaded_segs = load_operation(selected_id)
            st.session_state.ocr_data  = loaded_ocr
            st.session_state.segments  = loaded_segs
            st.session_state.total_hachi = int(loaded_ocr.get("total_hachi") or 1)
            st.session_state.editing_op_id = None
            st.success("読み込みました！STEP 3 の地図が更新されます。")
            st.rerun()

    with col_edit:
        if st.button("✏️ 編集する", use_container_width=True):
            loaded_ocr, loaded_segs = load_operation(selected_id)
            st.session_state.ocr_data  = loaded_ocr
            st.session_state.segments  = loaded_segs
            st.session_state.total_hachi = int(loaded_ocr.get("total_hachi") or 1)
            st.session_state.editing_op_id = selected_id
            st.success(f"ID {selected_id} を編集モードで開きました。STEP 1 で値を直してください。")
            st.rerun()

    with col_del:
        if st.button("🗑️ 削除する", type="secondary", use_container_width=True):
            # 誤って消してもすぐ戻せるよう、削除する前に中身を退避しておく
            deleted_ocr, deleted_segs = load_operation(selected_id)
            st.session_state.last_deleted = {
                "id":       selected_id,
                "date":     deleted_ocr.get("date"),
                "ocr_data": deleted_ocr,
                "segments": deleted_segs,
            }
            delete_operation(selected_id)
            st.warning("削除しました。下の「↩️ 削除を取り消す」ですぐ戻せます。")
            st.rerun()

# ── 削除の取り消し（直前の削除だけ復元できる） ──────────────
# ※ if/else の外に置く。最後の1件を消すと履歴が空になり else に入らないため。
if st.session_state.get("last_deleted"):
    ld = st.session_state.last_deleted
    st.info(f"🗑️ 直前に削除した操業（元ID{ld['id']}・{ld['date']}）を復元できます。")
    if st.button("↩️ 削除を取り消す（復元する）", type="primary", use_container_width=True):
        new_id = save_operation(ld["ocr_data"], ld["segments"])
        st.session_state.last_deleted = None
        st.success(f"復元しました！（新しいID{new_id}で保存し直しました）")
        st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 5: 全操業の重ね地図（鉄板ポイント分析）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 5 　全操業の重ね地図（鉄板ポイント分析）")

all_segs = load_all_segments()

if not all_segs:
    st.info("GPS付きで保存した操業がまだありません。STEP 1〜4で保存すると、ここに重ねて表示されます。")
else:
    # ── 日付フィルタ ──
    all_dates = sorted({s["op_date"] for s in all_segs})
    sel_dates = st.multiselect(
        "表示する操業日を選択（未選択なら全部表示）",
        options=all_dates,
        default=all_dates,
    )
    if not sel_dates:
        sel_dates = all_dates

    view_segs = [s for s in all_segs if s["op_date"] in sel_dates]
    st.caption(f"表示中：{len(sel_dates)} 日分 / 合計 {len(view_segs)} 鉢")

    # ── 地図の中心 ──
    lats = [s["center_lat"] for s in view_segs if s["center_lat"]]
    lons = [s["center_lon"] for s in view_segs if s["center_lon"]]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    m5 = folium.Map(location=center, zoom_start=13, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="標準地図").add_to(m5)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="衛星写真",
    ).add_to(m5)
    folium.TileLayer(
        tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
        attr="OpenSeaMap", name="海図レイヤー", overlay=True,
    ).add_to(m5)

    # ── 全鉢を重ねて描画 ──
    for s in view_segs:
        try:
            gps = json.loads(s["gps_points"]) if s["gps_points"] else []
        except Exception:
            gps = []
        if len(gps) < 2:
            continue
        coords = [(p[0], p[1]) for p in gps]
        color = catch_color(s["catch"])
        weight = catch_weight(s["catch"])

        popup_html = f"""
        <div style="font-family:sans-serif; min-width:180px;">
          <h4 style="margin:4px 0; color:{color};">{s['op_date']} 第{s['hachi_no']}鉢</h4>
          <hr style="margin:4px 0;">
          <b>🐟 釣果：</b>{s['catch']} 匹<br>
          <b>📍 場所：</b>{s['location'] or '—'}<br>
          <b>🌡 底水温：</b>{s['bottom_temp']}℃<br>
          <b>🌊 最大水深：</b>{s['max_depth']} m<br>
        </div>
        """
        folium.PolyLine(
            locations=coords, color=color, weight=weight, opacity=0.75,
            tooltip=f"{s['op_date']} 第{s['hachi_no']}鉢：{s['catch']}匹",
            popup=folium.Popup(popup_html, max_width=260),
        ).add_to(m5)

    folium.LayerControl(collapsed=False).add_to(m5)
    m5.get_root().html.add_child(folium.Element(CATCH_LEGEND_HTML))

    st.caption("赤い線が重なる場所＝いつもよく釣れる鉄板ポイントです")
    st_folium(m5, use_container_width=True, height=600, returned_objects=[])

    # 「水深 × 釣果」の単独分析はリセット（削除）した。
    # 理由: 水深だけでは釣果を説明できない（同じ場所でも釣れる日と釣れない日がある）。
    #       水温など他の条件と運が混ざるため、水深単独の相関は誤った傾向を示す。
    # 今後は「水温 × 水深」を組み合わせた分析（STEP 6で検討中）に置き換える。


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 6: 水温で探す（魚の群れの再現性ポイント分析）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.header("STEP 6 　水温で探す 🌡🔍")
st.caption(
    "日付ではなく「水温」で過去の実績をしぼり込みます。"
    "年によって海の進み方は違っても、同じ水温なら魚の群れは再現しやすい——"
    "という考え方です。来年のハモ漁で「今この水温なら、ここが狙い目」を見つけるための機能です。"
)

st6_segs = load_all_segments()

if not st6_segs:
    st.info("GPS付きで保存した操業がまだありません。STEP 1〜4で保存すると、ここで水温検索できます。")
else:
    # ── 水温の種類を選ぶ（ふだんは底水温／レンタル機返却後は表層水温） ──
    temp_source = st.radio(
        "分析に使う水温",
        ["底水温", "表層水温"],
        horizontal=True,
    )
    temp_key = "bottom_temp" if temp_source == "底水温" else "surface_temp"
    st.caption(
        "ふだんは **底水温** で分析します。"
        "底水温の計測機（レンタル）を返却したあとは「表層水温」に切り替えれば、"
        "表層水温だけで同じ分析を続けられます。"
    )

    # 選んだ水温が記録されている鉢だけを対象にする
    has_temp = [s for s in st6_segs if s.get(temp_key) is not None and s["center_lat"]]

    if not has_temp:
        st.warning(
            f"{temp_source}が記録された操業がまだありません。"
            f"{temp_source}を入力して保存すると、ここで検索できるようになります。"
        )
    else:
        temps = [float(s[temp_key]) for s in has_temp]
        tmin, tmax = min(temps), max(temps)

        if tmin == tmax:
            st.info(f"記録されている{temp_source}は {tmin}℃ の1種類だけです。全部を表示します。")
            lo, hi = tmin, tmax
        else:
            # スライダーは少し余裕をもたせる（端のデータも選べるように）
            s_min = round(tmin - 0.5, 1)
            s_max = round(tmax + 0.5, 1)
            lo, hi = st.slider(
                f"{temp_source}レンジ（℃）　— このはばの水温だった鉢だけを表示します",
                min_value=s_min, max_value=s_max,
                value=(round(tmin, 1), round(tmax, 1)),
                step=0.1,
            )

        # 選んだ水温レンジの鉢だけ抽出
        sel = [s for s in has_temp if lo <= float(s[temp_key]) <= hi]
        st.caption(
            f"🌡 {temp_source} {lo}〜{hi}℃　→　該当 {len(sel)} 鉢"
            f"（{len({s['op_date'] for s in sel})} 日分）"
        )

        if not sel:
            st.warning("このレンジに当てはまる鉢がありません。はばを広げてみてください。")
        else:
            # ── ① 全体ヒートマップ ＋ ② レンジ検索の地図 ──
            lats = [s["center_lat"] for s in sel]
            lons = [s["center_lon"] for s in sel]
            center6 = [sum(lats) / len(lats), sum(lons) / len(lons)]

            m6 = folium.Map(location=center6, zoom_start=13, control_scale=True)
            folium.TileLayer("OpenStreetMap", name="標準地図").add_to(m6)
            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                attr="Esri", name="衛星写真",
            ).add_to(m6)
            folium.TileLayer(
                tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
                attr="OpenSeaMap", name="海図レイヤー", overlay=True,
            ).add_to(m6)

            # ヒートマップは「10匹以上」釣れた鉢だけで集計する。
            # ※9匹以下のダメなポイントは、何回やっても釣れない。それを点の数で
            #   積み上げてしまうと色が濃く見えて誤解を生むため、集計から除外する。
            HEAT_MIN_CATCH = 10
            heat_data = [
                [s["center_lat"], s["center_lon"], float(s["catch"])]
                for s in sel if s["catch"] and s["catch"] >= HEAT_MIN_CATCH
            ]
            if heat_data:
                HeatMap(
                    heat_data, name="🔥 釣果ヒートマップ（10匹以上）",
                    radius=22, blur=18, min_opacity=0.35,
                ).add_to(m6)
            else:
                st.info("このレンジには10匹以上釣れた鉢がないため、ヒートマップは表示されません。")

            # 鉢ごとの軌跡も色分けで重ねる（個別に見たいとき用）
            track_group = folium.FeatureGroup(name="鉢ごとの軌跡", show=False)
            for s in sel:
                try:
                    gps = json.loads(s["gps_points"]) if s["gps_points"] else []
                except Exception:
                    gps = []
                if len(gps) < 2:
                    continue
                coords = [(p[0], p[1]) for p in gps]
                color = catch_color(s["catch"])
                popup_html = f"""
                <div style="font-family:sans-serif; min-width:180px;">
                  <h4 style="margin:4px 0; color:{color};">{s['op_date']} 第{s['hachi_no']}鉢</h4>
                  <hr style="margin:4px 0;">
                  <b>🐟 釣果：</b>{s['catch']} 匹<br>
                  <b>🌡 {temp_source}：</b>{s[temp_key]}℃<br>
                  <b>📍 場所：</b>{s['location'] or '—'}<br>
                </div>
                """
                folium.PolyLine(
                    locations=coords, color=color, weight=catch_weight(s["catch"]),
                    opacity=0.8,
                    tooltip=f"{s['op_date']} 第{s['hachi_no']}鉢：{s['catch']}匹（{s[temp_key]}℃）",
                    popup=folium.Popup(popup_html, max_width=260),
                ).add_to(track_group)
            track_group.add_to(m6)

            folium.LayerControl(collapsed=False).add_to(m6)
            m6.get_root().html.add_child(folium.Element(CATCH_LEGEND_HTML))

            st.caption("🔥 赤く光る場所＝この水温のときによく釣れる鉄板エリアです（**10匹以上**の鉢だけで集計。左上で軌跡の表示も切り替えられます）")
            st_folium(m6, use_container_width=True, height=600, returned_objects=[], key="map6")

            # ── ③ よく釣れる場所の環境リスト（水深ごと） ──
            st.markdown("---")
            st.subheader("📋 この水温でよく釣れる場所の環境リスト")
            st.caption(
                "選んだ水温レンジで、海底の水深ごとに「平均何匹／最大何匹／何回釣れたか」をまとめます。"
                "水深はGEBCO（約450mメッシュ）から自動取得します。"
            )

            if st.button("🌊 水深を取得して環境リストを作る", type="primary", key="depth_btn6"):
                coords_all = tuple(
                    (round(s["center_lat"], 4), round(s["center_lon"], 4))
                    for s in has_temp
                )
                with st.spinner("海底水深データを取得中...（数秒）"):
                    depths_all = get_depths(coords_all)
                # 座標→水深 の対応表を作って退避（スライダーを動かしても再取得しない）
                dmap = {}
                for (la, lo_), d in zip(coords_all, depths_all):
                    dmap[(la, lo_)] = d
                st.session_state.temp_depth_map = dmap

            dmap = st.session_state.get("temp_depth_map")
            if dmap:
                rows = []
                for s in sel:
                    key = (round(s["center_lat"], 4), round(s["center_lon"], 4))
                    d = dmap.get(key)
                    if d is None:
                        continue
                    rows.append({"水深(m)": d, "釣果(匹)": s["catch"]})

                env_df = pd.DataFrame(rows)
                if len(env_df) >= 1:
                    bins = [0, 20, 40, 60, 80, 100, 9999]
                    labels = ["0-20m", "20-40m", "40-60m", "60-80m", "80-100m", "100m以上"]
                    env_df["水深帯"] = pd.cut(env_df["水深(m)"], bins=bins, labels=labels, right=False)
                    band = env_df.groupby("水深帯", observed=True)["釣果(匹)"].agg(
                        ["mean", "max", "count"]
                    )
                    band = band.rename(
                        columns={"mean": "平均釣果", "max": "最大釣果", "count": "鉢数"}
                    )
                    band["平均釣果"] = band["平均釣果"].round(1)
                    band["底質"] = "（海しるAPI準備中）"  # ← キー取得後にここを埋める
                    st.dataframe(band, use_container_width=True)

                    # いちばん釣れている水深帯をひとことで
                    best = band["平均釣果"].idxmax()
                    best_avg = band.loc[best, "平均釣果"]
                    st.success(
                        f"💡 {temp_source} {lo}〜{hi}℃ のときは、"
                        f"**水深 {best} で平均 {best_avg} 匹** がいちばんの狙い目です。"
                    )
                    st.caption(
                        "※「底質（砂・泥・礫）」の列は、海しるAPIの無料キーが取れ次第ここに表示します。"
                    )
                else:
                    st.warning("このレンジでは水深を取得できた鉢がありませんでした。")

            # ── ④ 水温×水深 の早見表＋バブル図（スライダーに関係なく全データで集計） ──
            st.markdown("---")
            st.subheader("📊 水温 × 水深 の早見表（鉄板の組み合わせさがし）")
            st.caption(
                "水深だけ・水温だけでは釣果は決まりません。"
                "「水温帯 × 水深帯」の組み合わせごとに平均釣果と回数をまとめます。"
                "上のスライダーに関係なく全データで集計します。"
                "回数が多いマスほど信用でき、回数1は“まだ運かも”です。"
            )

            if not dmap:
                st.info("上の「🌊 水深を取得して環境リストを作る」ボタンを押すと、ここに早見表が出ます。")
            else:
                grid_rows = []
                for s in has_temp:
                    key = (round(s["center_lat"], 4), round(s["center_lon"], 4))
                    d = dmap.get(key)
                    if d is None:
                        continue
                    grid_rows.append({
                        "水温": float(s[temp_key]),
                        "水深": d,
                        "釣果": s["catch"],
                    })
                grid_df = pd.DataFrame(grid_rows)

                if len(grid_df) < 1:
                    st.warning("水深を取得できた鉢がなく、早見表を作れませんでした。")
                else:
                    # 水温帯＝1℃ごと / 水深帯＝20mごと に区切る
                    t_lo = int(grid_df["水温"].min())
                    t_hi = int(grid_df["水温"].max()) + 1
                    t_edges = list(range(t_lo, t_hi + 1))
                    t_labels = [f"{t_edges[i]}-{t_edges[i+1]}℃" for i in range(len(t_edges) - 1)]
                    d_edges = [0, 20, 40, 60, 80, 100, 9999]
                    d_labels = ["0-20m", "20-40m", "40-60m", "60-80m", "80-100m", "100m以上"]

                    grid_df["水温帯"] = pd.cut(grid_df["水温"], bins=t_edges, labels=t_labels, right=False)
                    grid_df["水深帯"] = pd.cut(grid_df["水深"], bins=d_edges, labels=d_labels, right=False)

                    mean_p = grid_df.pivot_table(
                        index="水温帯", columns="水深帯", values="釣果",
                        aggfunc="mean", observed=True,
                    )
                    cnt_p = grid_df.pivot_table(
                        index="水温帯", columns="水深帯", values="釣果",
                        aggfunc="count", observed=True,
                    )

                    # 「平均(回数)」の文字に整形して表示
                    disp = pd.DataFrame(index=mean_p.index, columns=mean_p.columns, dtype="object")
                    for i in mean_p.index:
                        for c in mean_p.columns:
                            m = mean_p.loc[i, c]
                            n = cnt_p.loc[i, c]
                            disp.loc[i, c] = f"{m:.0f}匹({int(n)}回)" if pd.notna(m) else "—"

                    st.markdown("**早見表：たて＝水温帯 / よこ＝水深帯　→　平均釣果(回数)**")
                    st.dataframe(disp, use_container_width=True)

                    # いちばん信用できる組み合わせ（2回以上やって平均が高いマス）
                    trust = mean_p.where(cnt_p >= 2)
                    if trust.notna().to_numpy().any():
                        stacked = trust.stack()
                        bi = stacked.idxmax()
                        bv = stacked.max()
                        st.success(
                            f"💡 いちばん信用できる鉄板の組み合わせ："
                            f"**水温 {bi[0]} × 水深 {bi[1]} → 平均 {bv:.0f}匹"
                            f"（{int(cnt_p.loc[bi])}回）**"
                        )
                    else:
                        st.caption("※まだ「2回以上やったマス」が少なく、鉄板の断定はできません。データが貯まると出ます。")

                    # バブル図：よこ＝水深 / たて＝水温 / 丸の大きさ＝釣果
                    st.markdown("**バブル図：よこ＝水深 / たて＝水温 / 丸の大きさ＝釣果**")
                    st.caption("大きい丸が固まっている所＝その水温×水深がよく釣れる組み合わせです。")
                    st.scatter_chart(grid_df, x="水深", y="水温", size="釣果", height=420)
