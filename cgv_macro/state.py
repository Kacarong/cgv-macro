"""이전 상태 저장/비교 — 동일 상태 중복 알림 방지."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("cgv_macro")


class StateStore:
    """
    상태 구조(파일: state.json):
    {
      "<target_key>": {
        "<showtime_key>": {"status": "soldout|available|open", "remaining": 0, "notified": ["available"]}
      }
    }
    showtime_key 는 회차 단위(예: "1900|IMAX|9관") 로 구성한다.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning("상태 파일 로드 실패(%s) — 새로 시작합니다.", e)
                self.data = {}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            logger.error("상태 파일 저장 실패: %s", e)

    def get_showtime(self, target_key: str, showtime_key: str) -> dict[str, Any]:
        return self.data.get(target_key, {}).get(showtime_key, {})

    def set_showtime(
        self, target_key: str, showtime_key: str, record: dict[str, Any]
    ) -> None:
        self.data.setdefault(target_key, {})[showtime_key] = record

    def is_seeded(self, target_key: str) -> bool:
        """이 대상이 최초 폴링(기준선 저장)을 이미 마쳤는지."""
        return target_key in (self.data.get("__seeded__") or {})

    def mark_seeded(self, target_key: str) -> None:
        self.data.setdefault("__seeded__", {})[target_key] = True

    def already_notified(self, target_key: str, showtime_key: str, kind: str) -> bool:
        rec = self.get_showtime(target_key, showtime_key)
        return kind in (rec.get("notified") or [])

    def mark_notified(self, target_key: str, showtime_key: str, kind: str) -> None:
        rec = self.get_showtime(target_key, showtime_key)
        notified = set(rec.get("notified") or [])
        notified.add(kind)
        rec["notified"] = sorted(notified)
        self.set_showtime(target_key, showtime_key, rec)
