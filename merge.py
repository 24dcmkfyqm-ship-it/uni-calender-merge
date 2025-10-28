import os, re, requests
from datetime import timedelta
from dateutil import tz
from icalendar import Calendar, Event

FEED_URL = os.environ["FEED_URL"]          # Uni
OVERRIDES_URL = os.environ["OVERRIDES_URL"]# Dein Kalender
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Berlin")

def fetch_ics(url: str) -> Calendar:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return Calendar.from_ical(r.content)

def uid(ev: Event) -> str:
    return str(ev.get("UID") or "").strip()

def text(v) -> str:
    return "" if v is None else str(v)

def norm_summary(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

def get_dt(ev: Event, key: str):
    v = ev.get(key)
    return v.dt if v else None

def approx_equal_time(a, b, minutes=15):
    if not a or not b: return False
    delta = abs((a - b))
    return delta <= timedelta(minutes=minutes)

def find_orig_uid_in_override(ov: Event) -> str|None:
    """
    Versuche, die Ziel-UID aus dem Override-Event zu entnehmen:
    - X-ORIG-UID (wenn du das mal per .ics importierst)
    - 'ORIG-UID: <uid>' irgendwo in DESCRIPTION
    - '[UID:<uid>]' am Ende von SUMMARY
    """
    x = ov.get("X-ORIG-UID")
    if x: return str(x)
    desc = text(ov.get("DESCRIPTION"))
    m = re.search(r"ORIG-UID:\s*([^\s]+)", desc or "", flags=re.I)
    if m: return m.group(1).strip()
    summ = text(ov.get("SUMMARY"))
    m2 = re.search(r"\[UID:([^\]]+)\]$", summ or "", flags=re.I)
    if m2: return m2.group(1).strip()
    return None

def index_base_by_uid(base_events):
    return { uid(e): e for e in base_events if uid(e) }

def fuzzy_match_base(base_events, override: Event):
    """Letzter Ausweg: gleiche (normierte) SUMMARY und ähnliche DTSTART."""
    o_sum = norm_summary(text(override.get("SUMMARY")))
    o_dt  = get_dt(override, "DTSTART")
    candidates = []
    for ev in base_events:
        b_sum = norm_summary(text(ev.get("SUMMARY")))
        b_dt  = get_dt(ev, "DTSTART")
        if o_sum == b_sum and approx_equal_time(o_dt, b_dt, minutes=30):
            candidates.append(ev)
    return candidates[0] if candidates else None

def apply_override(base: Event, override: Event):
    """Feldweise Überschreibung: SUMMARY, LOCATION, DTSTART/DTEND, STATUS, DESCRIPTION."""
    for key in ("SUMMARY","LOCATION","DESCRIPTION","STATUS"):
        if override.get(key) is not None:
            base[key] = override.get(key)

    # Zeiten: Wenn Override DTSTART gesetzt ist → übernehmen.
    if override.get("DTSTART") is not None:
        base["DTSTART"] = override.get("DTSTART")
        # Falls Override Dauer klar ist (DTEND vorhanden), übernehmen.
        if override.get("DTEND") is not None:
            base["DTEND"] = override.get("DTEND")

def main():
    uni = fetch_ics(FEED_URL)
    ov  = fetch_ics(OVERRIDES_URL)

    # Output-Kalender vorbereiten
    out = Calendar()
    out.add("PRODID", "-//wolfi dual-merge//DE")
    out.add("VERSION", "2.0")
    out.add("CALSCALE", "GREGORIAN")
    # VTIMEZONE aus Quelle übernehmen, falls vorhanden
    for comp in uni.walk():
        if comp.name == "VTIMEZONE":
            out.add_component(comp)

    base_events = [c for c in uni.walk("VEVENT")]
    overrides   = [c for c in ov.walk("VEVENT")]

    base_by_uid = index_base_by_uid(base_events)

    # 1) Alle Base-Events kopieren (damit alles drin ist)
    #    (Wir überschreiben gleich die Felder falls Overrides matchen)
    merged_events = { id(e): e for e in base_events }  # identitäts-basiert

    for o in overrides:
        target = None

        # a) direkter Match über Original-UID, falls in Override hinterlegt
        orig = find_orig_uid_in_override(o)
        if orig and orig in base_by_uid:
            target = base_by_uid[orig]

        # b) falls kein UID-Hinweis: fuzzy match (Summary+Zeitfenster)
        if target is None:
            target = fuzzy_match_base(base_events, o)

        if target is not None:
            # Felder überschreiben – UID bleibt die der Uni,
            # damit Kalender-Apps es als „dasselbe Event“ erkennen.
            apply_override(target, o)
        else:
            # Kein Match in Base → Override-Event zusätzlich aufnehmen
            # (z. B. komplett neuer Termin nur in deinem Kalender)
            out.add_component(o)

    # Base (teils überschrieben) in den Output schreiben
    for ev in base_events:
        out.add_component(ev)

    os.makedirs("docs", exist_ok=True)
    with open("docs/merged.ics","wb") as f:
        f.write(out.to_ical())

if __name__ == "__main__":
    main()
