#!/usr/bin/env python3
import re
import json
import sys
import os
import math
from datetime import datetime, timedelta

# ---------------- Constants & precompiled regex ----------------
CURRENT_YEAR = datetime.now().year
MONTH_MAP = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}

TRIP_SPLIT = re.compile(r'(?=\bTRIP\s*#)', re.I)
TRIP_HEAD = re.compile(r"TRIP\s*#\s*(\S+)\s+(\S+).*?\b([A-Z]{3})\b", re.I)
TRIP_HEAD_NOBASE = re.compile(r"TRIP\s*#\s*(\S+)\s+(\S+)", re.I)

TAFB_RE = re.compile(r"TAFB:\s*([\d+h\(\)\w:]+)", re.I)
CREDIT_RE = re.compile(r"Credit Time:\s*([^\s,]+)", re.I)
PERDIEM_RE = re.compile(r"PERDIEM:\s*([\d\.,]+)", re.I)

LEG_PATTERN = re.compile(
    r"\s+(\d{1,2})\s+([A-Z0-9_]{2,9})\s+([A-Z]{3})\s+([A-Z]{3})\s+(\d{2}:\d{2})\s+(\d{2}:\d{2})\s+([0-9]{1,3}h[0-9]{2}|[0-9]{1,2}:\d{2})",
    re.I,
)

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

# --- UPDATED PRELIM REGEX ---
# Relaxed the anchor to catch prelim headers even with messy whitespace or missing brackets
PRELIM_SPLIT = re.compile(
    r'(?=^[ \t]*[A-Z]{3,}.*?effective\s+[A-Z]{3}\s+\d{1,2})',
    re.M | re.I
)
PRELIM_TRIP_HEAD = re.compile(r"TRIP\s*#\s*(\S+)\s+(\S+)", re.I)

# ---------------- Helper functions ----------------

def time_str_to_minutes(time_str):
    if not time_str or not isinstance(time_str, str):
        raise ValueError("Invalid time string input")
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
    weekdays = {(i % 7) for i, ch in enumerate(nums[:7]) if ch == '1'}
    return weekdays or None


def _parse_underscore_digit_mask(mask):
    mask = (mask or '').strip()[:7].ljust(7, '_')
    weekdays = {(i % 7) for i, ch in enumerate(mask) if ch != '_'}
    return weekdays or None


def determine_effective_year(block):
    current_date = datetime.now().date()
    first_date_match = MO_DAY.search(block)
    
    if first_date_match:
        mo, day = first_date_match.groups()
        mo_num = MONTH_MAP.get(mo.upper())
        day_num = int(day)
        
        if mo.upper() == 'JAN' and current_date.month == MONTH_MAP['DEC']:
            return CURRENT_YEAR + 1
        
        try:
            test_date = datetime(CURRENT_YEAR, mo_num, day_num).date()
            if test_date < current_date:
                return CURRENT_YEAR + 1
        except ValueError:
            pass
            
    return CURRENT_YEAR


def parse_operating_dates(block, effective_year):
    eff_idx = block.lower().find('effective')
    if eff_idx == -1:
        return []
    window = block[max(0, eff_idx - 200): eff_idx + 200]

    m = EFFECTIVE_NEIGHBOR.search(window)
    if not m:
        return []
    start_mon, start_day, end_mon, end_day = m.group(1).upper(), m.group(2), m.group(3).upper(), m.group(4)
    try:
        start_date = datetime(effective_year, MONTH_MAP[start_mon], int(start_day)).date()
        end_date = datetime(effective_year, MONTH_MAP[end_mon], int(end_day)).date()
        
        if end_date < start_date:
            end_date = datetime(effective_year + 1, MONTH_MAP[end_mon], int(end_day)).date()
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
        mask_weekdays = set(range(7))

    exceptions = set()
    ex_match = EXCEPTIONS_RE.search(window)
    if ex_match:
        ex_str = ex_match.group(1)
        for mo, d in MO_DAY.findall(ex_str):
            try:
                ex_date = datetime(effective_year, MONTH_MAP[mo.upper()], int(d)).date()
                if start_date <= ex_date <= end_date:
                    exceptions.add(ex_date)
                ex_date_next = datetime(effective_year + 1, MONTH_MAP[mo.upper()], int(d)).date()
                if start_date <= ex_date_next <= end_date:
                    exceptions.add(ex_date_next)
            except Exception:
                continue

    res = []
    cur = start_date
    while cur <= end_date:
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
        if h2:
            trip['trip_number'], trip['pairing_number'], trip['base'] = h2.group(1), h2.group(2), None
        else:
            return None

    trip['original_text'] = block
    effective_year = determine_effective_year(block)
    trip['effective_year'] = effective_year
    trip['operating_dates'] = parse_operating_dates(block, effective_year)

    # TAFB
    tafb_m = TAFB_RE.search(block)
    if tafb_m: 
        trip['tafb'] = tafb_m.group(1).strip()
        try:
            trip['tafb_minutes'] = time_str_to_minutes(trip['tafb'])
        except Exception:
            pass

    # Credit
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
            pass

    # Per Diem
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
    trip['has_deadhead'] = False
    trip['deadhead_legs'] = []

    for lm in LEG_PATTERN.finditer(block):
        current_day = lm.group(1)
        flight_num = lm.group(2).upper()
        dep = lm.group(3).upper(); arr = lm.group(4).upper()
        dep_t = lm.group(5); arr_t = lm.group(6); blk = lm.group(7)
        
        # Define all deadhead prefixes in a clean tuple
        dh_prefixes = ("DH", "AC", "UA", "LIM9", "AV", "VB", "AA")
        is_deadhead = flight_num.startswith(dh_prefixes)
        
        # Set display number to 000DH for all deadheads, including LIM9
        display_flight_num = "000DH" if is_deadhead else flight_num
            
        leg_data = {
            "flight_number": display_flight_num,
            "original_flight_number": flight_num, # Added to keep track of the LIM9 identifier
            "dep_station": dep,
            "arr_station": arr,
            "dep_time": dep_t,
            "arr_time": arr_t,
            "duration": blk,
            "is_deadhead": is_deadhead
        }
        trip['days'].setdefault(current_day, []).append(leg_data)

        if is_deadhead:
            trip['has_deadhead'] = True
            leg_string = f"{display_flight_num}    {dep} {arr} {dep_t} {arr_t}  {blk}"
            trip['deadhead_legs'].append(leg_string)

    trip['starts_or_ends_with_deadhead'] = False
    trip['starts_with_deadhead_to_ylw'] = False
    try:
        if trip['days']:
            sorted_days = sorted(int(k) for k in trip['days'].keys())
            if sorted_days:
                first_day_key = str(sorted_days[0])
                first_day_legs = trip['days'].get(first_day_key, [])
                if first_day_legs:
                    first_leg_obj = first_day_legs[0]
                    if isinstance(first_leg_obj, dict) and first_leg_obj.get('is_deadhead'):
                        trip['starts_or_ends_with_deadhead'] = True
                        if first_leg_obj.get('arr_station', '').upper() == 'YLW':
                            trip['starts_with_deadhead_to_ylw'] = True

                if not trip['starts_or_ends_with_deadhead']:
                    last_day_key = str(sorted_days[-1])
                    last_day_legs = trip['days'].get(last_day_key, [])
                    if last_day_legs:
                        last_leg_obj = last_day_legs[-1]
                        if isinstance(last_leg_obj, dict) and last_leg_obj.get('is_deadhead'):
                            trip['starts_or_ends_with_deadhead'] = True
    except Exception:
        pass

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

    trip['longest_layover'] = 0.0
    try:
        longest = 0
        for lay in trip.get('layovers', []):
            dur = lay.get('duration', '')
            try:
                mins = time_str_to_minutes(dur)
                if mins > longest: longest = mins
            except Exception: continue
        if longest > 0:
            trip['longest_layover'] = round(longest / 60.0, 2)
    except Exception:
        trip['longest_layover'] = 0.0

    if trip['days']:
        try:
            keys = sorted(int(k) for k in trip['days'].keys())
            trip['days_of_work'] = keys[-1] if keys else 0
        except Exception:
            trip['days_of_work'] = len(trip['days'])
    else:
        trip['days_of_work'] = 0
        
    if 'credit_minutes' in trip and trip.get('days_of_work', 0) > 0:
        try:
            avg_hours_decimal = (trip['credit_minutes'] / trip['days_of_work']) / 60.0
            trip['credit_time_per_day'] = round(avg_hours_decimal, 2)
        except Exception:
            trip['credit_time_per_day'] = 0.0

    trip['calendar'] = []
    current_effective_year = trip.get('effective_year', CURRENT_YEAR) 
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
            pass

    trip['is_redeye'] = False
    try:
        if trip.get('days_of_work', 0) > 1 and len(trip.get('layovers', [])) >= 1:
            for legs in trip['days'].values():
                for leg in legs:
                    if isinstance(leg, dict):
                        dep_t_str = leg.get('dep_time', '')
                        arr_t_str = leg.get('arr_time', '')
                        dep_m_match = TIME_PAIR.match(dep_t_str)
                        arr_m_match = TIME_PAIR.match(arr_t_str)
                        if dep_m_match and arr_m_match:
                            dep_m = int(dep_m_match.group(1))*60 + int(dep_m_match.group(2))
                            arr_m = int(arr_m_match.group(1))*60 + int(arr_m_match.group(2))
                            includes_0200 = False
                            if dep_m <= arr_m:
                                includes_0200 = (dep_m <= 120 <= arr_m)
                            else:
                                includes_0200 = (arr_m >= 120 or dep_m <= 120)
                            if includes_0200:
                                trip['is_redeye'] = True
                                raise StopIteration
    except StopIteration: pass
    except Exception: trip['is_redeye'] = False

    trip['is_lazy_pairing'] = False
    try:
        if trip['days_of_work'] > 1:
            trip['is_lazy_pairing'] = all(len(d) <= 1 for d in trip['days'].values())
    except Exception: pass

    trip['is_weekday_only'] = True
    try:
        if not trip.get('calendar'):
            trip['is_weekday_only'] = False
        else:
            for instance in trip.get('calendar', []):
                for date_str in instance.values():
                    date_obj = datetime.strptime(f"{date_str} {current_effective_year}", '%a %d %b %Y').date()
                    if date_obj.weekday() >= 5:
                        trip['is_weekday_only'] = False
                        raise StopIteration
    except StopIteration: pass
    except Exception: trip['is_weekday_only'] = False

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
    except Exception: pass

    return trip


# --- UPDATED PRELIM PARSER LOGIC ---
def parse_prelim_block(block):
    """
    Improved prelim parser that is more resilient to formatting shifts in PRELIM PDFs.
    """
    lines = [l for l in block.strip().splitlines() if l.strip()]
    if not lines:
        return None

    head = lines[0]

    # 1. Search for a real TRIP # header anywhere in the block first
    m_real_trip_head = PRELIM_TRIP_HEAD.search(block)
    
    # 2. Extract Base and Mask from the header line
    base_code = "XX"
    base_mask = "_______"
    
    # Try to find "YEG: 111____" pattern
    m_base_mask = re.search(r'\b([A-Z]{3}):\s*([0-9_]{1,7})', head)
    if m_base_mask:
        base_code = m_base_mask.group(1).upper()
        base_mask = m_base_mask.group(2)
    else:
        # Fallback: look for 3-letter city code at start of line
        m_city = re.match(r'^\s*([A-Z]{3})', head)
        if m_city:
            base_code = m_city.group(1).upper()

    # 3. Handle IDs
    if m_real_trip_head:
        fake_trip = m_real_trip_head.group(1)
        fake_pairing = m_real_trip_head.group(2)
        is_prelim_type = False
    else:
        # Generate dummy ID for true prelims
        fake_trip = base_code
        fake_pairing = f"P{hash(block) % 1000000:06d}" 
        is_prelim_type = True

    # 4. Extract effective clause
    m_eff = re.search(r'(effective.*)$', head, re.I)
    effective_clause = m_eff.group(1) if m_eff else "effective AUTO"

    # 5. Reconstruct a "Final" style header so the main parser can digest it
    fake_header = (
        f"TRIP #{fake_trip}  {fake_pairing}  ({base_code}) "
        f"{base_code}: {base_mask} {effective_clause}"
    )

    full_block = f"{fake_header}\n{block}"
    trip = parse_trip_block(full_block) 
    
    if trip and is_prelim_type:
        trip['is_prelim'] = True
            
    return trip 


def strip_cover_pages(text):
    idx = min(
        [i for i in [
            text.lower().find('trip #'),
            text.lower().find('effective '),
        ] if i != -1] or [0]
    )
    return text[idx:] if idx > 0 else text


def parse_full_text(content):
    content = strip_cover_pages(content)

    # Split by canonical TRIP # first
    trip_blocks = TRIP_SPLIT.split(content)
    
    parsed = []
    blocks_to_check_as_prelim = []

    for b in trip_blocks:
        if 'TRIP' in b and ('TAFB' in b or 'Credit Time' in b or 'PERDIEM' in b):
            p = parse_trip_block(b)
            if p: parsed.append(p)
        else:
            blocks_to_check_as_prelim.append(b)

    # Process prelims using the relaxed splitter
    for b in blocks_to_check_as_prelim:
        # Re-split the fragment if it contains multiple prelims
        prelim_fragments = PRELIM_SPLIT.split(b)
        for frag in prelim_fragments:
            if 'effective' in frag.lower() and ('TAFB' in frag or 'Credit Time' in frag or LEG_PATTERN.search(frag)):
                p = parse_prelim_block(frag)
                if p:
                    is_duplicate = any(
                        (item.get('trip_number') == p.get('trip_number') and item.get('pairing_number') == p.get('pairing_number'))
                        for item in parsed
                    )
                    if not is_duplicate:
                        parsed.append(p)

    return parsed


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pairing_file.txt>", file=sys.stderr)
        sys.exit(1)
        
    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"Error: File not found at {filepath}", file=sys.stderr)
        sys.exit(1)
        
    try:
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
        print(json.dumps(parsed_data, indent=2))
    except Exception as e:
        print(f"Error during parsing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
