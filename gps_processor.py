"""
GPSログCSVの読み込み・時刻フィルタ・鉢ごとの区間分割モジュール
"""
import math
from datetime import datetime, date
from io import StringIO

import pandas as pd


# よく使われるGPS CSVの列名パターン（自動検出用）
LAT_COLS = ["latitude", "lat", "緯度", "Lat", "Latitude"]
LON_COLS = ["longitude", "lon", "lng", "経度", "Lon", "Longitude"]
# 「時刻」は時刻のみ列なのでTIME_COLSから除外
TIME_COLS = ["timestamp", "datetime", "日時", "DateTime"]
DATE_COLS = ["date", "日付", "Date"]
TIMEONLY_COLS = ["時刻", "time", "Time"]


def _detect_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    # 部分一致フォールバック
    for col in df.columns:
        for c in candidates:
            if c.lower() in col.lower():
                return col
    return None


def load_gps_csv(file_obj) -> pd.DataFrame:
    """
    CSVファイルオブジェクトを読み込み、lat/lon/timestampを統一列名で返す。
    Raises ValueError if required columns are not found.
    """
    try:
        content = file_obj.read()
        if isinstance(content, bytes):
            # Shift-JIS / UTF-8 を自動判別
            for enc in ("utf-8-sig", "shift_jis", "cp932"):
                try:
                    text = content.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("文字コードを判別できませんでした")
        else:
            text = content

        df = pd.read_csv(StringIO(text))
    except Exception as e:
        raise ValueError(f"CSV読み込みエラー: {e}")

    lat_col = _detect_column(df, LAT_COLS)
    lon_col = _detect_column(df, LON_COLS)
    time_col = _detect_column(df, TIME_COLS)

    missing = []
    if lat_col is None:
        missing.append("緯度(latitude)")
    if lon_col is None:
        missing.append("経度(longitude)")

    # 日付と時刻が別列の場合（GPS2CSV形式）に対応
    if time_col is None:
        date_col = _detect_column(df, DATE_COLS)
        timeonly_col = _detect_column(df, TIMEONLY_COLS)
        if date_col and timeonly_col:
            # 年2桁対応: "26/06/01" → "2026/06/01"
            def fix_year(d):
                parts = str(d).split("/")
                if len(parts) == 3 and len(parts[0]) == 2:
                    return "20" + "/".join(parts)
                return d
            df["timestamp"] = pd.to_datetime(
                df[date_col].apply(fix_year).astype(str) + " " + df[timeonly_col].astype(str),
                format="%Y/%m/%d %H:%M:%S",
                errors="coerce"
            )
            time_col = "timestamp_created"
        else:
            missing.append("日時(timestamp)")

    if missing:
        raise ValueError(f"必要な列が見つかりません: {', '.join(missing)}\n検出された列: {list(df.columns)}")

    if time_col != "timestamp_created":
        df = df.rename(columns={time_col: "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    df = df.rename(columns={lat_col: "lat", lon_col: "lon"})
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df[["timestamp", "lat", "lon"] + [c for c in df.columns if c not in ("timestamp", "lat", "lon")]]


def filter_by_time(df: pd.DataFrame, op_date: date, start_hhmm: str, end_hhmm: str) -> pd.DataFrame:
    """
    操業日・開始時刻・終了時刻でGPSログを絞り込む。
    op_date: datetime.date
    start_hhmm / end_hhmm: "HH:MM" 形式の文字列
    """
    start_dt = datetime.strptime(f"{op_date} {start_hhmm}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{op_date} {end_hhmm}", "%Y-%m-%d %H:%M")

    mask = (df["timestamp"] >= start_dt) & (df["timestamp"] <= end_dt)
    filtered = df[mask].reset_index(drop=True)

    if len(filtered) == 0:
        raise ValueError(
            f"指定時刻 {start_hhmm}〜{end_hhmm} の範囲にGPSポイントがありません。\n"
            f"GPSログの時刻範囲: {df['timestamp'].min()} 〜 {df['timestamp'].max()}"
        )

    return filtered


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """2点間の距離をメートルで返す（Haversine公式）"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _cumulative_distance(df: pd.DataFrame) -> list[float]:
    """各ポイントの累積距離リスト（メートル）を返す"""
    dist = [0.0]
    for i in range(1, len(df)):
        d = _haversine_m(
            df.loc[i - 1, "lat"], df.loc[i - 1, "lon"],
            df.loc[i, "lat"], df.loc[i, "lon"],
        )
        dist.append(dist[-1] + d)
    return dist


def split_into_hachi(df: pd.DataFrame, total_hachi: int) -> list[dict]:
    """
    GPSトラックを total_hachi 等分し、各区間の情報を返す。

    Returns:
        list of dict, 各dictは:
          - hachi_no: int（1始まり）
          - points: DataFrame（区間内のGPSポイント）
          - center_lat, center_lon: 区間中心座標
          - start_time, end_time: 区間の開始・終了時刻
          - length_m: 区間距離（m）
    """
    if total_hachi <= 0:
        raise ValueError("総鉢数は1以上にしてください")
    if len(df) < total_hachi:
        raise ValueError(
            f"GPSポイント数({len(df)})が総鉢数({total_hachi})より少ないため分割できません"
        )

    cum_dist = _cumulative_distance(df)
    total_dist = cum_dist[-1]
    segment_len = total_dist / total_hachi

    segments = []
    seg_start_idx = 0

    for hachi_no in range(1, total_hachi + 1):
        target_end = segment_len * hachi_no

        if hachi_no == total_hachi:
            seg_end_idx = len(df) - 1
        else:
            # target_end を超えた最初のインデックスを探す
            seg_end_idx = seg_start_idx
            for i in range(seg_start_idx, len(df)):
                if cum_dist[i] >= target_end:
                    seg_end_idx = i
                    break

        seg_df = df.iloc[seg_start_idx: seg_end_idx + 1].reset_index(drop=True)
        center_lat = seg_df["lat"].mean()
        center_lon = seg_df["lon"].mean()
        length_m = cum_dist[seg_end_idx] - cum_dist[seg_start_idx]

        segments.append(
            {
                "hachi_no": hachi_no,
                "points": seg_df,
                "center_lat": center_lat,
                "center_lon": center_lon,
                "start_time": seg_df["timestamp"].iloc[0],
                "end_time": seg_df["timestamp"].iloc[-1],
                "length_m": length_m,
            }
        )

        seg_start_idx = seg_end_idx  # 境界点は次の区間の始点とも共有

    return segments


def merge_catch_to_segments(segments: list[dict], catch_per_hachi: list[dict]) -> list[dict]:
    """
    各区間に釣果データを紐付ける。
    catch_per_hachi: [{"hachi": 1, "count": 12}, ...]
    """
    catch_map = {item["hachi"]: item["count"] for item in catch_per_hachi}
    for seg in segments:
        seg["catch"] = catch_map.get(seg["hachi_no"], 0)
    return segments
