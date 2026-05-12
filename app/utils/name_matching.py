from __future__ import annotations
import re
from rapidfuzz import fuzz

try:
    import jellyfish

    _JELLYFISH = True
except ImportError:
    _JELLYFISH = False


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _phonetic_score(a: str, b: str) -> float:
    """Return 85 if soundex codes match, 0 otherwise."""
    if not _JELLYFISH:
        return 0.0
    try:
        # Compare first tokens phonetically (handles partial name matches)
        a_tokens = a.split()
        b_tokens = b.split()
        # Any token pair phonetic match
        for at in a_tokens:
            for bt in b_tokens:
                if len(at) >= 2 and len(bt) >= 2:
                    if jellyfish.soundex(at) == jellyfish.soundex(bt):
                        return 85.0
                    if jellyfish.metaphone(at) == jellyfish.metaphone(
                        bt
                    ) and jellyfish.metaphone(at):
                        return 85.0
        return 0.0
    except Exception:
        return 0.0


def get_similar_score(spoken: str, candidates: list[dict]) -> list[dict]:
    """
    Score all candidates against the spoken name.
    candidates: list of {customer_id, name, display_name}
      where `name` is already normalized (lowercase, stripped)
    Returns list of {customer_id, name, score} sorted by score desc.
    """
    spoken_norm = _normalize(spoken)
    scored = []

    for c in candidates:
        name_norm = c["name"]  # pre-normalized
        display = c.get("display_name", name_norm)

        # Exact match
        if spoken_norm == name_norm:
            scored.append(
                {"customer_id": c["customer_id"], "name": display, "score": 100.0}
            )
            continue

        # Fuzzy scores (multiple algorithms)
        token_set = fuzz.token_set_ratio(spoken_norm, name_norm)
        ratio = fuzz.ratio(spoken_norm, name_norm)
        partial = fuzz.partial_ratio(spoken_norm, name_norm)
        wratio = fuzz.WRatio(spoken_norm, name_norm)
        fuzzy_best = max(token_set, ratio, partial, wratio)

        # Phonetic score
        phonetic = _phonetic_score(spoken_norm, name_norm)

        # Final: take best of fuzzy and phonetic
        final = max(fuzzy_best, phonetic)
        # If phonetic matched (≥85) but fuzzy is slightly lower, floor at 80
        if phonetic >= 85 and final < 80:
            final = 80.0

        scored.append(
            {"customer_id": c["customer_id"], "name": display, "score": round(final, 1)}
        )

    return sorted(scored, key=lambda x: x["score"], reverse=True)


def find_best_match(spoken_name: str, name_map: list[dict]) -> dict | None:
    """
    Legacy helper: returns the single best match or None if score < 60.
    name_map: list of {customer_id, customer_name_en, customer_name_ta}
    """
    if not name_map:
        return None

    spoken = _normalize(spoken_name)

    # Exact match first
    for entry in name_map:
        en = _normalize(entry.get("customer_name_en") or "")
        ta = _normalize(entry.get("customer_name_ta") or "")
        if spoken == en or spoken == ta:
            return entry

    # Build candidates from both en and ta names (deduplicate by customer_id)
    candidates: dict[int, dict] = {}
    for entry in name_map:
        cid = entry["customer_id"]
        en = _normalize(entry.get("customer_name_en") or "")
        ta = _normalize(entry.get("customer_name_ta") or "")
        # prefer en name, fall back to ta
        name = en or ta
        if name:
            candidates[cid] = {
                "customer_id": cid,
                "name": name,
                "display_name": entry.get("customer_name_en")
                or entry.get("customer_name_ta")
                or "",
            }

    results = get_similar_score(spoken, list(candidates.values()))
    if results and results[0]["score"] >= 60:
        best_cid = results[0]["customer_id"]
        return next((r for r in name_map if r["customer_id"] == best_cid), None)
    return None


_LATIN_OR_TAMIL = r"[a-zA-Z\u0B80-\u0BFF]"


def parse_online_entry(text: str) -> list[str]:
    """
    Split online-payer transcription by dots, commas, asterisks, or spoken variants.
    Handles: .  dot  ,  comma  coma  komma  koma  *  star
    E.g. "amudha. selvam , guru dot valarmadhi * murugan" → ["amudha", "selvam", "guru", "valarmadhi", "murugan"]
    Supports both Latin and Tamil Unicode names.
    """
    # Replace spoken separator variants with a real comma
    normalized = re.sub(
        r"\b(dot|comma|Khama|kamma|khamma|kama|cama|coma|komma|koma|star)\b",
        ",",
        text,
        flags=re.IGNORECASE,
    )
    segments = re.split(r"[.,*\n]+", normalized)
    return [s.strip() for s in segments if re.search(_LATIN_OR_TAMIL, s.strip())]


def detect_online_names(text: str) -> list[str]:
    """
    Extract spoken names associated with the 'online' keyword.
    E.g. "Gajalajmi 400 online, Kannan 300, Salun Balaji 100 online"
    → ["Gajalajmi", "Salun Balaji"]
    """
    results = []
    segments = re.split(r"[,.\n]+", text)
    for seg in segments:
        seg = seg.strip()
        if re.search(r"\bonline\b", seg, re.IGNORECASE):
            cleaned = re.sub(r"\bonline\b", "", seg, flags=re.IGNORECASE)
            cleaned = re.sub(r"[\d,]+", "", cleaned).strip()
            if cleaned:
                results.append(cleaned)
    return results


def parse_voice_entry(text: str) -> list[dict]:
    """
    Parse voice entries from a transcription string.
    Splits by locating numbers in the text — the name is everything
    between the previous number's end and the current number's start.

    Works on Whisper output like "Gajalajmi 400 Kannan 300 Salun Balaji 100"
    or Tamil Unicode "கண்ணன் 400 பிரியா 200" without needing delimiters.
    """
    # Remove "online" keyword before parsing so it doesn't become part of names
    text = re.sub(r"\bonline\b", "", text, flags=re.IGNORECASE)

    number_re = re.compile(r"\d+(?:\.\d+)?")
    matches = list(number_re.finditer(text))
    if not matches:
        return []

    # Strip non-letter chars (Latin or Tamil Unicode) from edges of name
    _strip_edges = re.compile(r"^[^a-zA-Z\u0B80-\u0BFF]+|[^a-zA-Z\u0B80-\u0BFF]+$")

    results = []
    prev_end = 0
    for match in matches:
        # Name is text from end of previous number to start of this number
        raw_name = text[prev_end : match.start()].strip()
        name = _strip_edges.sub("", raw_name).strip()
        prev_end = match.end()
        if not name:
            continue
        try:
            amount = float(match.group())
            if amount > 0:
                results.append({"name": name, "amount": amount})
        except ValueError:
            continue
    return results
