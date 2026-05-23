"""WhatsApp-friendly formatting for BPS data output.

WhatsApp supports limited formatting:
- *bold* for emphasis
- _italic_ for secondary info
- ~strikethrough~
- ```monospace``` for single-line code
- No markdown tables, no code blocks with syntax highlighting

This module provides clean, readable formatting optimized for mobile WhatsApp.
"""

from typing import Any


# ─── Emoji constants for visual structure ───────────────────────────────────

ICON_TABLE = "\U0001f4ca"       # 📊
ICON_BOOK = "\U0001f4d6"        # 📖
ICON_LINK = "\U0001f517"        # 🔗
ICON_CALENDAR = "\U0001f4c5"    # 📅
ICON_PIN = "\U0001f4cc"         # 📌
ICON_CHECK = "\u2705"           # ✅
ICON_RIGHT = "\u25b6"           # ▶
ICON_DOT = "\u2022"             # •
ICON_DIVIDER = "\u2500" * 20    # ────────────────────


def format_dynamic_table(
    title: str,
    unit: str,
    row_labels: list[str],
    table: dict[str, list[str]],
    missing_years: list[str] | None = None,
) -> str:
    """Format dynamic table data for WhatsApp display.

    Instead of ASCII pipe-tables, uses a card-style layout:
    - Each row gets its own line with bold label
    - Year values listed clearly per row
    """
    year_labels = list(table.keys())
    max_len = max((len(values) for values in table.values()), default=0)

    if not row_labels or len(row_labels) != max_len:
        row_labels = [f"Rincian {index}" for index in range(1, max_len + 1)]

    lines: list[str] = []

    # Header
    lines.append(f"{ICON_TABLE} *{title}*")
    lines.append(f"_Satuan: {unit}_")
    lines.append("")

    # Check if it's a simple table (few rows, few columns)
    is_compact = len(row_labels) <= 8 and len(year_labels) <= 4

    if is_compact and len(year_labels) > 1:
        # Multi-year compact: row-by-row with all years
        for index, label in enumerate(row_labels):
            lines.append(f"{ICON_DOT} *{label}*")
            for year in year_labels:
                values = table.get(year, [])
                value = values[index] if index < len(values) else "-"
                lines.append(f"    {year}: {value}")
            lines.append("")
    elif is_compact and len(year_labels) == 1:
        # Single year: simple list
        year = year_labels[0]
        lines.append(f"{ICON_CALENDAR} *Tahun {year}*")
        lines.append("")
        for index, label in enumerate(row_labels):
            values = table.get(year, [])
            value = values[index] if index < len(values) else "-"
            lines.append(f"  {ICON_DOT} {label}: *{value}*")
        lines.append("")
    else:
        # Large table: year-by-year sections
        for year in year_labels:
            values = table.get(year, [])
            lines.append(f"{ICON_CALENDAR} *Tahun {year}*")
            for index, label in enumerate(row_labels):
                value = values[index] if index < len(values) else "-"
                lines.append(f"  {ICON_DOT} {label}: {value}")
            lines.append("")

    # Footer
    lines.append(f"_Sumber: BPS Kab. Padang Pariaman via WebAPI_")

    if missing_years:
        lines.append("")
        lines.append(f"{ICON_PIN} _Catatan: data tahun {', '.join(missing_years)} belum tersedia di WebAPI BPS._")

    return "\n".join(lines).strip()


def format_matrix_table(
    title: str,
    matrix: list[list[str]],
    intro: str,
    source: str,
    note: str = "",
) -> str:
    """Format a matrix (SIMDASI) table for WhatsApp display.

    Uses header row as bold labels, then each data row as a card.
    """
    normalized_rows = [
        [str(cell) for cell in row]
        for row in matrix
        if any(str(cell).strip() for cell in row)
    ]
    if not normalized_rows:
        return f"{intro}\n\n{ICON_TABLE} *{title}*\n\n_Sumber: {source}_"

    header = normalized_rows[0]
    data_rows = normalized_rows[1:]

    lines: list[str] = []
    lines.append(intro)
    lines.append("")
    lines.append(f"{ICON_TABLE} *{title}*")

    if note:
        lines.append(f"_{note}_")
    lines.append("")

    # If table is small enough, show as labeled cards
    if len(header) <= 5 and len(data_rows) <= 15:
        for row_index, row in enumerate(data_rows, start=1):
            row_parts: list[str] = []
            for col_index, cell in enumerate(row):
                if col_index < len(header):
                    col_name = header[col_index]
                    row_parts.append(f"{col_name}: *{cell}*")
                else:
                    row_parts.append(f"*{cell}*")
            lines.append(f"{row_index}. {' | '.join(row_parts)}")
        lines.append("")
    else:
        # Large matrix: show rows with first column as key
        for row in data_rows:
            if not row:
                continue
            key = row[0]
            lines.append(f"{ICON_DOT} *{key}*")
            for col_index in range(1, len(row)):
                col_name = header[col_index] if col_index < len(header) else f"Kolom {col_index}"
                lines.append(f"    {col_name}: {row[col_index]}")
            lines.append("")

    lines.append(f"_Sumber: {source}_")
    return "\n".join(lines).strip()


def format_publication(
    title: str,
    release_date: str,
    abstract: str = "",
    source_url: str | None = None,
    year_note: str = "",
) -> str:
    """Format a publication result for WhatsApp display."""
    lines: list[str] = []

    lines.append(f"{ICON_BOOK} *{title}*")
    lines.append("")

    if year_note:
        lines.append(f"{ICON_CALENDAR} {year_note}")

    lines.append(f"{ICON_CALENDAR} Tanggal rilis: {release_date}")

    if abstract:
        lines.append("")
        lines.append(f"{ICON_PIN} *Ringkasan:*")
        # Trim long abstracts for WhatsApp readability
        clean_abstract = str(abstract).strip()
        if len(clean_abstract) > 500:
            clean_abstract = clean_abstract[:497] + "..."
        lines.append(clean_abstract)

    if source_url:
        lines.append("")
        lines.append(f"{ICON_LINK} {source_url}")

    lines.append("")
    lines.append(f"_Sumber: BPS Kab. Padang Pariaman via WebAPI_")

    return "\n".join(lines).strip()


def format_data_options(
    source_groups: dict[str, list[dict[str, Any]]],
    source_pages: dict[str, int],
    page_size: int = 5,
    source_order: tuple[str, ...] = ("dynamic_table", "simdasi", "publication"),
    source_labels: dict[str, str] | None = None,
) -> tuple[str, list[dict], str]:
    """Format data option choices for WhatsApp display.

    Returns:
        (options_message, visible_choices, guidance_message)
    """
    if source_labels is None:
        source_labels = {
            "dynamic_table": f"{ICON_TABLE} *Tabel Dinamis*",
            "simdasi": f"{ICON_TABLE} *SIMDASI*",
            "publication": f"{ICON_BOOK} *Publikasi*",
        }

    visible_choices: list[dict] = []
    result_lines: list[str] = []
    result_lines.append(f"🔍 Saya temukan beberapa data yang mungkin cocok:")
    result_lines.append("")

    choice_number = 1
    has_next = False

    for source in source_order:
        items = source_groups.get(source, [])
        if not items:
            continue
        page = source_pages.get(source, 0)
        start = page * page_size
        end = start + page_size
        visible = items[start:end]
        if not visible:
            continue

        if (page + 1) * page_size < len(items):
            has_next = True

        result_lines.append(source_labels.get(source, source))
        result_lines.append("")
        for item in visible:
            visible_choices.append(item)
            item_title = str(item.get("title") or item.get("label") or item.get("name") or "-").strip()
            result_lines.append(f"  {choice_number}. {item_title}")
            choice_number += 1
        result_lines.append("")

    # Guidance
    guidance_lines: list[str] = []
    guidance_lines.append("👉 Ketik *nomor* untuk memilih.")
    if has_next:
        guidance_lines.append("👉 Ketik *lainnya* untuk hasil berikutnya.")
    guidance_lines.append("")
    guidance_lines.append("Atau tulis kata kunci baru yang lebih spesifik.")
    guidance_lines.append("🔙 _batal_ = kembali | _menu_ = menu utama")

    return (
        "\n".join(result_lines).strip(),
        visible_choices,
        "\n".join(guidance_lines).strip(),
    )
