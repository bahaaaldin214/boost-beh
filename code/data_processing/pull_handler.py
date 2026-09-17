import io
import os
import re
import sys
import zipfile
from io import BytesIO

import pandas as pd
import requests
from termcolor import cprint

_STUDY_RESULT_DIR = re.compile(r"(?:^|/)study_result_(\d+)(?:/|$)")


class Pull:
    def __init__(
        self,
        taskIds,
        tease,
        token,
        taskName,
        proxy=True,
        base_url: str | None = None,
    ):
        if not isinstance(taskIds, list):
            raise ValueError(
                "task IDs is not a valid list, must be of type list "
                "(e.g. [123, 123, 123, ..., 123])"
            )
            sys.exit()
        cleaned = [int(x) for x in taskIds if x is not None]
        if not cleaned:
            raise ValueError("task IDs list is empty")
        if len(cleaned) > 6:
            raise ValueError(f"Expected at most 6 studyIds, got {len(cleaned)}")
        self.IDs = cleaned
        self.tease = tease
        self.token = token
        self.taskName = taskName
        self.proxy = proxy
        self.base_url = (base_url or os.environ.get(
            "JATOS_BASE_URL", "https://pbsjatos.psychology.uiowa.edu"
        )).rstrip("/")

    def load(self, days_ago=1):
        from datetime import datetime, timedelta

        proxies = {
            "http": f"http://zjgilliam:{self.tease}@proxy.divms.uiowa.edu:8888",
            "https": f"http://zjgilliam:{self.tease}@proxy.divms.uiowa.edu:8888",
        }

        url = f"{self.base_url}/jatos/api/v1/results/metadata"
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data = {"studyIds": self.IDs}

        cutoff_time = (
            datetime.now() - timedelta(days=days_ago)
        ).timestamp() * 1000

        try:
            cprint(
                f"requesting data from Jatos ({self.base_url}) "
                f"task={self.taskName} studies={self.IDs} days_ago={days_ago}...",
                "green",
            )
            if self.proxy:
                response = requests.post(
                    url, headers=headers, json=data, proxies=proxies, timeout=120
                )
            else:
                response = requests.post(
                    url, headers=headers, json=data, timeout=120
                )
            response.raise_for_status()
            response_json = response.json()
        except requests.RequestException as e:
            cprint(f"Error during API request: {e}", "red")
            return []

        study_result_ids = [
            study_result["id"]
            for study in response_json.get("data", [])
            for study_result in study.get("studyResults", [])
            if study_result["studyState"] == "FINISHED"
            and study_result["endDate"] >= cutoff_time
        ]
        cprint(
            f"  FINISHED results in window: {len(study_result_ids)} "
            f"(task={self.taskName}, host={self.base_url})",
            "cyan",
        )
        if not study_result_ids:
            return []
        return self.return_data(study_result_ids)

    def return_data(self, study_result_ids):
        proxies = {
            "http": f"http://zjgilliam:{self.tease}@proxy.divms.uiowa.edu:8888",
            "https": f"http://zjgilliam:{self.tease}@proxy.divms.uiowa.edu:8888",
        }
        headers = {
            "accept": "application/octet-stream",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data = {
            "studyIds": self.IDs,
            "studyResultIds": study_result_ids,
        }
        url = f"{self.base_url}/jatos/api/v1/results/data"

        try:
            if self.proxy:
                response = requests.post(
                    url, headers=headers, json=data, proxies=proxies, timeout=300
                )
            else:
                response = requests.post(
                    url, headers=headers, json=data, timeout=300
                )
            response.raise_for_status()
        except requests.RequestException as e:
            cprint(f"Error fetching result data zip: {e}", "red")
            return []

        if not zipfile.is_zipfile(BytesIO(response.content)):
            return []

        return self._extract_txt_files(response.content, study_result_ids)

    def _extract_txt_files(self, zip_content, study_result_ids):
        wanted = {str(sid) for sid in study_result_ids}
        data_frames = []

        with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zip_ref:
            for zip_info in zip_ref.infolist():
                m = _STUDY_RESULT_DIR.search(zip_info.filename.replace("\\", "/"))
                if not m or m.group(1) not in wanted:
                    continue
                if not zip_info.filename.endswith(".txt"):
                    continue
                with zip_ref.open(zip_info) as file:
                    file_data = file.read().decode("utf-8")
                    data_frames.append(
                        pd.DataFrame({"file_content": [file_data]})
                    )

        cprint(
            f"  extracted {len(data_frames)} txt payloads "
            f"(task={self.taskName}, host={self.base_url})",
            "cyan",
        )
        return data_frames
