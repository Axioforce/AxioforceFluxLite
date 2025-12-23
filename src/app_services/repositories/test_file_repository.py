from __future__ import annotations
from typing import Dict, List, Tuple

from .csv_transform_repository import CsvTransformRepository
from .discrete_temp_repository import DiscreteTempRepository
from .temperature_test_repository import TemperatureTestRepository

class TestFileRepository:
    """
    Handles file system operations for test data (listing, reading, metadata).
    """

    def __init__(self) -> None:
        self._temp = TemperatureTestRepository()
        self._discrete = DiscreteTempRepository()
        self._csv = CsvTransformRepository()

    def list_temperature_tests(self, device_id: str) -> List[str]:
        return self._temp.list_temperature_tests(device_id)

    def list_temperature_devices(self) -> List[str]:
        return self._temp.list_temperature_devices()

    def list_discrete_tests(self) -> List[Tuple[str, str, str]]:
        return self._discrete.list_discrete_tests()

    def get_temperature_test_details(self, csv_path: str) -> Dict[str, object]:
        return self._temp.get_temperature_test_details(csv_path)

    def analyze_discrete_temp_csv(self, csv_path: str) -> Tuple[bool, List[float]]:
        return self._discrete.analyze_discrete_temp_csv(csv_path)

    def downsample_csv_to_50hz(self, source_csv: str, dest_csv: str) -> str:
        return self._csv.downsample_csv_to_50hz(source_csv, dest_csv)

    def derive_temperature_paths(self, raw_csv: str, device_id: str, mode: str = "legacy") -> Dict[str, str]:
        return self._temp.derive_temperature_paths(raw_csv, device_id, mode)

    def update_meta_with_processed(
        self,
        meta_path: str,
        trimmed_csv: str,
        processed_off: str,
        processed_on: str,
        slopes: dict,
        mode: str = "legacy",
    ) -> None:
        self._temp.update_meta_with_processed(meta_path, trimmed_csv, processed_off, processed_on, slopes, mode)

    def format_slopes_label(self, slopes: dict, mode: str = "legacy") -> str:
        return self._temp.format_slopes_label(slopes, mode=mode)

    def formatted_slope_name(self, slopes: dict) -> str:
        return self._temp.formatted_slope_name(slopes)

    def normalize_slopes(self, slopes: dict) -> Dict[str, float]:
        return self._temp.normalize_slopes(slopes)

