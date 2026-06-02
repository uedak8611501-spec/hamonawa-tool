"""
操業データ SQLite 永続化モジュール
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "operations.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """テーブルが存在しない場合に作成する"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS operations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            op_date     TEXT NOT NULL,          -- 操業日 YYYY-MM-DD
            location    TEXT,                   -- 操業場所
            bait        TEXT,                   -- エサ
            start_time  TEXT,                   -- 投入開始 HH:MM
            end_time    TEXT,                   -- 投入終了 HH:MM
            total_hachi INTEGER,                -- 総鉢数
            total_catch INTEGER,                -- 総釣果
            surface_temp    REAL,
            bottom_temp     REAL,
            surface_salinity REAL,
            bottom_salinity  REAL,
            max_depth        REAL,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
            hachi_no    INTEGER NOT NULL,
            catch       INTEGER DEFAULT 0,
            center_lat  REAL,
            center_lon  REAL,
            length_m    REAL,
            start_time  TEXT,
            end_time    TEXT,
            gps_points  TEXT    -- JSON: [[lat,lon], ...]
        );
        """)


def save_operation(ocr_data: dict, segments: list[dict]) -> int:
    """
    操業データと分割セグメントをDBに保存する。
    Returns: 新規レコードのID
    """
    init_db()
    ctd = ocr_data.get("ctd") or {}
    total_catch = sum(x["count"] for x in ocr_data.get("catch_per_hachi", []))

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO operations
              (op_date, location, bait, start_time, end_time, total_hachi, total_catch,
               surface_temp, bottom_temp, surface_salinity, bottom_salinity,
               max_depth, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
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
            ),
        )
        op_id = cur.lastrowid

        for seg in segments:
            pts = seg["points"]
            gps_json = json.dumps(
                list(zip(pts["lat"].tolist(), pts["lon"].tolist()))
            )
            conn.execute(
                """
                INSERT INTO segments
                  (operation_id, hachi_no, catch, center_lat, center_lon,
                   length_m, start_time, end_time, gps_points)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    op_id,
                    seg["hachi_no"],
                    seg["catch"],
                    seg["center_lat"],
                    seg["center_lon"],
                    seg["length_m"],
                    seg["start_time"].isoformat(),
                    seg["end_time"].isoformat(),
                    gps_json,
                ),
            )

    return op_id


def list_operations() -> list[dict]:
    """保存済み操業一覧を返す（新しい順）"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM operations ORDER BY op_date DESC, start_time DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_operation(op_id: int) -> tuple[dict, list[dict]]:
    """
    指定IDの操業データとセグメントを返す。
    Returns: (ocr_data形式のdict, segments形式のlist)
    """
    init_db()
    with get_conn() as conn:
        op = dict(conn.execute(
            "SELECT * FROM operations WHERE id=?", (op_id,)
        ).fetchone())

        segs_rows = conn.execute(
            "SELECT * FROM segments WHERE operation_id=? ORDER BY hachi_no",
            (op_id,)
        ).fetchall()

    # ocr_data形式に変換
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

    # segments形式に変換
    import pandas as pd
    segments = []
    for row in segs_rows:
        row = dict(row)
        gps = json.loads(row["gps_points"])
        pts_df = pd.DataFrame(gps, columns=["lat", "lon"])
        pts_df["timestamp"] = pd.NaT  # 表示用なので空で可

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


def delete_operation(op_id: int):
    """指定IDの操業データを削除する"""
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM operations WHERE id=?", (op_id,))
