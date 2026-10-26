import hashlib
import json

import pytest

from tools.evidence_validation.real_negatives import load_real_negatives


def test_reviewed_real_negative_manifest_does_not_change_frozen_set(tmp_path):
    image = tmp_path / "real.png"
    image.write_bytes(b"reviewed-original-bytes")
    manifest = tmp_path / "real.json"
    value = {
        "sample_id": "R01",
        "source_filename": "real.png",
        "synthetic": False,
        "expected_receipt": False,
        "source_integrity": {"sha256": hashlib.sha256(image.read_bytes()).hexdigest()},
    }
    manifest.write_text(json.dumps({"samples": [value]}))
    assert load_real_negatives(manifest)[0].source == image
    value["synthetic"] = True
    manifest.write_text(json.dumps({"samples": [value]}))
    with pytest.raises(ValueError):
        load_real_negatives(manifest)
    value["synthetic"] = False
    value["source_integrity"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps({"samples": [value]}))
    with pytest.raises(ValueError):
        load_real_negatives(manifest)
