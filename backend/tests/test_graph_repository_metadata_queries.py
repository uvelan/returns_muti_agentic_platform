from pathlib import Path


def test_configuration_release_queries_do_not_project_missing_property_directly() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "return_platform"
        / "configuration"
        / "graph_repository.py"
    ).read_text(encoding="utf-8")

    assert "r.metadata_json AS metadata_json" not in source
    assert "coalesce(properties(r)['metadata_json'], '{}')" in source
    assert "r.metadata_json = '{}'" in source
