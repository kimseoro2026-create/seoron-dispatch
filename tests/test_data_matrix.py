"""이동시간 행렬 파일 찾기 — 압축본(.csv.gz) 대체 경로."""
import pandas as pd

from planner.data import load_travel, matrix_path

ROWS = pd.DataFrame({
    "from_node": ["A", "B"], "to_node": ["B", "A"],
    "minutes": [7.5, 8.0], "source": ["osrm", "osrm"],
})


def test_matrix_path_prefers_plain_csv(tmp_path):
    ROWS.to_csv(tmp_path / "travel_matrix.csv", index=False)
    ROWS.to_csv(tmp_path / "travel_matrix.csv.gz", index=False)
    assert matrix_path(tmp_path).name == "travel_matrix.csv"


def test_matrix_path_falls_back_to_gz(tmp_path):
    ROWS.to_csv(tmp_path / "travel_matrix.csv.gz", index=False)
    assert matrix_path(tmp_path).name == "travel_matrix.csv.gz"


def test_matrix_path_none_when_missing(tmp_path):
    assert matrix_path(tmp_path) is None


def test_load_travel_reads_gz(tmp_path):
    ROWS.to_csv(tmp_path / "travel_matrix.csv.gz", index=False)
    t = load_travel(tmp_path)
    assert t == {("A", "B"): 7.5, ("B", "A"): 8.0}
