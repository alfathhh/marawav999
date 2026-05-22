import re


YEAR_PATTERN = r"20\d{2}"
YEAR_RANGE_PATTERN = rf"({YEAR_PATTERN})\s*(?:-|sampai|sd|s/d)\s*({YEAR_PATTERN})"
QUARTER_LABELS = {
    1: "Triwulan I",
    2: "Triwulan II",
    3: "Triwulan III",
    4: "Triwulan IV",
}
QUARTER_ALIASES = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
}


def parse_years(text: str) -> list[str]:
    range_match = re.search(YEAR_RANGE_PATTERN, text.strip(), flags=re.IGNORECASE)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start > end:
            start, end = end, start
        return [str(year) for year in range(start, end + 1)]
    return re.findall(YEAR_PATTERN, text)


def parse_quarter_periods(text: str) -> list[str]:
    normalized = text.lower()
    if "semua triwulan" in normalized or "seluruh triwulan" in normalized:
        return [QUARTER_LABELS[number] for number in range(1, 5)]

    range_match = re.search(
        r"(?:triwulan|tw|q)\s*([1-4ivx]+)\s*(?:-|sampai|sd|s/d)\s*([1-4ivx]+)",
        normalized,
    )
    if range_match:
        start = quarter_number(range_match.group(1))
        end = quarter_number(range_match.group(2))
        if start and end:
            if start > end:
                start, end = end, start
            return [QUARTER_LABELS[number] for number in range(start, end + 1)]

    return [
        QUARTER_LABELS[number]
        for item in re.findall(r"(?:triwulan|tw|q)\s*([1-4ivx]+)", normalized)
        if (number := quarter_number(item))
    ]


def quarter_number(value: str) -> int | None:
    return QUARTER_ALIASES.get(value.strip().lower())
