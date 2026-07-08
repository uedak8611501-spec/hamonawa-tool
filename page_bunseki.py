"""
データ分析ページ（旧STEP 5）
水温×場所で鉄板ポイントをさがす。ここに分析機能を追加していく。
"""
import json

import streamlit as st
import pandas as pd
import altair as alt
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium

from seabed import get_depths
from common import (
    fetch_all_segments,
    catch_color, catch_weight, CATCH_LEGEND_HTML,
)

st.title("🔍 データ分析（水温×場所で鉄板さがし）")
st.caption(
    "日付ではなく「水温」で過去の実績をしぼり込みます。"
    "年によって海の進み方は違っても、同じ水温なら魚の群れは再現しやすい——"
    "という考え方です。来年のハモ漁で「今この水温なら、ここが狙い目」を見つけるための機能です。"
)

st6_all = fetch_all_segments()

if not st6_all:
    st.info("GPS付きで保存した操業がまだありません。「🎣 延縄記録」ページで保存すると、ここで分析できます。")
else:
    # ── 日付で絞る（ふだんは全部のままでOK） ──
    all_dates = sorted({s["op_date"] for s in st6_all})
    sel_dates = st.multiselect(
        "操業日で絞る（未選択なら全部）",
        options=all_dates,
        default=all_dates,
    )
    if not sel_dates:
        sel_dates = all_dates
    st6_segs = [s for s in st6_all if s["op_date"] in sel_dates]
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
        "（水温が **0℃** の鉢は「計測なし」とみなして分析から除外します）"
    )

    # 選んだ水温が記録されている鉢だけを対象にする。
    # 水温0℃は「計測なし」を意味するので、分析から除外する。
    has_temp = [
        s for s in st6_segs
        if s.get(temp_key) is not None
        and float(s[temp_key]) != 0
        and s["center_lat"]
    ]

    if not has_temp:
        st.warning(
            f"{temp_source}が記録された操業がまだありません。"
            f"{temp_source}を入力して保存すると、ここで検索できるようになります。"
        )
    else:
        temps = [float(s[temp_key]) for s in has_temp]
        tmin, tmax = min(temps), max(temps)

        # スライダーは 15〜25℃ の固定はばで、0.1℃きざみで細かく見られるようにする。
        s_min, s_max = 15.0, 25.0
        # 初期表示は実際のデータの最小〜最大（ただし15〜25の範囲内に収める）
        default_lo = max(s_min, round(tmin, 1))
        default_hi = min(s_max, round(tmax, 1))
        if default_lo > default_hi:
            default_lo, default_hi = s_min, s_max
        lo, hi = st.slider(
            f"{temp_source}レンジ（℃）　— このはばの水温だった鉢だけを表示します",
            min_value=s_min, max_value=s_max,
            value=(default_lo, default_hi),
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

            # 鉢ごとの軌跡も色分けで重ねる（旧STEP5の重ね地図をここに統合）
            track_group = folium.FeatureGroup(name="鉢ごとの軌跡", show=True)
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

            # 水深はGEBCOから自動取得する。結果は30日間キャッシュされるので、
            # 待つのは初回だけ。全操業分をまとめて取るので、フィルタを変えても再取得しない。
            coords_all = tuple(
                (round(s["center_lat"], 4), round(s["center_lon"], 4))
                for s in st6_all if s["center_lat"]
            )
            with st.spinner("海底水深データを取得中...（初回だけ数秒）"):
                depths_all = get_depths(coords_all)
            dmap = dict(zip(coords_all, depths_all))
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
                st.warning("水深データを取得できなかったため、早見表を作れませんでした。")
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
                    # 最初はデータにピッタリ合わせ（zero=Falseで0からにしない）、
                    # .interactive() でマウス操作（ホイール拡大・ドラッグ移動）できるようにする。
                    st.markdown("**バブル図：よこ＝水深 / たて＝水温 / 丸の大きさ＝釣果**")
                    st.caption(
                        "大きい丸が固まっている所＝その水温×水深がよく釣れる組み合わせです。"
                        "🖱 マウスのホイールで拡大・縮小、ドラッグで移動できます。ダブルクリックで元に戻ります。"
                    )
                    bubble = (
                        alt.Chart(grid_df)
                        .mark_circle(opacity=0.6)
                        .encode(
                            x=alt.X("水深:Q", title="水深(m)", scale=alt.Scale(zero=False)),
                            y=alt.Y("水温:Q", title="水温(℃)", scale=alt.Scale(zero=False)),
                            size=alt.Size("釣果:Q", title="釣果(匹)", scale=alt.Scale(range=[20, 600])),
                            color=alt.Color("釣果:Q", title="釣果(匹)", scale=alt.Scale(scheme="turbo")),
                            tooltip=["水温", "水深", "釣果"],
                        )
                        .properties(height=420)
                        .interactive()
                    )
                    st.altair_chart(bubble, use_container_width=True)
