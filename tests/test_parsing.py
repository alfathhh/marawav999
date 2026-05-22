from app.conversation.parsing import parse_quarter_periods, parse_years


def test_parse_years_supports_single_and_range():
    assert parse_years("data 2023") == ["2023"]
    assert parse_years("data 2021-2023") == ["2021", "2022", "2023"]
    assert parse_years("data 2023 sampai 2021") == ["2021", "2022", "2023"]


def test_parse_quarter_periods_supports_aliases_and_range():
    assert parse_quarter_periods("2024 triwulan 1") == ["Triwulan I"]
    assert parse_quarter_periods("2024 TW 1-3") == ["Triwulan I", "Triwulan II", "Triwulan III"]
    assert parse_quarter_periods("semua triwulan") == ["Triwulan I", "Triwulan II", "Triwulan III", "Triwulan IV"]
