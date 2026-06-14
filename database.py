"""
操業データ Cloudflare D1 永続化モジュール

Streamlit Cloud のファイルはアプリ再起動で消えるため、
データは Cloudflare D1（クラウド上のSQLite）に保存する。

認証情報は Streamlit Secrets または環境変数から読み込む:
  CF_ACCOUNT_ID   : Cloudflare アカウントID
  CF_DATABASE_ID  : D1 データベースID
  CF_API_TOKEN    : D1 Edit 権限のAPIトークン
"""
import os
import json
from datetime import datetime

import requests


def _get_secret(name: str) -> str:
    """Streamlit Secrets → 環境変数 の順で認証情報を取得"""
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"認証情報 {name} が設定されていません。"
            "Streamlit Cloud の Secrets に登録してください。"
        )
    return val


def _d1_query(sql: str, params: list | None = None) -> dict:
    """
    D1 にSQLを1文実行し、結果(result[0])を返す。
    返り値の例: {"results": [...rows...], "meta": {"last_row_id": 5, ...}}
    """
    account_id = _get_secret("CF_ACCOUNT_ID")
    database_id = _get_secret("CF_DATABASE_ID")
    token = _get_secret("CF_API_TOKEN")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/d1/database/{database_id}/query"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"sql": sql}
    if params is not None:
        body["params"] = params

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    data = resp.json()

    if not data.get("success"):
        errors = data.get("errors", [])
        raise RuntimeError(f"D1エラー: {errors}\nSQL: {sql}")

    return data["result"][0]


def init_db():
    """テーブルが存在しない場合に作成する"""
    _d1_query("""
        CREATE TABLE IF NOT EXISTS operations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            op_date     TEXT NOT NULL,
            location    TEXT,
            bait        TEXT,
            start_time  TEXT,
            end_time    TEXT,
            total_hachi INTEGER,
            total_catch INTEGER,
            surface_temp    REAL,
            bottom_temp     REAL,
            surface_salinity REAL,
            bottom_salinity  REAL,
            max_depth        REAL,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _d1_query("""
        CREATE TABLE IF NOT EXISTS segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id INTEGER NOT NULL,
            hachi_no    INTEGER NOT NULL,
            catch       INTEGER DEFAULT 0,
            center_lat  REAL,
            center_lon  REAL,
            length_m    REAL,
            start_time  TEXT,
            end_time    TEXT,
            gps_points  TEXT
        )
    """)


def save_operation(ocr_data: dict, segments: list[dict]) -> int:
    """操業データと分割セグメントをD1に保存する。Returns: 新規レコードのID"""
    init_db()
    ctd = ocr_data.get("ctd") or {}
    total_catch = sum(x["count"] for x in ocr_data.get("catch_per_hachi", []))

    result = _d1_query(
        """
        INSERT INTO operations
          (op_date, location, bait, start_time, end_time, total_hachi, total_catch,
           surface_temp, bottom_temp, surface_salinity, bottom_salinity,
           max_depth, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ocr_data.get("date"),
            ocr_data.get("location"),
            ocr_data.get("bait"),
            ocr_data.get("start_time"),
            ocr_data.get("end_time"),
            ocr_data.get("total_hachi"),
            total_catch,
            ctd.get("surface_temp"),
            ctd.get("bottom_temp"),
            ctd.get("surface_salinity"),
            ctd.get("bottom_salinity"),
            ctd.get("max_depth"),
            ocr_data.get("notes"),
        ],
    )
    op_id = result["meta"]["last_row_id"]

    for seg in segments:
        pts = seg["points"]
        gps_json = json.dumps(
            list(zip(pts["lat"].tolist(), pts["lon"].tolist()))
        )
        _d1_query(
            """
            INSERT INTO segments
              (operation_id, hachi_no, catch, center_lat, center_lon,
               length_m, start_time, end_time, gps_points)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                op_id,
                seg["hachi_no"],
                seg["catch"],
                seg["center_lat"],
                seg["center_lon"],
                seg["length_m"],
                seg["start_time"].isoformat(),
                seg["end_time"].isoformat(),
                gps_json,
            ],
        )

    return op_id


def list_operations() -> list[dict]:
    """保存済み操業一覧を返す（新しい順）"""
    init_db()
    result = _d1_query(
        "SELECT * FROM operations ORDER BY op_date DESC, start_time DESC"
    )
    return result["results"]


def load_operation(op_id: int) -> tuple[dict, list[dict]]:
    """指定IDの操業データとセグメントを返す。"""
    init_db()
    op_result = _d1_query("SELECT * FROM operations WHERE id=?", [op_id])
    op = op_result["results"][0]

    segs_result = _d1_query(
        "SELECT * FROM segments WHERE operation_id=? ORDER BY hachi_no",
        [op_id],
    )
    segs_rows = segs_result["results"]

    ocr_data = {
        "date":        op["op_date"],
        "location":    op["location"],
        "bait":        op["bait"],
        "start_time":  op["start_time"],
        "end_time":    op["end_time"],
        "total_hachi": op["total_hachi"],
        "catch_per_hachi": [],
        "ctd": {
            "surface_temp":     op["surface_temp"],
            "bottom_temp":      op["bottom_temp"],
            "surface_salinity": op["surface_salinity"],
            "bottom_salinity":  op["bottom_salinity"],
            "max_depth":        op["max_depth"],
        },
        "notes": op["notes"],
    }

    import pandas as pd
    segments = []
    for row in segs_rows:
        gps = json.loads(row["gps_points"])
        pts_df = pd.DataFrame(gps, columns=["lat", "lon"])
        pts_df["timestamp"] = pd.NaT

        segments.append({
            "hachi_no":   row["hachi_no"],
            "catch":      row["catch"],
            "center_lat": row["center_lat"],
            "center_lon": row["center_lon"],
            "length_m":   row["length_m"],
            "start_time": datetime.fromisoformat(row["start_time"]),
            "end_time":   datetime.fromisoformat(row["end_time"]),
            "points":     pts_df,
        })
        ocr_data["catch_per_hachi"].append(
            {"hachi": row["hachi_no"], "count": row["catch"]}
        )

    return ocr_data, segments


def load_all_segments() -> list[dict]:
    """
    全操業の全セグメントを操業情報付きで返す（重ね地図・集計分析用）。
    各行に op_date / location / bait / CTD環境データが結合される。
    """
    init_db()
    result = _d1_query("""
        SELECT
            s.operation_id, s.hachi_no, s.catch,
            s.center_lat, s.center_lon, s.length_m, s.gps_points,
            o.op_date, o.location, o.bait,
            o.surface_temp, o.bottom_temp,
            o.surface_salinity, o.bottom_salinity, o.max_depth,
            o.total_catch, o.total_hachi
        FROM segments s
        JOIN operations o ON s.operation_id = o.id
        ORDER BY o.op_date, s.hachi_no
    """)
    return result["results"]


def delete_operation(op_id: int):
    """指定IDの操業データを削除する"""
    init_db()
    _d1_query("DELETE FROM segments WHERE operation_id=?", [op_id])
    _d1_query("DELETE FROM operations WHERE id=?", [op_id])
