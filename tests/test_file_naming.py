import os
import re

from gas_server.core.file_naming import build_output_filename, build_output_path, safe_output_stem


def test_safe_output_stem_uses_clean_words_only():
    assert safe_output_stem("Map flood-risk in Centre County!") == "map_flood"


def test_safe_output_stem_uses_fallback_for_empty_text():
    assert safe_output_stem("", fallback="dataset") == "dataset"


def test_build_output_filename_adds_six_digit_suffix_and_extension():
    filename = build_output_filename("Join parcels to schools", extension="geojson")

    assert re.fullmatch(r"join_parcels_\d{6}\.geojson", filename)


def test_build_output_path_creates_directory(tmp_path):
    output_path = build_output_path(
        str(tmp_path / "nested"),
        "Render map",
        extension=".png",
    )

    assert os.path.isdir(tmp_path / "nested")
    assert re.search(r"render_map_\d{6}\.png$", output_path)

