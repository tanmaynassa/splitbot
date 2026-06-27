"""Parse grocery invoice PDFs from multiple platforms."""

import re
import pdfplumber
import logging

logger = logging.getLogger(__name__)


def parse_invoice(pdf_path: str) -> dict:
    """
    Parse a grocery invoice PDF. Auto-detects platform.
    Returns dict with platform, order_date, order_no, items list, and total.
    """
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        all_tables = []
        for page in pdf.pages:
            all_tables.extend(page.extract_tables())

    # Detect platform
    text_lower = full_text.lower()
    if "zeptonow" in text_lower or "zepto" in text_lower:
        return _parse_zepto(all_tables, full_text)
    elif "blinkit" in text_lower or "grofers" in text_lower or "locodel" in text_lower:
        return _parse_blinkit(all_tables, full_text)
    else:
        # Generic fallback — try to extract any table with items and prices
        return _parse_generic(all_tables, full_text)


def _parse_zepto(all_tables: list, full_text: str) -> dict:
    """Parse Zepto invoice PDF."""
    if len(all_tables) < 2:
        raise ValueError("Could not find item table in Zepto invoice")

    items = _extract_items_from_tables(all_tables)

    # Extract date and order number
    order_date = ""
    order_no = ""

    date_match = re.search(r"Date\s*:\s*([\d\-]+)", full_text)
    if date_match:
        order_date = date_match.group(1)

    order_match = re.search(r"Order No\.?:\s*(\S+)", full_text)
    if order_match:
        order_no = order_match.group(1)

    return {
        "platform": "Zepto",
        "order_date": order_date,
        "order_no": order_no,
        "items": items,
        "total": sum(item["amount"] for item in items),
    }


def _parse_blinkit(all_tables: list, full_text: str) -> dict:
    """Parse Blinkit invoice PDF."""
    items = _extract_items_from_tables(all_tables)

    # Blinkit date/order extraction
    order_date = ""
    order_no = ""

    date_match = re.search(r"Date\s*[:\-]\s*([\d\-/]+)", full_text)
    if date_match:
        order_date = date_match.group(1)

    order_match = re.search(r"(?:Order|Invoice)\s*(?:No|ID|#)\.?\s*[:\-]?\s*(\S+)", full_text, re.IGNORECASE)
    if order_match:
        order_no = order_match.group(1)

    return {
        "platform": "Blinkit",
        "order_date": order_date,
        "order_no": order_no,
        "items": items,
        "total": sum(item["amount"] for item in items),
    }


def _parse_generic(all_tables: list, full_text: str) -> dict:
    """Generic fallback parser for unknown invoice formats."""
    items = _extract_items_from_tables(all_tables)

    order_date = ""
    date_match = re.search(r"Date\s*[:\-]\s*([\d\-/]+)", full_text)
    if date_match:
        order_date = date_match.group(1)

    order_no = ""
    order_match = re.search(r"(?:Order|Invoice)\s*(?:No|ID|#)\.?\s*[:\-]?\s*(\S+)", full_text, re.IGNORECASE)
    if order_match:
        order_no = order_match.group(1)

    return {
        "platform": "Unknown",
        "order_date": order_date,
        "order_no": order_no,
        "items": items,
        "total": sum(item["amount"] for item in items),
    }


def _extract_items_from_tables(all_tables: list) -> list:
    """Extract items from PDF tables — works across platforms."""
    items = []

    for table in all_tables:
        for row in table:
            # Find the SR No — could be at index 0 or 1 depending on page layout
            sr_no = None
            sr_idx = None
            for idx in range(min(3, len(row))):
                cell = (row[idx] or "").strip()
                if cell.isdigit():
                    sr_no = cell
                    sr_idx = idx
                    break

            if sr_no is None:
                continue

            # Item name is the next column after SR No
            name_idx = sr_idx + 1
            if name_idx >= len(row):
                continue

            item_name = (row[name_idx] or "").replace("\n", " ").strip()
            item_name = re.sub(r"\s+", " ", item_name)

            if not item_name:
                continue

            try:
                total_amt = float(row[-1])
            except (ValueError, TypeError):
                continue

            # Avoid duplicate SR numbers
            if not any(i["sr"] == int(sr_no) for i in items):
                items.append({
                    "sr": int(sr_no),
                    "name": item_name,
                    "amount": total_amt,
                })

    return items


def format_item_list(parsed: dict, flatmate_names: list) -> str:
    """Format parsed invoice into a numbered list for Telegram."""
    platform = parsed.get("platform", "")
    lines = [f"🛒 *{platform} Order — {parsed['order_date']}*"]
    lines.append(f"Total: ₹{parsed['total']:.2f}\n")

    for item in parsed["items"]:
        lines.append(f"`{item['sr']}.` {item['name']} — ₹{item['amount']:.2f}")

    lines.append("\n*Tag your items:*")
    lines.append("`mine: 1,2`")
    for name in flatmate_names:
        lines.append(f"`{name.lower()}: 3`")
    lines.append("`rest split among: me, name`  _(optional — default: everyone)_")
    lines.append("\nOr type `all` if everything splits equally.")

    return "\n".join(lines)


def compute_split(items: list, personal_indices: list, flatmate_tagged: dict, flatmate_ids: dict, num_splitters: int, split_among: dict = None) -> dict:
    """
    Compute the expense split.
    personal_indices: item SRs that are the user's only
    flatmate_tagged: {flatmate_name: [item SRs]} for each flatmate's personal items
    flatmate_ids: {flatmate_name: splitwise_user_id}
    num_splitters: total people sharing (for equal split of shared items)
    split_among: optional dict {"include_self": bool, "flatmate_ids": [ids]} — who shares the rest
    """
    personal_total = 0.0
    shared_total = 0.0

    personal_items = []
    shared_items = []
    flatmate_items = {}  # {splitwise_user_id: [items]}
    flatmate_totals = {}  # {splitwise_user_id: total}

    # Collect flatmate personal items
    all_flatmate_srs = set()
    for fm_name, srs in flatmate_tagged.items():
        fm_id = flatmate_ids[fm_name]
        flatmate_items[fm_id] = []
        flatmate_totals[fm_id] = 0.0
        all_flatmate_srs.update(srs)

    # Initialize all flatmate totals
    for fm_name, fm_id in flatmate_ids.items():
        if fm_id not in flatmate_items:
            flatmate_items[fm_id] = []
            flatmate_totals[fm_id] = 0.0

    for item in items:
        if item["sr"] in personal_indices:
            personal_total += item["amount"]
            personal_items.append(item)
        elif item["sr"] in all_flatmate_srs:
            for fm_name, srs in flatmate_tagged.items():
                if item["sr"] in srs:
                    fm_id = flatmate_ids[fm_name]
                    flatmate_items[fm_id].append(item)
                    flatmate_totals[fm_id] += item["amount"]
                    break
        else:
            shared_total += item["amount"]
            shared_items.append(item)

    # Determine who splits the shared items
    if split_among:
        actual_splitters = len(split_among["flatmate_ids"])
        if split_among["include_self"]:
            actual_splitters += 1
        shared_each = round(shared_total / actual_splitters, 2) if actual_splitters > 0 else 0
    else:
        actual_splitters = num_splitters
        shared_each = round(shared_total / num_splitters, 2) if num_splitters > 0 else 0

    # Calculate shares
    shares = {}

    # User's share
    if split_among is None or split_among["include_self"]:
        shares["user"] = round(personal_total + shared_each, 2)
    else:
        shares["user"] = round(personal_total, 2)

    # Flatmate shares
    for fm_name, fm_id in flatmate_ids.items():
        personal_fm = flatmate_totals.get(fm_id, 0)
        if split_among is None:
            # Everyone splits shared
            shares[fm_id] = round(personal_fm + shared_each, 2)
        elif fm_id in split_among["flatmate_ids"]:
            # This flatmate is in the split group
            shares[fm_id] = round(personal_fm + shared_each, 2)
        else:
            # This flatmate only pays personal items
            shares[fm_id] = round(personal_fm, 2)

    order_total = round(personal_total + shared_total + sum(flatmate_totals.values()), 2)

    return {
        "personal_items": personal_items,
        "shared_items": shared_items,
        "flatmate_items": flatmate_items,
        "personal_total": personal_total,
        "shared_total": shared_total,
        "shared_each": shared_each,
        "order_total": order_total,
        "shares": shares,
    }


def format_split_summary(split: dict, order_date: str, user_name: str, flatmate_names: dict) -> str:
    """Format the split result into a confirmation message.
    flatmate_names: {splitwise_user_id: display_name}
    """
    lines = [f"📊 *Split Summary — {order_date}*\n"]

    if split["personal_items"]:
        names = ", ".join(i["name"] for i in split["personal_items"])
        lines.append(f"🔹 *Your items:* ₹{split['personal_total']:.2f}")
        lines.append(f"   _{names}_\n")

    for fm_id, items in split["flatmate_items"].items():
        if items:
            fm_name = flatmate_names.get(fm_id, "Flatmate")
            subtotal = sum(i["amount"] for i in items)
            names = ", ".join(i["name"] for i in items)
            lines.append(f"🔸 *{fm_name}'s items:* ₹{subtotal:.2f}")
            lines.append(f"   _{names}_\n")

    if split["shared_items"]:
        names = ", ".join(i["name"] for i in split["shared_items"])
        lines.append(f"🔀 *Shared (equal split):* ₹{split['shared_total']:.2f} → ₹{split['shared_each']:.2f} each")
        lines.append(f"   _{names}_\n")

    lines.append(f"💰 *You paid:* ₹{split['order_total']:.2f}")

    for fm_id, share in split["shares"].items():
        if fm_id != "user" and share > 0:
            fm_name = flatmate_names.get(fm_id, "Flatmate")
            lines.append(f"📌 *{fm_name} owes you:* ₹{share:.2f}")

    lines.append(f"\nType `ok` to confirm or `cancel` to discard.")

    return "\n".join(lines)
