"""
Defaults for expense categories / people and the keyword guess used to group OLD expenses from their free-text notes.

Old notes look like "groceries, appa, from fund" or "kam marital, family, gold": what was bought, for whom, and sometimes an
event. The guess reads the first two; the notes text itself is never changed. The user reviews every guess before it is saved.
"""
from __future__ import annotations

import re
from typing import Optional

MISC = "Miscellaneous"          # the default category; cannot be renamed or deleted

# top-level category -> sub-categories (created the first time categories are read, only while there are none)
DEFAULT_TREE: dict[str, list[str]] = {
    "Groceries": ["Vegetables & Fruit", "Fish & Chicken", "Mutton & Meat", "Milk & Eggs", "Provisions"],
    "Eating Out": ["Hotel & Tiffin", "Online Food", "Snacks & Sweets", "Liquor"],
    "Vehicle & Fuel": ["Petrol", "Service & Repair", "Bus & Auto"],
    "Bills & Recharge": ["Electricity", "Mobile Recharge", "Internet", "Water & Gas"],
    "Home & Appliances": ["Appliances", "Repairs", "Furniture"],
    "Medical": ["Medicine", "Hospital", "Dental & Eye"],
    "Wedding & Functions": ["Marriage", "Pooja", "Birthday & Gifts"],
    "Clothing": [],
    "Gold & Jewellery": [],
    MISC: [],
}
DEFAULT_PERSONS = ["Amma", "Appa", "On-house", "Suji", "Kamali", "Guru"]


def _kw(*words: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])")


# (top-level, [(sub-category, keywords), ...], keywords of the whole group). First group that matches wins; inside it the
# first sub-category that matches wins (no sub-category when none does).
_RULES: list[tuple[str, list[tuple[Optional[str], re.Pattern]], re.Pattern]] = []


def _group(top: str, subs: list[tuple[Optional[str], tuple[str, ...]]], extra: tuple[str, ...] = ()) -> None:
    sub_rules = [(name, _kw(*kws)) for name, kws in subs]
    every = tuple(k for _n, kws in subs for k in kws) + extra
    _RULES.append((top, sub_rules, _kw(*every)))


_group("Wedding & Functions", [
    ("Marriage", ("my marriage", "kam marital", "marriage", "nalangu", "pandhal", "reception", "thattu seer", "mandabam", "moi", "function", "func", "virundhu", "welcome board", "decoration")),
    ("Pooja", ("pooja", "paatha pooja")),
])
_group("Medical", [
    ("Dental & Eye", ("dental", "eye glass", "eye", "glass")),
    ("Hospital", ("hospital", "doctor")),
    ("Medicine", ("medicine", "medical", "tablets")),
])
_group("Gold & Jewellery", [(None, ("gold", "jewel", "jewellery"))])
_group("Clothing", [(None, ("dress", "chappal", "cloth", "clothes", "shirt", "saree"))])
_group("Home & Appliances", [
    ("Appliances", ("ac", "tv purchase", "tv", "fridge", "washing machine", "inverter", "grinder", "cooker", "fan")),
    ("Furniture", ("bed", "cot", "table", "sofa")),
    ("Repairs", ("electrician", "electric fix", "bathroom", "plumber", "repair")),
], extra=("mosquito net", "mosquito", "utensils", "utensil", "amazon", "house hold", "toner", "photo", "god photo"))
_group("Bills & Recharge", [
    ("Mobile Recharge", ("recharge", "recharge.")),
    ("Electricity", ("current bill", "electric bill", "electricity bill", "electricity bil", "electricity", "eb bill", "eb")),
    ("Internet", ("net", "wifi", "broadband")),
    ("Water & Gas", ("water can", "cylinder", "gas")),
], extra=("bill", "security"))
_group("Wedding & Functions", [("Birthday & Gifts", ("gift", "bday", "birthday", "cake", "return gift"))])
_group("Groceries", [
    ("Fish & Chicken", ("fish", "chicken")),
    ("Mutton & Meat", ("mutton", "meat")),
    ("Milk & Eggs", ("milk", "egg", "eggs")),
    ("Vegetables & Fruit", ("veg", "vegies", "vegetables", "vegetable", "fruit", "fruits", "banana", "apple", "onion", "garlic", "coconut", "papaya")),
    ("Provisions", ("rice", "oil", "oils", "spice", "cerials", "cereals", "sugar", "flour")),
], extra=("groceries", "grocery", "grocer", "grovery"))
_group("Eating Out", [
    ("Liquor", ("liquor", "wine", "beer", "whisky", "whiskey", "brandy", "rum", "vodka", "tasmac", "bar", "alcohol")),
    ("Online Food", ("zomato", "zomota", "swiggy")),
    ("Snacks & Sweets", ("snacks", "icecream", "ice cream", "sweets")),
    ("Hotel & Tiffin", ("food", "biriyani", "tiffin", "restaurant", "restaurent", "outing", "saapadu", "hotel")),
])
_group("Vehicle & Fuel", [
    ("Petrol", ("petrol", "pertrol", "diesel", "fuel")),
    ("Service & Repair", ("tire", "tyre", "puncture", "bike service", "service", "phone rep")),
    ("Bus & Auto", ("bus", "bus fee", "auto", "auto fee", "transport", "cab", "travel")),
], extra=("car", "bike", "bajaj"))

_PEOPLE: list[tuple[str, re.Pattern]] = [
    ("On-house", _kw("family", "on-house", "on house", "onhouse", "house expense")),
    ("Appa", _kw("appa")),
    ("Amma", _kw("amma")),
    ("Suji", _kw("suji")),
    ("Kamali", _kw("kamali")),
    ("Guru", _kw("guru")),
]


def guess_person(notes: Optional[str]) -> Optional[str]:
    """The first person named in the notes (appa / amma / on-house (= family) / suji / kamali / guru), in the order they are written; None when nobody is."""
    text = (notes or "").lower()
    best: Optional[tuple[int, str]] = None
    for name, rx in _PEOPLE:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


def guess_category(notes: Optional[str]) -> tuple[str, Optional[str]]:
    """(top-level category, sub-category or None) read from the notes; (Miscellaneous, None) when nothing matches."""
    text = (notes or "").lower().strip()
    if not text:
        return MISC, None
    for top, subs, any_kw in _RULES:
        if not any_kw.search(text):
            continue
        for sub, rx in subs:
            if sub and rx.search(text):
                return top, sub
        return top, None
    return MISC, None
