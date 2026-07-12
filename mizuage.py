"""
水揚げ集計グラフページ
朝日水産の仕切票データ（OneDrive上の 朝日水産_水揚げ集計.csv）を読み込んでグラフ表示する。
CSVの場所は Streamlit Secrets の MIZUAGE_CSV_URL にOneDrive共有リンクを登録する。
データそのものはGitHubに置かない（非公開のまま）。
"""
import base64
import io
import re

import altair as alt
import pandas as pd
import requests
import streamlit as st

from common import fetch_operations

# ページ設定は app.py（エントリポイント）で一括して行う

st.title("📊 水揚げ集計（朝日水産 仕切票）")

# ハモの品目（サイズ）の表示順と色
# 主力の大・大〇は強調色（濃い青・濃いオレンジ）、特大・小は薄色、上り（最安値）は黒。
# 青⇔オレンジの組み合わせは色の見え方が異なる人にも区別しやすい。
HAMO_SIZE_ORDER = ["大", "大〇", "特大", "小", "上り"]
HAMO_SIZE_COLORS = [
    "#08519c",  # 大: 濃い青（主力・強調）
    "#d95f02",  # 大〇: 濃いオレンジ（主力・強調）
    "#9ecae1",  # 特大: 薄い水色
    "#fdd0a2",  # 小: 薄いオレンジ
    "#000000",  # 上り: 黒（単価最安）
]


def _direct_url(share_url: str) -> str:
    """OneDriveの共有リンクを直接ダウンロード用URLに変換する"""
    url = share_url.strip()
    # 新形式リンク https://1drv.ms/x/c/<CID>/<共有トークン>?e=... に対応
    # （SharePoint基盤へ移行済みのOneDrive個人アカウント用。動作確認済み）
    m = re.match(r"https://1drv\.ms/[a-z]+/c/([0-9a-fA-F]+)/([A-Za-z0-9_\-!]+)", url)
    if m:
        cid, token = m.group(1), m.group(2)
        return (f"https://my.microsoftpersonalcontent.com/personal/{cid}"
                f"/_layouts/15/download.aspx?share={token}")
    # 旧形式リンクは従来の shares API 変換
    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"


@st.cache_data(ttl=600, show_spinner="仕切票データを読み込み中…")
def load_csv(share_url: str) -> pd.DataFrame:
    r = requests.get(_direct_url(share_url), timeout=30)
    if r.status_code != 200:
        # 共有リンク変換で取れなかったら、そのままのURLでも試す
        r = requests.get(share_url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="utf-8-sig")
    df["日付"] = pd.to_datetime(df["日付"], errors="coerce")
    df["重量(kg)"] = pd.to_numeric(df["重量(kg)"], errors="coerce")
    df["単価(円/kg)"] = pd.to_numeric(df["単価(円/kg)"], errors="coerce")
    df = df.dropna(subset=["日付"])
    return df


# ── Secrets（CSVの場所）チェック ──────────────────────────────
share_url = st.secrets.get("MIZUAGE_CSV_URL", "")
if not share_url:
    st.warning("まだCSVの場所が設定されていません。")
    st.markdown(
        """
        **初回設定（1回だけ）**
        1. パソコンで `朝日水産_水揚げ集計.csv` を右クリック → **OneDrive → 共有** → 「リンクのコピー」
        2. [share.streamlit.io](https://share.streamlit.io) にログイン → このアプリの **Settings → Secrets** を開く
        3. 次の1行を追加して保存:
        ```toml
        MIZUAGE_CSV_URL = "コピーした共有リンク"
        ```
        """
    )
    st.stop()

try:
    df = load_csv(share_url)
except Exception as e:
    st.error(f"CSVの読み込みに失敗しました: {e}")
    st.info("OneDriveの共有リンクが有効か（削除・変更されていないか）確認してください。")
    st.stop()

mizuage = df[df["区分"] == "水揚げ"].copy()
esa = df[df["区分"] == "餌代"].copy()

if mizuage.empty:
    st.info("水揚げデータがまだありません。")
    st.stop()

# ── サマリー（一番上に大きく）───────────────────────────────
latest_day = mizuage["日付"].max()
latest_rows = mizuage[mizuage["日付"] == latest_day]
total_kg = mizuage["重量(kg)"].sum()
days = mizuage["日付"].nunique()

c1, c2, c3 = st.columns(3)
c1.metric("直近の水揚げ", f"{latest_rows['重量(kg)'].sum():.1f} kg",
          latest_day.strftime("%m/%d"))
c2.metric("累計水揚げ", f"{total_kg:,.0f} kg")
c3.metric("出漁日数", f"{days} 日")

# ── 期間フィルタ ─────────────────────────────────────────────
period = st.radio("表示期間", ["全期間", "直近30日", "直近7日"],
                  horizontal=True)
if period == "直近30日":
    cutoff = latest_day - pd.Timedelta(days=30)
    mizuage = mizuage[mizuage["日付"] >= cutoff]
    esa = esa[esa["日付"] >= cutoff]
elif period == "直近7日":
    cutoff = latest_day - pd.Timedelta(days=7)
    mizuage = mizuage[mizuage["日付"] >= cutoff]
    esa = esa[esa["日付"] >= cutoff]

if st.button("🔄 最新データに更新"):
    st.cache_data.clear()
    st.rerun()

# 棒の太さ: 表示期間の日数に合わせて自動調整（隣の日と重ならない範囲で太く）
span_days = max((mizuage["日付"].max() - mizuage["日付"].min()).days + 1, 1)
bar_size = max(5, min(30, int(550 / span_days * 0.7)))

# ── 1. ハモのサイズ内訳（日別）──────────────────────────────
# 平均サイズの折れ線を重ねられるようにするため、描画はセクション2の
# 計算が終わったあとに行う。st.container()で表示場所だけ先に確保する
# （画面上はこの位置＝サイズ内訳→平均サイズの順で表示される）。
hamo_section = st.container()

hamo = mizuage[mizuage["魚種"] == "ハモ"].copy()
if not hamo.empty:
    hamo["サイズ"] = hamo["品目"].str.replace("ハモ", "", regex=False)
    hamo_daily = hamo.groupby(["日付", "サイズ"], as_index=False)["重量(kg)"].sum()

# ── 2. ハモの平均サイズ（日別）──────────────────────────────
st.subheader("ハモの平均サイズ（日別）")
st.caption(
    "水揚げ重量 ÷ 匹数 で「1匹あたり何kgか」を出します。"
    "延縄記録の匹数には逃がした魚・逃げた魚など水揚げしない分も入っているため、"
    "下の「非水揚げ率」の分を匹数から差し引いてから計算します。"
)

avg_df = None  # サイズ内訳グラフへの重ね描き用（計算できた場合のみDataFrameが入る）

if not hamo.empty:
    loss_pct = st.slider(
        "非水揚げ率（釣った匹数のうち、水揚げに含まれない割合）",
        min_value=0, max_value=30, value=10, step=1, format="%d%%",
    )

    ops = fetch_operations()
    cnt_df = pd.DataFrame([
        {"日付": pd.to_datetime(o["op_date"]), "釣った匹数": o["total_catch"]}
        for o in ops if o.get("total_catch")
    ])

    if cnt_df.empty:
        st.info("延縄記録にまだ操業データがないため、平均サイズを計算できません。")
    else:
        w_daily = hamo.groupby("日付", as_index=False)["重量(kg)"].sum()
        avg_df = w_daily.merge(cnt_df, on="日付", how="inner")
        avg_df["水揚げ匹数(補正後)"] = (avg_df["釣った匹数"] * (1 - loss_pct / 100)).round(1)
        avg_df = avg_df[avg_df["水揚げ匹数(補正後)"] > 0]

        if avg_df.empty:
            st.info(
                "水揚げ（仕切票）と延縄記録の両方がそろった日がまだありません。"
                "※同じ日付どうしを突き合わせて計算します。"
            )
        else:
            avg_df["平均サイズ(kg/匹)"] = (
                avg_df["重量(kg)"] / avg_df["水揚げ匹数(補正後)"]
            ).round(2)

            chart_avg = (
                alt.Chart(avg_df)
                .mark_line(point=alt.OverlayMarkDef(size=80, filled=True),
                           strokeWidth=2, color="#08519c")
                .encode(
                    x=alt.X("日付:T", title="", axis=alt.Axis(format="%m/%d")),
                    y=alt.Y("平均サイズ(kg/匹):Q", title="平均サイズ (kg/匹)",
                            scale=alt.Scale(zero=False)),
                    tooltip=[
                        alt.Tooltip("日付:T", format="%m/%d"),
                        alt.Tooltip("平均サイズ(kg/匹):Q", format=".2f"),
                        alt.Tooltip("重量(kg):Q", format=".1f"),
                        "釣った匹数:Q",
                        alt.Tooltip("水揚げ匹数(補正後):Q", format=".1f"),
                    ],
                )
                .properties(height=280)
            )
            st.altair_chart(chart_avg, use_container_width=True)

            # 期間全体の平均（日ごとの平均の平均ではなく、総重量÷総匹数で出す）
            overall = avg_df["重量(kg)"].sum() / avg_df["水揚げ匹数(補正後)"].sum()
            st.metric("期間全体の平均サイズ", f"{overall:.2f} kg/匹")

            with st.expander("日ごとの数字を見る"):
                st.dataframe(
                    avg_df[["日付", "重量(kg)", "釣った匹数", "水揚げ匹数(補正後)", "平均サイズ(kg/匹)"]],
                    use_container_width=True, hide_index=True,
                )
            st.caption("※操業日と仕切票の日付が同じ日だけを突き合わせています。")

# ── 1の描画（平均サイズ計算後に、確保しておいた場所へ描く）──
with hamo_section:
    st.subheader("ハモのサイズ内訳（日別）")
    if hamo.empty:
        st.info("この期間はハモの水揚げがありません。")
    else:
        bars = (
            alt.Chart(hamo_daily)
            .mark_bar(size=bar_size, stroke="white", strokeWidth=1)
            .encode(
                x=alt.X("日付:T", title="", axis=alt.Axis(format="%m/%d")),
                y=alt.Y("重量(kg):Q", title="水揚げ量 (kg)"),
                color=alt.Color("サイズ:N", title="サイズ",
                                sort=HAMO_SIZE_ORDER,
                                scale=alt.Scale(domain=HAMO_SIZE_ORDER,
                                                range=HAMO_SIZE_COLORS)),
                order=alt.Order("color_サイズ_sort_index:Q"),
                tooltip=[alt.Tooltip("日付:T", format="%m/%d"), "サイズ:N",
                         alt.Tooltip("重量(kg):Q", format=".1f")],
            )
        )

        can_overlay = avg_df is not None and len(avg_df) > 0
        overlay_on = st.toggle(
            "📈 平均サイズ(kg/匹)の折れ線を重ねる",
            value=True,
            disabled=not can_overlay,
            help="緑の折れ線。目盛りはグラフの右側の軸です。非水揚げ率は下のスライダーで調整できます。",
        )

        if overlay_on and can_overlay:
            line = (
                alt.Chart(avg_df)
                .mark_line(point=alt.OverlayMarkDef(size=70, filled=True),
                           strokeWidth=2.5, color="#1a7f37")
                .encode(
                    x=alt.X("日付:T"),
                    y=alt.Y("平均サイズ(kg/匹):Q", title="平均サイズ (kg/匹)",
                            axis=alt.Axis(orient="right", titleColor="#1a7f37",
                                          labelColor="#1a7f37"),
                            scale=alt.Scale(zero=False)),
                    tooltip=[
                        alt.Tooltip("日付:T", format="%m/%d"),
                        alt.Tooltip("平均サイズ(kg/匹):Q", format=".2f"),
                        alt.Tooltip("水揚げ匹数(補正後):Q", format=".1f"),
                    ],
                )
            )
            # 棒(左軸=kg)と折れ線(右軸=kg/匹)はケタが違うので目盛りを別々にする
            chart1 = alt.layer(bars, line).resolve_scale(y="independent").properties(height=320)
        else:
            chart1 = bars.properties(height=320)
            if not can_overlay:
                st.caption("（延縄記録と日付が合う日がまだ無いため、折れ線は表示できません）")

        st.altair_chart(chart1, use_container_width=True)

# ── 3. 月別まとめ（水揚げ・餌代）────────────────────────────
st.subheader("月別まとめ")
m = mizuage.copy()
m["月"] = m["日付"].dt.strftime("%Y-%m")
monthly_kg = m.groupby("月")["重量(kg)"].sum()
monthly_days = m.groupby("月")["日付"].nunique()

e = esa.copy()
if not e.empty:
    e["月"] = e["日付"].dt.strftime("%Y-%m")
    e["餌代(円)"] = e["重量(kg)"] * e["単価(円/kg)"]
    monthly_esa = e.groupby("月")["餌代(円)"].sum()
else:
    monthly_esa = pd.Series(dtype=float)

summary = pd.DataFrame({
    "出漁日数": monthly_days,
    "水揚げ (kg)": monthly_kg.round(1),
    "1日平均 (kg)": (monthly_kg / monthly_days).round(1),
    "餌代 (円)": monthly_esa.round(0),
}).fillna(0)
st.dataframe(summary, use_container_width=True)
st.caption("※餌代は伝票に単価が書いてあった分のみの集計です。")

st.divider()
st.caption(f"データ最終日: {latest_day.strftime('%Y-%m-%d')} ／ "
           f"10分ごとに自動で最新のCSVを読み直します（「最新データに更新」ですぐ反映）")
