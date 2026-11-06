from datetime import date

from relay.infrastructure.donut_reader import parse_fields, plausible, to_canonical


def test_reading_is_converted_and_checked() -> None:
    text = (
        '{"amount":"1,165","currency":"EGP","reference":"01002625754",'
        '"date":"27 Oct 2026","time":"04:36 PM"'
    )
    raw = parse_fields(text, ("amount", "currency", "reference", "date", "time"))
    assert to_canonical("amount", raw["amount"]) == "1165.00"
    assert to_canonical("time", raw["time"]) == "16:36:00"
    assert to_canonical("date", raw["date"]) == "2026-10-27"
    # A phone number is not a reference; a broken date is dropped.
    assert plausible("reference_number", raw["reference"]) is None
    assert plausible("reference_number", "455106702850") == "455106702850"
    assert plausible("reference_number", "FT26298YKLCP") == "FT26298YKLCP"
    assert to_canonical("date", ",") is None
    assert plausible("date", "2031-01-01", today=date(2026, 11, 7)) is None
    assert plausible("amount", "0.00") is None
