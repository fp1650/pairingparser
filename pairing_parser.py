#!/usr/bin/env python3
import re
import json
import sys
import os
import math
from datetime import datetime, timedelta

# ---------------- Constants & precompiled regex ----------------
YEAR = datetime.now().year
MONTH_MAP = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}

TRIP_SPLIT = re.compile(r'(?=\bTRIP\s*#)', re.I)
TRIP_HEAD = re.compile(r"TRIP\s*#\s*(\S+)\s+(\S+).*?\b([A-Z]{3})\b", re.I)
TRIP_HEAD_NOBASE = re.compile(r"TRIP\s*#\s*(\S+)\s+(\S+)", re.I)

TAFB_RE = re.compile(r"TAFB:\s*([\d+h\(\)\w:]+)", re.I)
CREDIT_RE = re.compile(r"Credit Time:\s*([^\s,]+)", re.I)
PERDIEM_RE = re.compile(r"PERDIEM:\s*([\d\.,]+)", re.I)

# --- CORRECTED LEG_PATTERN ---
# This pattern correctly captures the full flight number (with or without
# prefixes like "DH_" or "AC") as group(2).
LEG_PATTERN = re.compile(
    r"\b(\d{1,2})\s+((?:[A-Z]{2}_?)?[0-9]{2,5})\s+([A-Z]{3})\s+([A-Z]{3})\s+(\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([0-9]{1,3}h[0-9]{2}|[0-9]{1,2}:\d{2})",
    re.I,
)
# --- END CORRECTED ---

LAYOVER_MARKER = re.compile(r"----\s+([A-Z]{3})\b", re.I)
LAYOVER_CUE = re.compile(r"\b(hotel|overnight|layover)\b", re.I)
LAYOVER_DUR = re.compile(r"(\d{1,3}h\d{2})", re.I)

EFFECTIVE_NEIGHBOR = re.compile(r'([A-Z]{3})\s+(\d{1,2})\s*-\s*([A-Z]{3})\s+(\d{1,2})', re.I)
BRACKET_MASK = re.compile(r'\[([^\]]+)\]')
BASE_MASK = re.compile(r'\b[A-Z]{3}:\s*([0-9_]{1,7})')
NEAR_MASK = re.compile(r'([0-9_]{1,7})\s+effective', re.I)
EXCEPTIONS_RE = re.compile(r'except\s+(.*)', re.I)
MO_DAY = re.compile(r'([A-Z]{3})\s+(\d{1,2})', re.I)
TIME_PAIR = re.compile(r'(\d{2}):(\d{2})')

# ---------------- Helper functions ----------------

def time_str_to_minutes(time_str):
    if not time_str or not isinstance(time_str, str):
        raise ValueError("Invalid time string input")
    # Remove parenthesized content, e.g., (D) or (L)
    s = re.sub(r'\([^)]*\)', '', time_str).strip()
    m = re.match(r'(\d+)\s*h\s*(\d{1,2})?', s, re.I)
    if m:
        hours = int(m.group(1)); minutes = int(m.group(2) or 0)
        return hours * 60 + minutes
    if re.match(r'^\d{1,3}h\d{2}$', s, re.I):
        h, m = s.lower().split('h'); return int(h)*60 + int(m)
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]) * 60 + int(parts[1])
    if s.isdigit():
        return int(s)
    raise ValueError(f"Could not parse time string: {time_str}")


def minutes_to_time_str(total_minutes):
    try:
        h = int(total_minutes // 60); m = int(total_minutes % 60)
        return f"{h}h{m:02d}"
    except Exception:
        return "N/A"


def _parse_bracket_mask(mask):
    nums = re.findall(r'[01]', mask)
    if len(nums) < 7:
        return None
    # Assuming mask is 1-7 for Mon-Sun
    weekdays = {(i % 7) for i, ch in enumerate(nums[:7]) if ch == '1'}
    return weekdays or None


def _parse_underscore_digit_mask(mask):
    mask = (mask or '').strip()[:7].ljust(7, '_')
    # Assuming mask is 1-7 for Mon-Sun, where 7 is Sunday
    weekdays = {(i % 7) for i, ch in enumerate(mask) if ch != '_'}
    return weekdays or None


def parse_operating_dates(block):
    eff_idx = block.lower().find('effective')
    if eff_idx == -1:
        return []
    window = block[max(0, eff_idx - 200): eff_idx + 200]

    m = EFFECTIVE_NEIGHBOR.search(window)
    if not m:
        return []
    start_mon, start_day, end_mon, end_day = m.group(1).upper(), m.group(2), m.group(3).upper(), m.group(4)
    try:
        start_date = datetime(YEAR, MONTH_MAP[start_mon], int(start_day)).date()
        end_date = datetime(YEAR, MONTH_MAP[end_mon], int(end_day)).date()
        if end_date < start_date: # Handle year wrap-around
            end_date = datetime(YEAR + 1, MONTH_MAP[end_mon], int(end_day)).date()
    except Exception:
        return []

    mask_weekdays = None
    bracket = BRACKET_MASK.search(window)
    if bracket:
        mask_weekdays = _parse_bracket_mask(bracket.group(1))

    if mask_weekdays is None:
        mbase = BASE_MASK.search(window)
        if mbase:
            mask_weekdays = _parse_underscore_digit_mask(mbase.group(1))

    if mask_weekdays is None:
        mnear = NEAR_MASK.search(window)
        if mnear:
            mask_weekdays = _parse_underscore_digit_mask(mnear.group(1))

    if mask_weekdays is None:
        mask_weekdays = set(range(7)) # Default to all days

    exceptions = set()
    ex_match = EXCEPTIONS_RE.search(window)
    if ex_match:
        ex_str = ex_match.group(1)
        for mo, d in MO_DAY.findall(ex_str):
            try:
                # Try current year
                ex_date = datetime(YEAR, MONTH_MAP[mo.upper()], int(d)).date()
                if start_date <= ex_date <= end_date:
                    exceptions.add(ex_date)
                # Try next year (for date range wrap)
                ex_date_next = datetime(YEAR + 1, MONTH_MAP[mo.upper()], int(d)).date()
                if start_date <= ex_date_next <= end_date:
                    exceptions.add(ex_date_next)
            except Exception:
                continue

    res = []
    cur = start_date
    while cur <= end_date:
        # datetime.weekday() is Mon=0, Sun=6
        if cur.weekday() in mask_weekdays and cur not in exceptions:
            res.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)
    return res

# ---------------- Main parsing ----------------

def parse_trip_block(block):
    trip = {}
    h = TRIP_HEAD.search(block)
    if h:
        trip['trip_number'], trip['pairing_number'], trip['base'] = h.group(1), h.group(2), h.group(3).upper()
    else:
        h2 = TRIP_HEAD_NOBASE.search(block)
        if not h2:
            return None
        trip['trip_number'], trip['pairing_number'], trip['base'] = h2.group(1), h2.group(2), None

    trip['original_text'] = block
    trip['operating_dates'] = parse_operating_dates(block)

    # --- TAFB ---
    tafb_m = TAFB_RE.search(block)
    if tafb_m: 
        trip['tafb'] = tafb_m.group(1).strip()
        try:
            trip['tafb_minutes'] = time_str_to_minutes(trip['tafb'])
        except Exception:
            pass # Keep tafb string even if parsing fails

    # --- Credit ---
    credit_m = CREDIT_RE.search(block)
    trip['correctedcredit'] = 0.0
    trip['credit_time_per_day'] = 0.0
    if credit_m: 
        trip['credit_time'] = credit_m.group(1).strip()
        try:
            total_minutes = time_str_to_minutes(trip['credit_time'])
            trip['credit_minutes'] = total_minutes
            trip['correctedcredit'] = round(total_minutes / 60.0, 2)
        except Exception:
            pass # Keep credit_time string

    # --- Per Diem ---
    per_m = PERDIEM_RE.search(block)
    trip['correctedperdiem'] = 0.0
    if per_m:
        try: 
            per_diem_val = float(per_m.group(1).replace(',', ''))
            trip['per_diem'] = per_diem_val
            trip['correctedperdiem'] = math.ceil(per_diem_val / 2.0)
        except Exception: 
            trip['per_diem'] = per_m.group(1).strip()


    trip['days'] = {}
    trip['layovers'] = []
    # --- ADDED: Initialize deadhead fields ---
    trip['has_deadhead'] = False
    trip['deadhead_legs'] = []
    # --- END ADDED ---

    # Legs (single pass over whole block)
    # --- ✅ MODIFIED LOOP ---
    for lm in LEG_PATTERN.finditer(block):
        current_day = lm.group(1)
        flight_num = lm.group(2).upper()  # group(2) is FULL flight num
        
        dep = lm.group(3).upper(); arr = lm.group(4).upper()
        dep_t = lm.group(5); arr_t = lm.group(6); blk = lm.group(7)
        
        # --- Apply deadhead flight number replacement ---
        display_flight_num = flight_num
        is_deadhead = False
        if flight_num.startswith("DH") or flight_num.startswith("AC"):
            is_deadhead = True
            display_flight_num = "000DH"
        # --- End replacement ---
            
        leg_string = f"{display_flight_num}    {dep} {arr} {dep_t} {arr_t}  {blk}"
        trip['days'].setdefault(current_day, []).append(leg_string)

        if is_deadhead:
            trip['has_deadhead'] = True
            trip['deadhead_legs'].append(leg_string)
    # --- END MODIFIED LOOP ---

    # Layovers: scan by line to keep heuristics
    for raw in block.splitlines():
        line = raw.rstrip()
        lmkr = LAYOVER_MARKER.search(line)
        if lmkr and LAYOVER_CUE.search(line):
            apt = lmkr.group(1).upper()
            dmatch = LAYOVER_DUR.search(line)
            trip['layovers'].append({"location": apt, "duration": dmatch.group(1) if dmatch else "N/A"})
        else:
            if LAYOVER_CUE.search(line):
                apt_alt = re.search(r'\b([A-Z]{3})\b', line)
                if apt_alt:
                    apt = apt_alt.group(1).upper()
                    dmatch = LAYOVER_DUR.search(line)
                    if dmatch:
                        try:
                            mins = time_str_to_minutes(dmatch.group(1))
                            if mins >= 8*60:
                                trip['layovers'].append({"location": apt, "duration": dmatch.group(1)})
                        except Exception:
                            trip['layovers'].append({"location": apt, "duration": dmatch.group(1)})
                    else:
                        trip['layovers'].append({"location": apt, "duration": "N/A"})

    # Longest layover (hours decimal)
    trip['longest_layover'] = 0.0
    try:
        longest = 0
        for lay in trip.get('layovers', []):
            dur = lay.get('duration', '')
            try:
                mins = time_str_to_minutes(dur)
                if mins > longest:
                    longest = mins
            except Exception:
                continue
        if longest > 0:
            trip['longest_layover'] = round(longest / 60.0, 2)
    except Exception:
        trip['longest_layover'] = 0.0

    # Days of work
    if trip['days']:
        try:
            keys = sorted(int(k) for k in trip['days'].keys())
            trip['days_of_work'] = keys[-1] if keys else 0
        except Exception:
            trip['days_of_work'] = len(trip['days'])
    else:
        trip['days_of_work'] = 0
        
    # --- Credit Per Day (moved here to depend on days_of_work) ---
    if 'credit_minutes' in trip and trip.get('days_of_work', 0) > 0:
        try:
            avg_hours_decimal = (trip['credit_minutes'] / trip['days_of_work']) / 60.0
            trip['credit_time_per_day'] = round(avg_hours_decimal, 2)
        except Exception:
            trip['credit_time_per_day'] = 0.0

    # Calendar instances
    trip['calendar'] = []
    if trip.get('operating_dates') and trip.get('days'):
        try:
            sorted_days = sorted(int(k) for k in trip['days'].keys())
            for start_date_str in trip['operating_dates']:
                try:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    instance_calendar = {}
                    for day_num in sorted_days:
                        current_date = start_date + timedelta(days=day_num - 1)
                        instance_calendar[str(day_num)] = current_date.strftime('%a %d %b')
                    trip['calendar'].append(instance_calendar)
                except Exception:
                    continue
        except Exception:
            pass # Failed to build calendar

    # Redeye: must have >=1 layover, and a leg spanning 02:00 local
    trip['is_redeye'] = False
    try:
        if trip.get('days_of_work', 0) > 1 and len(trip.get('layovers', [])) >= 1:
            for legs in trip['days'].values():
                for leg in legs:
                    times = TIME_PAIR.findall(leg)
                    if len(times) >= 2:
                        dep_m = int(times[0][0])*60 + int(times[0][1])
                        arr_m = int(times[1][0])*60 + int(times[1][1])
                        includes_0200 = False
                        if dep_m <= arr_m:
                            includes_0200 = (dep_m <= 120 <= arr_m)
                        else:  # wraps midnight
                            includes_0200 = (arr_m >= 120 or dep_m <= 120)
                        if includes_0200:
                            trip['is_redeye'] = True
                            raise StopIteration
    except StopIteration:
        pass
    except Exception:
        trip['is_redeye'] = False

    # Lazy pairing: multi-day, 1 leg per day
    trip['is_lazy_pairing'] = False
    try:
        if trip['days_of_work'] > 1:
            trip['is_lazy_pairing'] = all(len(d) <= 1 for d in trip['days'].values())
    except Exception:
        trip['is_lazy_pairing'] = False

    # Weekday-only (uses calendar instances)
    trip['is_weekday_only'] = True
    try:
        if not trip.get('calendar'):
            trip['is_weekday_only'] = False
        else:
            for instance in trip.get('calendar', []):
                for date_str in instance.values():
                    date_obj = datetime.strptime(f"{date_str} {YEAR}", '%a %d %b %Y').date()
                    if date_obj.weekday() >= 5:  # Sat/Sun
                        trip['is_weekday_only'] = False
                        raise StopIteration
    except StopIteration:
        pass
    except Exception:
        trip['is_weekday_only'] = False

    # Commutable heuristic
    trip['is_commutable'] = False
    try:
        d = trip.get('days_of_work', 0)
        rpt = re.search(r'RPT.*?(\d{2}:\d{2})', block)
        rls = re.search(r'RLS.*?(\d{2}:\d{2})', block)
        if rpt: 
            trip['report_time'] = rpt.group(1)
            trip['report_minutes'] = time_str_to_minutes(trip['report_time'])
        if rls: 
            trip['release_time'] = rls.group(1)
            trip['release_minutes'] = time_str_to_minutes(trip['release_time'])
            
        if d in (3,4,5) and 'report_minutes' in trip and 'release_minutes' in trip:
            rpt_m = trip['report_minutes']
            rls_m = trip['release_minutes']
            trip['is_commutable'] = (rpt_m > 11*60 and rls_m < (22*60+30))
    except Exception:
        trip['is_commutable'] = False

    return trip


def parse_full_text(content):
    blocks = TRIP_SPLIT.split(content)
    out = []
    for b in blocks:
        # Check for minimal viability of a block
        if 'TRIP' in b and ('TAFB' in b or 'Credit Time' in b or 'PERDIEM' in b):
            p = parse_trip_block(b)
            if p:
                out.append(p)
    return out


# ---------------- Additional Regex for Prelim ----------------
PRELIM_SPLIT = re.compile(
    r'(?=^\s*[A-Z]{3,}(?:\s+\([A-Z0-9]{1,3}\))?\s*\[[0-9,\s_]+\]\s*[A-Z]{0,3}:?.*?effective\s+[A-Z]{3}\s+\d{1,2}-[A-Z]{3}\s+\d{1,2})',
    re.M | re.I
)

def parse_prelim_block(block):
    """
    Convert a prelim-style block into a TRIP-style block and reuse existing logic,
    preserving the actual weekday mask after the BASE: token and the real
    'effective ...' clause from the first line.
    """
    lines = block.strip().splitlines()
    if not lines:
        return None

    head = lines[0]

    # Base code and weekday mask right after "AAA:"
    # e.g. "YYC: _____6_" or "YVR: __34__7"
    base_code = None
    base_mask = "_______"  # fallback if not present
    m_base_mask = re.search(r'\b([A-Z]{3}):\s*([0-9_]{1,7})', head)
    if m_base_mask:
        base_code = m_base_mask.group(1).upper()
        base_mask = m_base_mask.group(2)
    else:
        m_base_only = re.search(r'\b([A-Z]{3}):', head)
        if m_base_only:
            base_code = m_base_only.group(1).upper()

    # Bracket mask like [1,1,0,0] if present (often <7 entries → parser will ignore it)
    m_bracket = re.search(r'\[([0-9,\s_]+)\]', head)
    bracket_mask_str = f"[{m_bracket.group(1)}]" if m_bracket else "[1,1,1,1,1,1,1]"

    # Preserve the real effective clause from the header (includes exceptions)
    # e.g. "effective JUL 05-JUL 26 except JUL 19"
    m_eff = re.search(r'(effective.*)$', head, re.I)
    effective_clause = m_eff.group(1) if m_eff else "effective AUTO"

    # Fabricate a stable trip/pairing id
    fake_trip = (base_code or 'PRELIM')
    fake_pairing = f"P{datetime.now().strftime('%H%M%S')}"

    # IMPORTANT: inject the *real* base_mask and effective clause
    fake_header = (
        f"TRIP #{fake_trip}  {fake_pairing}  ({base_code or 'XX'}) "
        f"{bracket_mask_str} {base_code or ''}: {base_mask} {effective_clause}"
    )

    full_block = f"{fake_header}\n{block}"
    return parse_trip_block(full_block)


def strip_cover_pages(text):
    """
    Remove any leading pages before the first actual pairing content.
    Detects the first occurrence of 'TRIP #' or a prelim header with 'effective'.
    """
    # Find first occurrence of either a TRIP or a prelim-style 'effective'
    idx = min(
        [i for i in [
            text.lower().find('trip #'),
            text.lower().find('effective '),
        ] if i != -1] or [0]
    )
    return text[idx:] if idx > 0 else text


def parse_full_text(content):
    """
    Handles both final and prelim pairing formats.
    """
    # ✅ Strip cover pages or disclaimers
    content = strip_cover_pages(content)

    # First detect finals normally
    trip_blocks = TRIP_SPLIT.split(content)
    prelim_blocks = PRELIM_SPLIT.split(content)

    parsed = []

    # Standard finals
    for b in trip_blocks:
        if 'TRIP' in b and ('TAFB' in b or 'Credit Time' in b or 'PERDIEM' in b):
            p = parse_trip_block(b)
            if p:
                parsed.append(p)

    # Prelims (skip if it already contained TRIP)
    for b in prelim_blocks:
        if 'TRIP' not in b and ('TAFB' in b or 'Credit Time' in b or 'PERDIEM' in b):
            p = parse_prelim_block(b)
            if p:
                p['is_prelim'] = True
                parsed.append(p)

    return parsed


def main():
    """
    Main execution function to run the parser from the command line.
    """
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pairing_file.txt>", file=sys.stderr)
        print("This script reads a pairing file and outputs the parsed data as JSON.", file=sys.stderr)
        sys.exit(1)
        
    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"Error: File not found at {filepath}", file=sys.stderr)
        sys.exit(1)
        
    try:
        # Use 'latin-1' or 'cp1252' if 'utf-8' fails, as these are common for text files
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                content = f.read()

    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)
        
    try:
        parsed_data = parse_full_text(content)
        # Output the parsed data as pretty-printed JSON
        print(json.dumps(parsed_data, indent=2))
    except Exception as e:
        print(f"Error during parsing: {e}", file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()