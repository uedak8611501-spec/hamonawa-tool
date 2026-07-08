"""
水薙丸ハモ縄分析ツール
Streamlit エントリポイント（ページ切り替え）
  🎣 延縄記録   … STEP1〜4（入力・GPS分割・地図・保存/履歴）→ page_kiroku.py
  📊 水揚げ集計 … 朝日水産 仕切票のグラフ → mizuage.py
  🔍 データ分析 … 水温×場所で鉄板さがし → page_bunseki.py
"""
import streamlit as st

from database import init_db

st.set_page_config(
    page_title="水薙丸ハモ縄分析ツール",
    page_icon="🎣",
    layout="wide",
)

init_db()

# ── 見出しサイズの調整 ─────────────────────────────────────
# Streamlit標準の見出しはスマホだと大きすぎて圧迫感があるため、
# 全体をひとまわり小さく、スマホ(640px以下)ではさらに一段小さくする。
st.markdown("""
<style>
h1 { font-size: 1.6rem !important; }
h2 { font-size: 1.25rem !important; }
h3 { font-size: 1.05rem !important; }
@media (max-width: 640px) {
  h1 { font-size: 1.25rem !important; }
  h2 { font-size: 1.05rem !important; }
  h3 { font-size: 0.95rem !important; }
}
</style>
""", unsafe_allow_html=True)

# ── セッション初期化（全ページ共通） ─────────────────────────
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
if "saved_op_id" not in st.session_state:
    st.session_state.saved_op_id = None  # 保存済み操業ID（二重保存ガード）

# ── ページ切り替え ───────────────────────────────────────────
pg = st.navigation([
    st.Page("page_kiroku.py", title="延縄記録", icon="🎣", default=True),
    st.Page("mizuage.py", title="水揚げ集計", icon="📊"),
    st.Page("page_bunseki.py", title="データ分析", icon="🔍"),
])
pg.run()
