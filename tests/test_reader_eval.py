from relay.infrastructure.reader_eval import compare, expected, grade, split_passes


def test_stored_text_splits_back_into_its_two_readings() -> None:
    block, sparse = "تم التحويل بنجاح\n550 cap", "550 cap\nالمبلغ المحول"
    stored = block + "\n" + sparse + "\n" + sparse
    assert split_passes(stored) == (block + "\n" + sparse, sparse)
    assert split_passes("one reading only") == ("one reading only", None)


def test_answers_come_only_from_confirmed_receipts() -> None:
    auto = {
        "auto_confirmed": {"rule": "sms_day", "trained_fields": ["amount"]},
        "fields": {
            "amount": {"value": "550.00"},
            "reference_number": {"value": "246685389859"},
        },
    }
    # The SMS proves the amount; the reference was not verified.
    assert expected(auto) == {"amount": "550.00"}
    operator = {
        "state": "REVIEW_FINALIZED",
        "fields": {
            "amount": {"value": "805.00", "trust": "OPERATOR_CONFIRMED"},
            "reference_number": {"value": "339581656980", "trust": "MACHINE_ONLY"},
        },
    }
    assert expected(operator) == {"amount": "805.00"}
    assert expected({"state": "NEEDS_REVIEW", "fields": {}}) is None


def test_bank_transfer_is_graded_right() -> None:
    sparse = (
        "تم التحويل بنجاح\nمعع 1,500.00\nYOUSSEF\nالرقم المرجعى للمعاملة\nFT26323HQ8RM"
    )
    stored = sparse + "\n" + sparse + "\n" + sparse
    grades = grade(stored, {"amount": "1500.00"}, ("YOUSSEF", "01003405232"))
    assert grades == {
        "amount": "ok",
        "reference": "skip",
        "kept": "ok",
        "recipient": "ok",
    }


def test_a_change_that_breaks_more_than_it_fixes_fails() -> None:
    ok = {"amount": "ok", "reference": "skip", "recipient": "ok", "kept": "ok"}
    miss = ok | {"amount": "miss"}
    wrong = ok | {"amount": "wrong"}
    assert compare({"a": miss, "b": ok}, {"a": ok, "b": ok})[0]
    assert not compare({"a": ok, "b": ok}, {"a": miss, "b": ok})[0]
    # Any newly wrong amount fails, even when another receipt was fixed.
    assert not compare({"a": miss, "b": ok}, {"a": ok, "b": wrong})[0]
    # New receipts with no earlier score never count as broken.
    assert compare({}, {"a": miss})[0]


def test_one_model_reading_grades_its_reference() -> None:
    text = "تم التحويل بنجاح\nYOUSSEF\n590.59 EGP\nالمرجع 885379009975"
    grades = grade(
        text,
        {"amount": "590.59", "reference": "885379009975"},
        ("YOUSSEF", "01003405232"),
        single=True,
    )
    assert grades["reference"] == "ok"
    assert grades["amount"] == "ok"


def test_helper_fills_what_the_current_reader_missed() -> None:
    from relay.infrastructure.ppocr_reader import VERSION
    from relay.infrastructure.reader_eval import HELPER, combined

    assert HELPER == VERSION
    current = {"amount": "miss", "reference": "ok", "recipient": "miss", "kept": "ok"}
    helper = {"amount": "ok", "reference": "miss", "recipient": "ok", "kept": "ok"}
    assert combined(current, helper) == {
        "amount": "ok",
        "reference": "ok",
        "recipient": "ok",
        "kept": "ok",
    }
