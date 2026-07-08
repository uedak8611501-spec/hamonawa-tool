"""
共通ヘルパーモジュール
各ページ（延縄記録・データ分析）から使う関数・定数をここに集約する。
"""
from datetime import datetime, time

import streamlit as st

from database import list_operations, load_all_segments

def _parse_time(s, default=time(6, 0)):
    if not s:
        return default
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return default


def _num_or_none(v):
    """CTDの値を数値に。0や未入力は「計測なし」としてNoneを返す"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def _fmt(v):
    """未計測(None)を「—」で表示する"""
    return "—" if v is None else v


# ── DB読み込みキャッシュ ─────────────────────────────────────
# スライダー操作などの再描画のたびにCloudflareへ取りに行かないよう、
# 読み込み結果を一時記憶する。保存・編集・削除のあとは clear_db_cache() で最新化。
@st.cache_data(ttl=600, show_spinner=False)
def fetch_operations():
    return list_operations()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_all_segments():
    return load_all_segments()


def clear_db_cache():
    """保存・編集・削除のあとに呼んで、履歴・地図を最新のデータにする"""
    fetch_operations.clear()
    fetch_all_segments.clear()


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
