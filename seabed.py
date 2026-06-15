"""
海底環境データ取得モジュール

水深: OpenTopoData の GEBCO 2020（全球水深データ、APIキー不要・無料）
  https://www.opentopodata.org/datasets/gebco/
  ※解像度は約450mメッシュ。瀬戸内海の細かい起伏までは出ないが、
    場所ごとの大まかな水深傾向の把握には使える。

底質（砂/泥/岩）は海しるAPI（要サブスクリプションキー）で別途対応予定。
"""
import requests
import streamlit as st

OPENTOPO_URL = "https://api.opentopodata.org/v1/gebco2020"
_CHUNK = 90  # 1リクエストあたりの座標数上限（無料枠は100まで）


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 30)
def get_depths(coords: tuple) -> list:
    """
    座標ごとの水深(m, 海面下を正の値)を返す。
    coords: ((lat, lon), (lat, lon), ...) のタプル（キャッシュのためタプル必須）
    取得できなかった点は None。
    """
    depths: list = []
    items = list(coords)
    for i in range(0, len(items), _CHUNK):
        chunk = items[i:i + _CHUNK]
        locs = "|".join(f"{lat},{lon}" for lat, lon in chunk)
        try:
            r = requests.get(OPENTOPO_URL, params={"locations": locs}, timeout=30)
            data = r.json()
            for res in data.get("results", []):
                elev = res.get("elevation")
                # GEBCOは陸が正・海が負。海面下を正の「水深」に変換。
                depths.append(None if elev is None else round(-elev, 1))
        except Exception:
            depths.extend([None] * len(chunk))
    return depths
