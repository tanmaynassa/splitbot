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

    def find_friends_by_name(self, name: str) -> list:
        """Find friends whose name contains the search string."""
        friends = self.get_friends()
        name_lower = name.lower()
        return [
            f for f in friends
            if name_lower in f"{f.get('first_name', '')} {f.get('last_name', '')}".lower()
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
    """Build itemized notes for the Splitwise expense.
    flatmate_names: {splitwise_user_id: display_name}
    """
    lines = ["— Order Breakdown —", ""]

    if split["personal_items"]:
        lines.append(f"{user_name}'s items:")
        for item in split["personal_items"]:
            lines.append(f"  • {item['name']} — ₹{item['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['personal_total']:.2f}")
        lines.append("")

    for fm_id, items in split.get("flatmate_items", {}).items():
        fm_name = flatmate_names.get(fm_id, "Flatmate")
        lines.append(f"{fm_name}'s items:")
        for item in items:
            lines.append(f"  • {item['name']} — ₹{item['amount']:.2f}")
        subtotal = sum(i["amount"] for i in items)
        lines.append(f"  Subtotal: ₹{subtotal:.2f}")
        lines.append("")

    if split["shared_items"]:
        lines.append("Shared items (split equally):")
        for item in split["shared_items"]:
            lines.append(f"  • {item['name']} — ₹{item['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['shared_total']:.2f}")
        lines.append("")

    lines.append("— Split —")
    lines.append(f"{user_name} paid: ₹{split['order_total']:.2f}")
    for person, share in split["shares"].items():
        name = user_name if person == "user" else flatmate_names.get(person, "Flatmate")
        lines.append(f"{name}'s share: ₹{share:.2f}")

    return "\n".join(lines)
