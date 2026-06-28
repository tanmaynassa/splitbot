"""Parse grocery invoice PDFs from multiple platforms."""

import re
import pdfplumber
import logging

logger = logging.getLogger(__name__)


def parse_invoice(pdf_path: str) -> dict:
    """
    Parse a grocery invoice PDF. Auto-detects platform.
    Returns dict with platform, order_date, order_no, items, total, extra_charges.
    """
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        all_tables = []
        for page in pdf.pages:
            all_tables.extend(page.extract_tables())

    # Detect platform
    text_lower = full_text.lower()
    if "zeptonow" in text_lower or "zepto" in text_lower:
        return _parse_invoice(all_tables, full_text, "Zepto")
    elif "blinkit" in text_lower or "grofers" in text_lower or "locodel" in text_lower:
        return _parse_invoice(all_tables, full_text, "Blinkit")
    elif "swiggy" in text_lower or "instamart" in text_lower or "handling fee" in text_lower:
        return _parse_invoice(all_tables, full_text, "Swiggy Instamart")
    else:
        return _parse_invoice(all_tables, full_text, "Unknown")


def _parse_invoice(all_tables: list, full_text: str, platform: str) -> dict:
    """Common parser for all platforms."""
    items = _extract_items_from_tables(all_tables)
    items_total = sum(item["amount"] for item in items)

    # Extract date
    order_date = ""
    date_match = re.search(r"Date\s*(?:of\s*Invoice)?\s*[:\-]\s*([\d\-/]+)", full_text)
    if date_match:
        order_date = date_match.group(1)

    # Extract order number
    order_no = ""
    order_match = re.search(r"(?:Order|Invoice)\s*(?:No|ID|#)\.?\s*[:\-]?\s*(\S+)", full_text, re.IGNORECASE)
    if order_match:
        order_no = order_match.group(1)

    # Extract invoice total (includes delivery charges, rounding etc.)
    invoice_total = _extract_invoice_total(full_text)
    extra_charges = round(invoice_total - items_total, 2) if invoice_total > items_total else 0

    return {
        "platform": platform,
        "order_date": order_date,
        "order_no": order_no,
        "items": items,
        "items_total": items_total,
        "extra_charges": extra_charges,
        "total": invoice_total if invoice_total > 0 else items_total,
    }


def _extract_invoice_total(full_text: str) -> float:
    """Extract the final invoice total from the PDF text."""
    invoice_value = 0.0
    handling_fee = 0.0

    # Get base invoice value
    for pattern in [
        r"Invoice\s*Value\s*[\s:₹]*(\d[\d,.]+)",
        r"Grand\s*Total\s*[\s:₹]*(\d[\d,.]+)",
        r"Net\s*(?:Payable|Amount)\s*[\s:₹]*(\d[\d,.]+)",
        r"Total\s*(?:Amount|Payable)\s*[\s:₹]*(\d[\d,.]+)",
        r"Amount\s*Paid\s*[\s:₹]*(\d[\d,.]+)",
    ]:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            val = match.group(1).replace(",", "")
            try:
                invoice_value = float(val)
                break
            except ValueError:
                continue

    # Check for handling/delivery fee
    fee_match = re.search(
        r"(?:Handling|Delivery|Platform)\s*(?:Fee|Charge|Charges)\s*[^₹\d]*[\s₹]*(\d[\d,.]+)",
        full_text, re.IGNORECASE,
    )
    if fee_match:
        try:
            handling_fee = float(fee_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Also look for "Amount in words" as the final total
    words_match = re.search(r"Amount\s*in\s*words\s*:\s*(.+?)(?:Rupees|Only)", full_text, re.IGNORECASE)
    if words_match and invoice_value > 0 and handling_fee > 0:
        # Use invoice_value + handling_fee as the total
        return invoice_value + handling_fee

    return invoice_value + handling_fee if invoice_value > 0 else 0.0


def _extract_items_from_tables(all_tables: list) -> list:
    """Extract items from PDF tables — works across platforms."""
    items = []

    for table in all_tables:
        for row in table:
            sr_no = None
            sr_idx = None
            for idx in range(min(3, len(row))):
                cell = (row[idx] or "").strip().rstrip(".")
                if cell.isdigit() and int(cell) > 0:
                    sr_no = cell
                    sr_idx = idx
                    break

            if sr_no is None:
                continue

            name_idx = sr_idx + 1
            if name_idx >= len(row):
                continue

            item_name = (row[name_idx] or "").replace("\n", " ").strip()
            item_name = re.sub(r"\s+", " ", item_name)

            if not item_name or item_name.upper() == "NOS":
                continue

            try:
                total_amt = float(row[-1])
            except (ValueError, TypeError):
                continue

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
    lines.append(f"Total: ₹{parsed['total']:.2f}")

    if parsed.get("extra_charges", 0) > 0:
        lines.append(f"_(includes ₹{parsed['extra_charges']:.2f} delivery/fees)_")

    lines.append("")

    for item in parsed["items"]:
        lines.append(f"`{item['sr']}.` {item['name']} — ₹{item['amount']:.2f}")

    lines.append("\n*Tag your items:*")
    lines.append("`mine: 1,2`")
    for name in flatmate_names:
        lines.append(f"`{name.lower()}: 3`")
    lines.append("`rest split among: me, name`  _(optional — default: everyone)_")
    lines.append("\nOr type `all` if everything splits equally.")

    return "\n".join(lines)


def compute_split(items: list, personal_indices: list, flatmate_tagged: dict, flatmate_ids: dict, num_splitters: int, split_among: dict = None, extra_charges: float = 0) -> dict:
    """
    Compute the expense split.
    extra_charges: delivery fees etc. — split among the sharing group.
    """
    personal_total = 0.0
    shared_total = 0.0

    personal_items = []
    shared_items = []
    flatmate_items = {}
    flatmate_totals = {}

    all_flatmate_srs = set()
    for fm_name, srs in flatmate_tagged.items():
        fm_id = flatmate_ids[fm_name]
        flatmate_items[fm_id] = []
        flatmate_totals[fm_id] = 0.0
        all_flatmate_srs.update(srs)

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

    # Determine who splits the shared items + delivery charges
    if split_among:
        actual_splitters = len(split_among["flatmate_ids"])
        if split_among["include_self"]:
            actual_splitters += 1
    else:
        actual_splitters = num_splitters

    # Add delivery charges to shared pool
    shared_pool = shared_total + extra_charges
    shared_each = round(shared_pool / actual_splitters, 2) if actual_splitters > 0 else 0

    # Calculate shares
    shares = {}

    if split_among is None or split_among["include_self"]:
        shares["user"] = round(personal_total + shared_each, 2)
    else:
        shares["user"] = round(personal_total, 2)

    for fm_name, fm_id in flatmate_ids.items():
        personal_fm = flatmate_totals.get(fm_id, 0)
        if split_among is None:
            shares[fm_id] = round(personal_fm + shared_each, 2)
        elif fm_id in split_among["flatmate_ids"]:
            shares[fm_id] = round(personal_fm + shared_each, 2)
        else:
            shares[fm_id] = round(personal_fm, 2)

    order_total = round(personal_total + shared_total + extra_charges + sum(flatmate_totals.values()), 2)

    return {
        "personal_items": personal_items,
        "shared_items": shared_items,
        "flatmate_items": flatmate_items,
        "personal_total": personal_total,
        "shared_total": shared_total,
        "extra_charges": extra_charges,
        "shared_each": shared_each,
        "order_total": order_total,
        "shares": shares,
    }


def format_split_summary(split: dict, order_date: str, user_name: str, flatmate_names: dict) -> str:
    """Format the split result into a confirmation message."""
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

    if split.get("extra_charges", 0) > 0:
        lines.append(f"🚚 *Delivery/fees:* ₹{split['extra_charges']:.2f} _(split equally)_\n")

    lines.append(f"💰 *You paid:* ₹{split['order_total']:.2f}")

    for fm_id, share in split["shares"].items():
        if fm_id != "user" and share > 0:
            fm_name = flatmate_names.get(fm_id, "Flatmate")
            lines.append(f"📌 *{fm_name} owes you:* ₹{share:.2f}")

    return "\n".join(lines)
