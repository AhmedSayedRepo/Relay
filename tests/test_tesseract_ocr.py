import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from relay.domain.processing import (
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    PreprocessedImage,
)
from relay.infrastructure.tesseract_ocr import TesseractOcrProvider

FIXTURES = Path(__file__).parent / "fixtures" / "tesseract"
TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\t"
    "width\theight\tconf\ttext\n"
)


def write_word_tsv(path: Path, logical_lines: list[list[str]]) -> None:
    rows = [TSV_HEADER]
    for line_number, words in enumerate(logical_lines, start=1):
        for word_number, word in enumerate(words, start=1):
            left = 10 + ((word_number - 1) * 80)
            rows.append(
                "\t".join(
                    (
                        "5",
                        "1",
                        "1",
                        "1",
                        str(line_number),
                        str(word_number),
                        str(left),
                        str(20 * line_number),
                        "70",
                        "16",
                        "95.000000",
                        word,
                    )
                )
                + "\n"
            )
    path.write_text("".join(rows), encoding="utf-8", newline="")


def test_tsv_adapter_preserves_english_text_values_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "english.tsv"
    expected = (
        "Transfer Successful",
        "Amount 790 EGP",
        "Date 29 Aug 2026",
        "Time 04:30 PM",
        "Reference 123456789012",
        "pilot.user@instapay",
    )
    write_word_tsv(path, [line.split() for line in expected])

    lines = TesseractOcrProvider._parse_tsv(path)

    assert tuple(line.text for line in lines) == expected
    assert all(line.confidence == pytest.approx(0.95) for line in lines)
    assert lines[0].bounding_box == (10, 20, 150, 16)


def test_tsv_adapter_preserves_arabic_text_values_and_order(tmp_path: Path) -> None:
    path = tmp_path / "arabic.tsv"
    expected = (
        "تم التحويل بنجاح",
        "المبلغ ٧٩٠ ج.م",
        "التاريخ ٢٩ أغسطس ٢٠٢٦",
        "الوقت ٠٤:٣٠ م",
        "رقم المرجع ١٢٣٤٥٦٧٨٩٠١٢",
    )
    write_word_tsv(path, [line.split() for line in expected])

    lines = TesseractOcrProvider._parse_tsv(path)

    assert tuple(line.text for line in lines) == expected
    assert all(line.confidence == pytest.approx(0.95) for line in lines)


def test_p01_mixed_bidi_tsv_has_no_metadata_leakage() -> None:
    lines = TesseractOcrProvider._parse_tsv(FIXTURES / "p01_mixed_ar_en.tsv")
    expected = (
        "تم التحويل بنجاح",
        "EGP\u200f 790",
        "pilot.user@instapay [=]\u200f",
        "انستاباي",
        'PILOT RECIPIENT ١ مستلم تجريبي "أحمد\u200e',
        "123456789012",
        "29 Aug 2026 04:30 PM",
        "اختبار تجريبي\u200e",
        "POWERED BY",
    )

    assert tuple(line.text for line in lines) == expected
    assert len(lines) == 9
    assert lines[5].confidence == pytest.approx(0.96660675)
    assert lines[5].bounding_box == (105, 1164, 230, 24)
    for line in lines:
        assert "\t" not in line.text
        assert " 5 1 1 " not in line.text
        assert len(line.text) < 100


def test_tsv_adapter_preserves_word_confidence_inside_a_noisy_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "noisy-reference.tsv"
    path.write_text(
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t10\t20\t150\t20\t96.000000\t123456789012\n"
        + "5\t1\t1\t1\t1\t2\t170\t20\t60\t20\t20.000000\tnoise\n",
        encoding="utf-8",
        newline="",
    )

    (line,) = TesseractOcrProvider._parse_tsv(path)

    assert line.text == "123456789012 noise"
    assert line.confidence == pytest.approx(0.58)
    assert tuple(token.text for token in line.tokens) == ("123456789012", "noise")
    assert line.tokens[0].confidence == pytest.approx(0.96)
    assert line.tokens[0].bounding_box == (10, 20, 150, 20)
    assert line.tokens[1].confidence == pytest.approx(0.20)
    assert line.as_json()["tokens"] == [
        {
            "text": "123456789012",
            "confidence": 0.96,
            "bounding_box": [10, 20, 150, 20],
        },
        {
            "text": "noise",
            "confidence": 0.20,
            "bounding_box": [170, 20, 60, 20],
        },
    ]


def test_secondary_line_provenance_is_separate_from_primary_text() -> None:
    primary = OcrLine("-—- Pr", 0.15, (578, 344, 125, 20))
    secondary = OcrLine(
        "790 EGP",
        0.85,
        (373, 322, 338, 78),
        (
            OcrToken("790", 0.94, (373, 322, 178, 77)),
            OcrToken("EGP", 0.76, (577, 343, 133, 55)),
        ),
        origin=OcrLineOrigin.SECONDARY,
        ocr_mode="line_crop_psm7",
        source_region=(357, 314, 370, 94),
        source_line_index=0,
        parser_eligible=True,
    )
    document = OcrDocument(
        "tesseract",
        "test",
        ("eng", "ara"),
        ("eng", "ara"),
        primary.text,
        (primary,),
        primary.confidence,
        1,
        secondary_lines=(secondary,),
    )

    assert document.normalized_text == "-—- Pr"
    assert document.lines == (primary,)
    assert document.parser_lines == (secondary, primary)
    assert secondary.as_json() == {
        "text": "790 EGP",
        "confidence": 0.85,
        "bounding_box": [373, 322, 338, 78],
        "tokens": [
            {
                "text": "790",
                "confidence": 0.94,
                "bounding_box": [373, 322, 178, 77],
            },
            {
                "text": "EGP",
                "confidence": 0.76,
                "bounding_box": [577, 343, 133, 55],
            },
        ],
        "origin": "SECONDARY",
        "ocr_mode": "line_crop_psm7",
        "source_region": [357, 314, 370, 94],
        "source_line_index": 0,
        "parser_eligible": True,
    }


def _fragmented_line_image() -> PreprocessedImage:
    image = Image.new("L", (1080, 600), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((373, 322, 550, 399), fill=0)
    draw.rectangle((578, 343, 710, 398), fill=90)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return PreprocessedImage(output.getvalue(), "image/png", 1080, 600, ())


def test_secondary_regions_expand_fragment_geometry_and_are_bounded() -> None:
    image = _fragmented_line_image()
    primary_lines = tuple(
        OcrLine("noise", 0.15, (578, 344 + (index * 45), 125, 20)) for index in range(8)
    )

    regions = TesseractOcrProvider._secondary_regions(image, primary_lines)

    assert 1 <= len(regions) <= 4
    assert regions[0].source_line_index == 0
    left, top, width, height = regions[0].bounding_box
    assert left <= 373 < left + width
    assert left <= 710 < left + width
    assert top <= 322 < top + height
    assert top <= 399 < top + height
    assert width <= 1_600
    assert height <= 256
    assert width * height <= 512_000


def test_high_confidence_p01_amount_line_is_not_reprocessed() -> None:
    image = _fragmented_line_image()
    primary_lines = (OcrLine("EGP 790", 0.7067, (373, 322, 338, 78)),)

    assert TesseractOcrProvider._secondary_regions(image, primary_lines) == ()


def test_secondary_region_rejects_oversized_source_fragment() -> None:
    raster = Image.new("L", (2400, 2400), 255)

    assert (
        TesseractOcrProvider._expand_fragmented_band(raster, (100, 100, 1700, 300))
        is None
    )


@pytest.mark.asyncio
async def test_secondary_psm7_recovers_fragment_without_changing_primary_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = object.__new__(TesseractOcrProvider)
    provider.executable = Path("fixed-tesseract")
    provider.tessdata_dir = Path("fixed-tessdata")
    provider._available_languages = ("ara", "eng")
    provider._provider_version = "test"
    calls: list[int] = []

    def run_tesseract(
        source: Path,
        output_base: Path,
        requested_languages: tuple[str, ...],
        psm: int,
        deadline: float,
    ) -> tuple[OcrLine, ...]:
        del source, output_base, requested_languages, deadline
        calls.append(psm)
        if psm == 6:
            return (OcrLine("-—- Pr", 0.15, (578, 344, 125, 20)),)
        return (
            OcrLine(
                "790 EGP",
                0.85,
                (16, 8, 338, 78),
                (
                    OcrToken("790", 0.94, (16, 8, 178, 77)),
                    OcrToken("EGP", 0.76, (220, 29, 133, 55)),
                ),
            ),
        )

    monkeypatch.setattr(provider, "_run_tesseract", run_tesseract)

    document = await provider.extract(_fragmented_line_image(), ("eng", "ara"), 20)

    assert calls == [6, 7]
    assert document.normalized_text == "-—- Pr"
    assert tuple(line.text for line in document.lines) == ("-—- Pr",)
    assert tuple(line.text for line in document.secondary_lines) == ("790 EGP",)
    recovered = document.secondary_lines[0]
    assert recovered.origin == OcrLineOrigin.SECONDARY
    assert recovered.ocr_mode == "line_crop_psm7"
    assert recovered.source_region is not None
    assert recovered.source_line_index == 0
    assert recovered.parser_eligible is True
    assert recovered.bounding_box is not None
    assert recovered.bounding_box[0] >= recovered.source_region[0]


@pytest.mark.parametrize(
    ("text", "tokens", "eligible"),
    (
        ("EGP 790", (("EGP", 0.91), ("790", 0.94)), True),
        ("790 EGP", (("790", 0.94), ("EGP", 0.76)), True),
        ("٧٩٠ ج.م", (("٧٩٠", 0.91), ("ج.م", 0.90)), True),
        ("790", (("790", 0.94),), False),
        ("EGP", (("EGP", 0.94),), False),
        ("128895843616 EGP", (("128895843616", 0.96), ("EGP", 0.91)), False),
        ("01003405232 EGP", (("01003405232", 0.96), ("EGP", 0.91)), False),
        ("29/08/2026 EGP", (("29/08/2026", 0.96), ("EGP", 0.91)), False),
        ("04:30 EGP", (("04:30", 0.96), ("EGP", 0.91)), False),
        ("790 EGP", (("790", 0.94), ("EGP", 0.69)), False),
    ),
)
def test_secondary_amount_pair_guard(
    text: str, tokens: tuple[tuple[str, float], ...], eligible: bool
) -> None:
    line = OcrLine(
        text,
        sum(confidence for _, confidence in tokens) / len(tokens),
        (0, 0, 200, 30),
        tuple(
            OcrToken(token, confidence, (index * 80, 0, 70, 30))
            for index, (token, confidence) in enumerate(tokens)
        ),
    )

    assert TesseractOcrProvider._secondary_line_is_parser_eligible(line) is eligible
