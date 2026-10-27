"""SAM output parsing and mask decoding, with payloads made by Meta's reference encoder (no API calls)."""
import json

import numpy as np
import pytest

from hephaestus import sam

codec = pytest.importorskip("meta_sam_parser._mask_codec")


def encoded(raster):
    h, w = raster.shape
    return codec._encode_raster(bytes(raster.astype(np.uint8).ravel()), w, h)


def test_parse_and_place_masks():
    a = np.zeros((20, 31), bool)
    a[5:15, 3:28] = True
    a[8:12, 10:14] = False                       # a hole
    b = np.ones((4, 6), bool)
    line = (f"<0f>0<|box;x1=100;y1=40;x2=130;y2=59;w=320;h=200|><|mask;x=0;y=0;data=20,31,{encoded(a)}|>,"
            f"7<|box;x1=0;y1=0;x2=5;y2=3;w=320;h=200;c=0.875|><|mask;x=0;y=0;c=0.875;data=4,6,{encoded(b)}|>")
    recs = sam.parse(line)
    assert [r["id"] for r in recs] == [0, 7] and recs[1]["conf"] == 0.875
    assert recs[0]["box"] == (100, 40, 130, 59) and recs[0]["mask_hw"] == (20, 31) and recs[0]["encoding"] == "one_bit"
    assert np.array_equal(sam.decode(recs[0]), a)
    full = sam.full_mask(recs[0])
    assert full.shape == (200, 320) and full.sum() == a.sum()
    assert np.array_equal(full[40:60, 100:131], a)      # inclusive box: 31 columns, 20 rows
    assert not full[48:52, 110:114].any()


def test_empty_output_is_no_records():
    assert sam.parse("") == [] and sam.parse(None) == []


def test_object_prompt_tokens():
    p = sam.object_prompt([{"id": 1, "box": (10, 20, 50, 60), "points": [(30, 40)], "negative": [(12, 22)]},
                           {"id": 2, "points": [(5, 5)]}], 320, 200)
    assert p == ("<0f>1<|box;x1=10;y1=20;x2=50;y2=60;w=320;h=200|><|point;x=30;y=40;w=320;h=200|>"
                 "-<|point;x=12;y=22;w=320;h=200|>,2<|point;x=5;y=5;w=320;h=200|>")
    with pytest.raises(ValueError):
        sam.object_prompt([{"id": i, "points": [(1, 1)]} for i in range(17)], 10, 10)


def test_cache_is_used_and_offline_miss_raises(tmp_path):
    img = b"\x89PNG fake bytes"
    a = np.ones((2, 2), bool)
    text = f"<0f>0<|box;x1=0;y1=0;x2=1;y2=1;w=4;h=4|><|mask;x=0;y=0;data=2,2,{encoded(a)}|>"
    key = sam._key(img, "sheet of paper", None)
    (tmp_path / f"{key}.json").write_text(json.dumps({"prompt": "sheet of paper", "text": text, "usage": {},
                                                      "seconds": 1.0, "image_size": [4, 4]}))
    out = sam.segment(img, "sheet of paper", cache_dir=str(tmp_path), offline=True)
    assert out["cached"] and out["cost_usd"] == 0 and out["records"][0]["box"] == (0, 0, 1, 1)
    with pytest.raises(sam.SamError):
        sam.segment(img, "a different prompt", cache_dir=str(tmp_path), offline=True)
