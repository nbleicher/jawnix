import importlib.util
import json
from pathlib import Path

import pytest


def load_shipper():
    path = Path(__file__).parents[2] / "worker" / "shipper.py"
    spec = importlib.util.spec_from_file_location("gms_test_shipper", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_phone_count_uses_nonempty_phone_field():
    shipper = load_shipper()
    rows = [
        ("key-1", None, None, "One", "+1 555 0101"),
        ("key-2", None, None, "Two", ""),
        ("key-3", None, None, "Three", None),
        ("key-4", None, None, "Four", "  555-0104  "),
    ]
    assert shipper.phone_count(rows) == 2
    assert "phone_count = EXCLUDED.phone_count" in shipper.RESULT_COUNT_SQL


def test_malformed_spool_file_is_rejected_with_valid_rows_preserved(
    tmp_path,
):
    shipper = load_shipper()
    spool = tmp_path / "results-42-100.ndjson"
    spool.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "job_id": 42,
                        "keyword": "roofers",
                        "title": "Good Roofing",
                        "phone": "6145550101",
                    }
                ),
                "{not-json",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(shipper.ParseFileError) as error:
        shipper.parse_file(spool)

    assert error.value.result.job_id == 42
    assert error.value.result.keyword == "roofers"
    assert error.value.result.result_count == 1
    assert error.value.result.bad_lines[0][0] == 2


def test_archiving_preserves_an_existing_file_with_the_same_name(
    tmp_path,
):
    shipper = load_shipper()
    spool = tmp_path / "spool"
    archive = tmp_path / "archive"
    spool.mkdir()
    archive.mkdir()
    data = spool / "results-42-100.ndjson"
    marker = spool / "results-42-100.ndjson.done"
    data.write_text("new", encoding="utf-8")
    marker.write_text("", encoding="utf-8")
    (archive / data.name).write_text("existing", encoding="utf-8")

    shipper.move_pair(data, marker, archive)

    archived_data = list(
        path
        for path in archive.glob("results-42-100.ndjson*")
        if ".done" not in path.name
    )
    assert sorted(path.read_text() for path in archived_data) == [
        "existing",
        "new",
    ]
