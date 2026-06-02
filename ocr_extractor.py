"""
操業メモ写真からClaude Vision APIで構造化データを抽出するモジュール
"""
import anthropic
import base64
import json
from pathlib import Path


EXTRACTION_PROMPT = """
あなたは漁師の手書きメモを読み取るOCRアシスタントです。
画像に写っている延縄（ハモ）操業メモから、以下の項目をすべて抽出し、
必ずJSON形式で返してください。存在しない項目はnullにしてください。

抽出項目:
{
  "date": "YYYY-MM-DD形式の日付",
  "bait": "エサの種類",
  "start_time": "HH:MM形式の投入開始時刻",
  "end_time": "HH:MM形式の投入終了時刻",
  "total_hachi": 総鉢数（整数）,
  "catch_per_hachi": [
    {"hachi": 1, "count": 12},
    {"hachi": 2, "count": 5},
    ...各鉢の釣果リスト...
  ],
  "ctd": {
    "surface_temp": 表層水温（数値℃）,
    "bottom_temp": 底水温（数値℃）,
    "surface_salinity": 表層塩分（数値psu）,
    "bottom_salinity": 底層塩分（数値psu）,
    "max_depth": 実測最大水深（数値m）
  },
  "notes": "その他メモがあれば文字列"
}

JSONのみ返してください。コードブロックや説明文は不要です。
"""


def extract_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """
    画像バイトデータをClaude Vision APIに送り、操業メモを構造化データとして返す。

    Returns:
        dict: 抽出されたデータ。解析失敗時はerrorキーを含む。
    """
    client = anthropic.Anthropic()

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw_text = message.content[0].text.strip()

    # コードブロックが混入した場合でも対応
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1])

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        return {"error": f"JSON解析失敗: {e}", "raw": raw_text}


def validate_and_fill_defaults(data: dict) -> dict:
    """抽出データのバリデーションとデフォルト値補完"""
    if "error" in data:
        return data

    # CTDが未抽出の場合は空のdictを用意
    if "ctd" not in data or data["ctd"] is None:
        data["ctd"] = {
            "surface_temp": None,
            "bottom_temp": None,
            "surface_salinity": None,
            "bottom_salinity": None,
            "max_depth": None,
        }

    if "catch_per_hachi" not in data or data["catch_per_hachi"] is None:
        data["catch_per_hachi"] = []

    return data
