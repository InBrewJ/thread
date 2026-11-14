import os
import json
import logging
from datetime import datetime
from calendar import monthrange
import requests

logger = logging.getLogger(__name__)

MONMON_URL = os.environ.get("MONMON_URL", "http://127.0.0.1:9000")


def fetch_monmon_transactions(user_id=None, token=None):
    """
    Fetch transactions from MonMon's REST API using RAuthy Bearer token or user ID.
    """
    uid = user_id or "inbrewj"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Try /api/dig/data first (returns up to 2000 categorized transactions with dates)
    dig_url = f"{MONMON_URL.rstrip('/')}/api/dig/data?user_id={uid}"
    try:
        resp = requests.get(dig_url, headers=headers, timeout=4)
        if resp.status_code == 200:
            data = resp.json()
            txs = data.get("transactions", [])
            if txs:
                return txs
    except Exception as e:
        logger.warning(f"Error fetching from MonMon dig API ({dig_url}): {e}")

    # Fallback to /bank/transactions
    bank_url = f"{MONMON_URL.rstrip('/')}/bank/transactions?user_id={uid}"
    try:
        resp = requests.get(bank_url, headers=headers, timeout=4)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data
    except Exception as e:
        logger.warning(f"Error fetching from MonMon bank transactions API ({bank_url}): {e}")

    return []


def fetch_earliest_monmon_date(user_id=None, token=None):
    """
    Finds the earliest available recorded entry across MonMon (balances & transactions).
    """
    candidates = []
    uids_to_try = []
    if user_id:
        uids_to_try.append(user_id)
    if "2ccc203f-a98c-488f-a5ba-8d20a384a9fc" not in uids_to_try:
        uids_to_try.append("2ccc203f-a98c-488f-a5ba-8d20a384a9fc")

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for uid in uids_to_try:
        url = f"{MONMON_URL.rstrip('/')}/internal/balances?userId={uid}"
        try:
            resp = requests.get(url, headers=headers, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    ts_list = [b["timestamp"] for b in data if isinstance(b.get("timestamp"), (int, float))]
                    if ts_list:
                        candidates.append(min(ts_list))
                        break
        except Exception as e:
            logger.warning(f"Error fetching balances for earliest check ({url}): {e}")

    if not candidates:
        return "2021-03-26"

    min_ts = min(candidates)
    if min_ts > 1e11:
        min_ts = min_ts / 1000.0

    try:
        dt = datetime.fromtimestamp(min_ts)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "2021-03-26"


def calculate_age_from_month(birthday_dt, year, month):
    """
    Calculate the exact fractional age corresponding to the 15th of the given year/month.
    """
    mid_month_dt = datetime(year, month, 15)
    days = (mid_month_dt - birthday_dt).days
    return round(days / 365.25, 2)


def compute_spending_correlation(birthday_str, slides, user_id=None, token=None):
    """
    Aggregates MonMon spending into monthly windows, computes 6-month rolling baseline,
    detects surges, and correlates with Thread mood and chaotic zones.
    """
    try:
        birthday_dt = datetime.strptime(birthday_str, "%Y-%m-%d")
    except Exception:
        birthday_dt = datetime(2000, 1, 1)

    earliest_date_str = fetch_earliest_monmon_date(user_id=user_id, token=token) or "2021-03-26"
    earliest_age = 28.79
    earliest_label = "MonMon Starts (Mar 2021)"
    if earliest_date_str:
        try:
            earliest_dt = datetime.strptime(earliest_date_str, "%Y-%m-%d")
            days = (earliest_dt - birthday_dt).days
            earliest_age = round(days / 365.2425, 2)
            earliest_label = f"MonMon Starts ({earliest_dt.strftime('%b %Y')})"
        except Exception:
            earliest_age = 28.79

    earliest_info = {
        "date": earliest_date_str,
        "age": earliest_age,
        "label": earliest_label,
    }

    raw_txs = fetch_monmon_transactions(user_id=user_id, token=token)

    # If MonMon is offline or has no data, return graceful mock/fallback structure
    if not raw_txs:
        return {
            "available": True,
            "earliest_monmon": earliest_info,
            "error": "No recent bank transactions returned from MonMon REST service.",
            "months": [],
            "summary": {
                "total_months": 0,
                "surges_count": 0,
                "chaotic_overlap_count": 0,
            },
        }

    # 1. Aggregate spend by calendar month (YYYY-MM)
    monthly_stats = {}
    for t in raw_txs:
        amt = float(t.get("amount") or 0.0)
        cat = (t.get("category") or "general").lower()
        if amt <= 0 or "transfer" in cat:
            continue

        date_str = t.get("date") or ""
        if len(date_str) < 7:
            continue
        m_key = date_str[:7]  # YYYY-MM

        if m_key not in monthly_stats:
            monthly_stats[m_key] = {
                "total_spend": 0.0,
                "tx_count": 0,
                "categories": {},
            }

        monthly_stats[m_key]["total_spend"] += amt
        monthly_stats[m_key]["tx_count"] += 1
        monthly_stats[m_key]["categories"][cat] = (
            monthly_stats[m_key]["categories"].get(cat, 0.0) + amt
        )

    if not monthly_stats:
        return {
            "available": True,
            "months": [],
            "summary": {"total_months": 0, "surges_count": 0, "chaotic_overlap_count": 0},
        }

    sorted_months = sorted(monthly_stats.keys())

    # Map slides by age for quick interpolation
    slide_points = []
    for s in slides:
        try:
            slide_points.append({"age": float(s["age"]), "mood": float(s["mood"])})
        except (ValueError, KeyError):
            continue
    slide_points.sort(key=lambda p: p["age"])

    def get_interpolated_mood(target_age):
        if not slide_points:
            return 5.0
        if target_age <= slide_points[0]["age"]:
            return slide_points[0]["mood"]
        if target_age >= slide_points[-1]["age"]:
            return slide_points[-1]["mood"]

        for i in range(len(slide_points) - 1):
            p1 = slide_points[i]
            p2 = slide_points[i + 1]
            if p1["age"] <= target_age <= p2["age"]:
                span = p2["age"] - p1["age"]
                if span <= 0:
                    return p1["mood"]
                factor = (target_age - p1["age"]) / span
                return round(p1["mood"] + factor * (p2["mood"] - p1["mood"]), 1)
        return 5.0

    # 2. Compute 6-month trailing rolling baseline & surge detection
    month_results = []
    surges_count = 0
    chaotic_overlaps = 0

    for idx, m_key in enumerate(sorted_months):
        year, month = map(int, m_key.split("-"))
        total = round(monthly_stats[m_key]["total_spend"], 2)
        tx_count = monthly_stats[m_key]["tx_count"]

        # Trailing 6-month window (or available preceding months)
        start_idx = max(0, idx - 6)
        preceding_keys = sorted_months[start_idx:idx]
        if preceding_keys:
            baseline = sum(monthly_stats[k]["total_spend"] for k in preceding_keys) / len(preceding_keys)
        else:
            baseline = total  # Baseline is self for the first recorded month

        baseline = round(baseline, 2)
        ratio = round(total / baseline, 2) if baseline > 0 else 1.0

        # Surge criteria: spending is at least 30% higher than the 6-month baseline
        is_surge = bool(ratio >= 1.30 and total >= 150.0)
        if is_surge:
            surges_count += 1

        age = calculate_age_from_month(birthday_dt, year, month)
        mood = get_interpolated_mood(age)

        # Elevated or turbulent mood indicator (mood >= 7.0 or significant mood swing)
        is_elevated_mood = mood >= 7.0
        is_low_mood = mood <= 2.5
        correlates_chaotic = bool(is_surge and (is_elevated_mood or is_low_mood))
        if correlates_chaotic:
            chaotic_overlaps += 1

        # Top category by expenditure
        cats = monthly_stats[m_key]["categories"]
        top_cat = max(cats.items(), key=lambda c: c[1])[0] if cats else "general"

        month_results.append({
            "month": m_key,
            "age": age,
            "total_spend": total,
            "tx_count": tx_count,
            "baseline_6mo": baseline,
            "surge_ratio": ratio,
            "is_surge": is_surge,
            "mood": mood,
            "is_elevated_mood": is_elevated_mood,
            "correlates_chaotic": correlates_chaotic,
            "top_category": top_cat,
        })

    max_surge = max((m["surge_ratio"] for m in month_results), default=1.0)

    return {
        "available": True,
        "source": "MonMon REST API",
        "birthday": birthday_str,
        "earliest_monmon": earliest_info,
        "months": month_results,
        "summary": {
            "total_months": len(month_results),
            "surges_count": surges_count,
            "chaotic_overlap_count": chaotic_overlaps,
            "max_surge_ratio": max_surge,
            "insight": (
                f"{surges_count} spending surge(s) identified over {len(month_results)} month window; "
                f"{chaotic_overlaps} coincided with elevated or turbulent mood phases."
            )
        },
    }
