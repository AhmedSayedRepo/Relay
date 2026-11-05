from relay.domain.review_trust import initial_resolution


def test_legacy_parser_fields_get_a_readable_shape() -> None:
    machine = {
        "parser_version": "instapay-v1",
        "fields": [
            {"field": "transaction_date", "value": "2026-10-27"},
            {"field": "sender_name", "value": None},
        ],
    }
    resolution = initial_resolution(machine, supported_receipt=True)
    for name in ("transaction_date", "sender_name"):
        field = resolution["fields"][name]
        assert field["state"] == "UNRESOLVED"
        assert field["reason"] == "legacy_parser_field"
        assert isinstance(field["candidates"], list)
    assert "transaction_date" not in resolution["blocking_fields"]
