"""Database operations using Supabase REST API."""

import os
import requests
import logging

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # anon/service key


def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ── Users ──

def get_user(telegram_id: int) -> dict | None:
    r = requests.get(
        _url("users"),
        headers=_headers(),
        params={"telegram_id": f"eq.{telegram_id}", "select": "*"},
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def create_user(telegram_id: int) -> dict:
    r = requests.post(
        _url("users"),
        headers={**_headers(), "Prefer": "return=representation,resolution=merge-duplicates"},
        json={"telegram_id": telegram_id},
    )
    r.raise_for_status()
    return r.json()[0]


def update_user(telegram_id: int, **fields) -> dict:
    r = requests.patch(
        _url("users"),
        headers=_headers(),
        params={"telegram_id": f"eq.{telegram_id}"},
        json=fields,
    )
    r.raise_for_status()
    return r.json()[0]


# ── Flatmates ──

def get_flatmates(telegram_id: int) -> list:
    r = requests.get(
        _url("flatmates"),
        headers=_headers(),
        params={"user_telegram_id": f"eq.{telegram_id}", "select": "*"},
    )
    r.raise_for_status()
    return r.json()


def add_flatmate(telegram_id: int, name: str, splitwise_user_id: int) -> dict:
    r = requests.post(
        _url("flatmates"),
        headers={**_headers(), "Prefer": "return=representation,resolution=merge-duplicates"},
        json={
            "user_telegram_id": telegram_id,
            "name": name,
            "splitwise_user_id": splitwise_user_id,
        },
    )
    r.raise_for_status()
    return r.json()[0]


def clear_flatmates(telegram_id: int):
    r = requests.delete(
        _url("flatmates"),
        headers=_headers(),
        params={"user_telegram_id": f"eq.{telegram_id}"},
    )
    r.raise_for_status()
