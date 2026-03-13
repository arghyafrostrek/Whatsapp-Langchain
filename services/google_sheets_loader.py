"""
Google Sheets data loader.

Fetches all rows from a Google Sheet and returns them as a list of dicts.
Uses gspread with a service account.
"""

import os
import gspread
from google.oauth2.service_account import Credentials

from logger_config import logger

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def load_sheet_data(sheet_name: str = "") -> list[dict]:
    """
    Load all rows from a Google Sheet.

    Args:
        sheet_name: Name of the Google Sheet to open.
                    Falls back to env var GOOGLE_SHEET_NAME.

    Returns:
        List of dicts (one per row, keys = header row).
    """
    sheet_name = sheet_name or os.getenv("GOOGLE_SHEET_NAME", "")
    if not sheet_name:
        logger.warning("google_sheets_loader: no sheet name configured")
        return []

    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_file:
        logger.warning("google_sheets_loader: GOOGLE_APPLICATION_CREDENTIALS not set")
        return []

    try:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).sheet1
        data = sheet.get_all_records()
        logger.info("google_sheets_loader: loaded %d rows from '%s'", len(data), sheet_name)
        return data
    except Exception as e:
        logger.error("google_sheets_loader: failed to load sheet '%s': %s", sheet_name, e)
        return []


def rows_to_text_chunks(rows: list[dict]) -> list[str]:
    """
    Convert each row dict into a single text chunk.
    Combines all column values into a readable string.
    """
    chunks: list[str] = []
    for row in rows:
        parts = []
        for key, value in row.items():
            val = str(value).strip()
            if val:
                parts.append(f"{key}: {val}")
        if parts:
            chunks.append("\n".join(parts))
    return chunks
