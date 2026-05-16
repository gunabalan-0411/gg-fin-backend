# Finance Collection Extraction Skill

## Purpose
Reusable extraction rules for handwritten Tamil finance collection records. This file evolves with each extraction session through human feedback.

---

## Domain Context

**Finance Products:**
- **EDI** — Easy Daily Installment (default product if no IOP marker found)
- **IOP** — Interest Only Payment

**Field agent writes:** date, customer name, product indicator (optional), online payment marker (optional), amount collected.

---

## Shorthand Markers

### IOP Product Indicators
| Marker | Interpretation |
|--------|---------------|
| `EL`   | IOP product — "EL" likely short for "Elivarathu" (interest) |
| `EN`   | IOP product — variant spelling |
| `E.L`  | IOP product — dotted form |
| `E.N`  | IOP product — dotted form |

### Online / Google Pay Indicators
| Marker | Interpretation |
|--------|---------------|
| `GG`   | Google Pay / online |
| `G`    | Google Pay / online |
| `GPAY` | Google Pay |
| `G.B`  | Google Pay — variant |
| `GB`   | Google Pay — variant |
| `GP`   | Google Pay — short form |

### EDI (Default)
If no EL/EN marker is found, assume **EDI** product.
If no GG/G/GPAY marker is found, assume **CASH** payment.

---

## Date Detection Rules

1. Dates appear at the **top of each section** before a group of customer entries.
2. Common formats: `24-1-2022`, `24/1/22`, `25-1-2026`, `25-ஜனவரி-2026`.
3. Tamil month names to English mapping:
   - ஜனவரி → January
   - பிப்ரவரி → February
   - மார்ச் → March
   - ஏப்ரல் → April
   - மே → May
   - ஜூன் → June
   - ஜூலை → July
   - ஆகஸ்ட் → August
   - செப்டம்பர் → September
   - அக்டோபர் → October
   - நவம்பர் → November
   - டிசம்பர் → December
4. A new date header signals a new collection group — all following entries belong to it until the next date is found.
5. Normalize all dates to `DD-MM-YYYY` format.

---

## Record Structure Per Line

Typical handwritten line pattern:
```
<CustomerName>  [<ProductMarker>]  [<OnlineMarker>]  <Amount>  [<Notes>]
```

Examples:
```
ராஜி   E.L  200     → IOP, CASH, 200
தங்கராஜ்  G.B  1400   → EDI, ONLINE (GPAY), 1400
சரவணன்       2000   → EDI, CASH, 2000
ஷெர்லி  E.L  9000   → IOP, CASH, 9000
```

---

## Ignore List — Non-Collection Lines

Ignore lines that match these patterns:
- Totals: lines with words like `மொத்தம்`, `total`, `போய்`, `கூட்டல்`
- Expense notes: lines mentioning `செலவு`, `expense`, standalone math
- GPay summary boxes: lines grouping multiple GPay transactions with totals
- Settlement lines: `தீர்வு`, `settlement`
- Lines that are pure numbers with no customer name context
- Standalone calculations: `3000 + 2000 = 5000`

---

## Amount Extraction Rules

1. Amounts appear at the **end** of the entry line, right-aligned.
2. Amounts are pure integers (no decimal in typical entries).
3. If two amounts appear: first is likely the installment, second may be total/running — take the **first** as collected amount.
4. Common misreads: `1` vs `l`, `0` vs `o`, `5` vs `S` — use context to disambiguate.
5. Amounts range typically from 200 to 50,000 in this domain.

---

## Customer Name Extraction Rules

1. Names are in **Tamil script** — transliterate to English phonetically.
2. Names typically appear at the **start** of the line.
3. Common Tamil name endings: `-ன்`, `-ம்`, `-ி`, `-ா`, `-ல்`
4. Transliteration guide (phonetic):
   - ர → r, ல → l, ட → d/t, ண → n, ன → n
   - சர → sar/char, ரா → ra, கு → ku, தம் → dam
5. Preserve name as closely as possible; do not anglicize or translate meaning.
6. If name is unclear, use `[UNCLEAR]` and mark confidence as LOW.

---

## Confidence Scoring Guide

| Score Range | Meaning |
|-------------|---------|
| 0.9 – 1.0   | Clear, unambiguous entry |
| 0.7 – 0.89  | Minor ambiguity in name or amount |
| 0.5 – 0.69  | Significant ambiguity; needs review |
| 0.0 – 0.49  | Very unclear; flag for manual review |

---

## Edge Cases & Failure Modes

### Known Ambiguities
- `EL` and `El` may be a name fragment vs product marker — check context (if followed by an amount, likely a marker)
- `G` alone may be part of a name or a GPay marker — use surrounding entries for context
- Tamil letters `ஜி` can look like `ஜ` — both could be part of "G" transliteration
- Amount columns may drift left/right due to handwriting — use whitespace as alignment guide
- Some pages have **two columns** of entries side by side — treat each column independently

### Multi-Day Pages
- When a date line appears mid-page, split entries into separate date groups
- Watch for date written in margin or as a header row

### Bottom-of-Page Notes
- Field agents often write expense summaries and day totals at the bottom
- These are **NOT** customer entries — skip them entirely

---

## Extraction Prompt Template (Claude Vision)

```
You are a specialized Tamil handwritten finance document extractor.

TASK: Extract all customer collection entries from this handwritten page image.

DOMAIN RULES:
- Products: EDI (default) or IOP (marked by EL/EN)
- Payment: CASH (default) or ONLINE (marked by GG/G/GPAY/GB)
- Ignore: expense notes, totals, GPay summaries, calculations

OUTPUT: Return a JSON array with this schema per record:
{
  "collection_date": "DD-MM-YYYY",
  "customer_name": "transliterated name",
  "product_type": "EDI or IOP",
  "payment_mode": "CASH or ONLINE",
  "online_marker": "GG/G/GPAY/GB or null",
  "amount": integer,
  "raw_text": "original line as you read it",
  "confidence_score": 0.0-1.0,
  "page_number": integer,
  "notes": "any ambiguity notes"
}

IMPORTANT:
- Do NOT hallucinate names or amounts
- Mark unclear entries with confidence < 0.7
- Detect date boundaries and assign correct date per group
- Transliterate Tamil names phonetically to English
```

---

## Revision History

| Date       | Version | Change |
|------------|---------|--------|
| 2026-05-16 | 1.0     | Initial skill file created from page 1 analysis |

---

## Feedback Log

_(Human corrections are appended here after each extraction session)_

<!-- FEEDBACK ENTRIES BELOW -->
