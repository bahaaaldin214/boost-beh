import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "code") not in sys.path:
    sys.path.insert(0, str(ROOT / "code"))

from data_processing.pull_handler import Pull, _STUDY_RESULT_DIR


def test_study_result_dir_regex():
    m9 = _STUDY_RESULT_DIR.search("study_result_9/comp-result_1/data.txt")
    assert m9 and m9.group(1) == "9"
    m19 = _STUDY_RESULT_DIR.search("study_result_19/comp-result_2/data.txt")
    assert m19 and m19.group(1) == "19"


def test_extract_txt_files_exact_result_id():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("study_result_9/comp-result_1/data.txt", '{"data":[]}')
        zf.writestr("study_result_19/comp-result_2/data.txt", '{"data":[]}')
        zf.writestr("study_result_90/comp-result_3/data.txt", '{"data":[]}')
    pull = Pull([1], tease="", token="x", taskName="AF", proxy=False)
    frames = pull._extract_txt_files(buf.getvalue(), [9])
    assert len(frames) == 1
    assert isinstance(frames[0], pd.DataFrame)
