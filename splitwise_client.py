"""Per-user Splitwise API client."""

import os
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://secure.splitwise.com/api/v3.0"
OAUTH_AUTHORIZE_URL = "https://secure.splitwise.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://secure.splitwise.com/oauth/token"

CONSUMER_KEY = os.environ.get("SPLITWISE_CONSUMER_KEY", "")
CONSUMER_SECRET = os.environ.get("SPLITWISE_CONSUMER_SECRET", "")


def get_auth_url(telegram_id: int, callback_url: str) -> str:
    """Generate Splitwise OAuth authorization URL."""
    return (
        f"{OAUTH_AUTHORIZE_URL}"
        f"?response_type=code"
        f"&client_id={CONSUMER_KEY}"
        f"&redirect_uri={callback_url}"
        f"&state={telegram_id}"
    )


def exchange_code_for_token(code: str, callback_url: str) -> dict:
    """Exchange OAuth authorization code for access token."""
    r = requests.post(OAUTH_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CONSUMER_KEY,
        "client_secret": CONSUMER_SECRET,
        "redirect_uri": callback_url,
    })
    r.raise_for_status()
    return r.json()


class SplitwiseClient:
    """Splitwise API client using a user's access token."""

    def __init__(self, access_token: str):
        self.token = access_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _get(self, endpoint, params=None):
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint, data):
        r = requests.post(f"{BASE_URL}/{endpoint}", headers=self.headers, data=data)
        r.raise_for_status()
        return r.json()

    def get_current_user(self) -> dict:
        return self._get("get_current_user")["user"]

    def get_friends(self) -> list:
        return self._get("get_friends")["friends"]

    def get_groups(self) -> list:
        """Get all groups the user is part of."""
        return self._get("get_groups")["groups"]

    def find_group_with_members(self, member_ids: list) -> list:
        """Find groups that contain ALL the specified member IDs (plus the current user)."""
        groups = self.get_groups()
        matching = []
        member_set = set(member_ids)

        for group in groups:
            if group.get("id") == 0:  # skip "non-group expenses"
                continue
            group_member_ids = {m["id"] for m in group.get("members", [])}
            if member_set.issubset(group_member_ids):
                matching.append({
                    "id": group["id"],
                    "name": group.get("name", "Unnamed Group"),
                    "member_count": len(group.get("members", [])),
                })

        return matching

    def find_friends_by_name(self, name: str) -> list:
        """Find friends whose name contains the search string."""
        friends = self.get_friends()
        name_lower = name.lower()
        return [
            f for f in friends
            if name_lower in f"{f.get('first_name') or ''} {f.get('last_name') or ''}".lower()
        ]

    def create_expense(
        self,
        description: str,
        total_cost: float,
        payer_id: int,
        shares: dict,  # {user_id: owed_amount}
        details: str = "",
        group_id: int = None,
    ) -> dict:
        """
        Create an expense.
        payer_id: who paid the full amount
        shares: {splitwise_user_id: amount_owed} for each person
        """
        cost_str = f"{total_cost:.2f}"

        data = {
            "cost": cost_str,
            "description": description,
            "details": details,
            "currency_code": "INR",
        }

        for i, (user_id, owed) in enumerate(shares.items()):
            paid = cost_str if user_id == payer_id else "0.00"
            data[f"users__{i}__user_id"] = user_id
            data[f"users__{i}__paid_share"] = paid
            data[f"users__{i}__owed_share"] = f"{owed:.2f}"

        if group_id:
            data["group_id"] = group_id

        result = self._post("create_expense", data)

        if "errors" in result and result["errors"]:
            raise ValueError(f"Splitwise error: {result['errors']}")

        return result


def build_expense_details(split: dict, user_name: str, flatmate_names: dict) -> str:
    """Build simple, readable notes for the Splitwise expense."""
    lines = []

    if split["personal_items"]:
        items = ", ".join(f"{i['name']} ₹{i['amount']:.0f}" for i in split["personal_items"])
        lines.append(f"{user_name}'s: {items}")

    for fm_id, items in split.get("flatmate_items", {}).items():
        if items:
            fm_name = flatmate_names.get(fm_id, "Flatmate")
            item_str = ", ".join(f"{i['name']} ₹{i['amount']:.0f}" for i in items)
            lines.append(f"{fm_name}'s: {item_str}")

    for gs in split.get("group_split_items", []):
        people = " & ".join(gs["people"])
        lines.append(f"{gs['item']['name']} ₹{gs['item']['amount']:.0f} split between {people} (₹{gs['per_person']:.0f} each)")

    if split["shared_items"]:
        items = ", ".join(f"{i['name']} ₹{i['amount']:.0f}" for i in split["shared_items"])
        lines.append(f"Shared: {items} (₹{split['shared_each']:.0f} each)")

    if split.get("extra_charges", 0) > 0:
        lines.append(f"Delivery/fees: ₹{split['extra_charges']:.0f}")

    lines.append("")
    lines.append(f"Total: ₹{split['order_total']:.0f}")
    lines.append(f"{user_name} paid the full bill")

    for fm_id, share in split["shares"].items():
        if fm_id != "user" and share > 0:
            fm_name = flatmate_names.get(fm_id, "Flatmate")
            lines.append(f"{fm_name} owes: ₹{share:.0f}")

    return "\n".join(lines)
