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

st.set_page_config(
    page_title="水揚げ集計",
    page_icon="📊",
    layout="wide",
)

st.title("📊 水揚げ集計（朝日水産 仕切票）")

# ハモの品目（サイズ）の表示順
HAMO_SIZE_ORDER = ["小", "大", "大〇", "特大", "上り"]


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

# ── 1. 日別の水揚げ量（魚種で色分け）─────────────────────────
st.subheader("日別の水揚げ量（魚種別）")
daily = (mizuage.groupby(["日付", "魚種"], as_index=False)["重量(kg)"].sum())
chart_daily = (
    alt.Chart(daily)
    .mark_bar(size=bar_size)
    .encode(
        x=alt.X("日付:T", title="", axis=alt.Axis(format="%m/%d")),
        y=alt.Y("重量(kg):Q", title="水揚げ量 (kg)"),
        color=alt.Color("魚種:N", title="魚種",
                        scale=alt.Scale(scheme="tableau10")),
        tooltip=[alt.Tooltip("日付:T", format="%m/%d"), "魚種:N",
                 alt.Tooltip("重量(kg):Q", format=".1f")],
    )
    .properties(height=320)
)
st.altair_chart(chart_daily, use_container_width=True)

# ── 2. ハモのサイズ内訳（日別）──────────────────────────────
st.subheader("ハモのサイズ内訳（日別）")
hamo = mizuage[mizuage["魚種"] == "ハモ"].copy()
if not hamo.empty:
    hamo["サイズ"] = hamo["品目"].str.replace("ハモ", "", regex=False)
    hamo_daily = hamo.groupby(["日付", "サイズ"], as_index=False)["重量(kg)"].sum()
    chart_hamo = (
        alt.Chart(hamo_daily)
        .mark_bar(size=bar_size)
        .encode(
            x=alt.X("日付:T", title="", axis=alt.Axis(format="%m/%d")),
            y=alt.Y("重量(kg):Q", title="水揚げ量 (kg)"),
            color=alt.Color("サイズ:N", title="サイズ",
                            sort=HAMO_SIZE_ORDER,
                            scale=alt.Scale(scheme="blues")),
            order=alt.Order("color_サイズ_sort_index:Q"),
            tooltip=[alt.Tooltip("日付:T", format="%m/%d"), "サイズ:N",
                     alt.Tooltip("重量(kg):Q", format=".1f")],
        )
        .properties(height=320)
    )
    st.altair_chart(chart_hamo, use_container_width=True)
else:
    st.info("この期間はハモの水揚げがありません。")

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
