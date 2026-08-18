"""디스코드 웹훅 알림."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger("cgv_macro")

# 상태값 → 디스코드 embed 색상
_COLOR = {
    "open": 0x3498DB,        # 파랑: 상영 오픈
    "available": 0x2ECC71,   # 초록: 잔여석
    "cancel": 0xF1C40F,      # 노랑: 취소표(매진→잔여)
    "seat_held": 0x9B59B6,   # 보라: 좌석 자동선택 완료
    "error": 0xE74C3C,       # 빨강: 에러
}


class DiscordNotifier:
    def __init__(self, webhook_url: str, mention: str = "") -> None:
        self.webhook_url = webhook_url
        self.mention = mention or ""
        self._last_error_ts = 0.0

    def _post(self, payload: dict[str, Any]) -> bool:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                # 디스코드/Cloudflare 는 기본 'Python-urllib' UA 를 403 으로 차단하므로
                # 반드시 정상적인 User-Agent 를 붙인다.
                "User-Agent": "cgv-macro/0.1 (+https://github.com/) DiscordWebhook",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            logger.error("디스코드 전송 실패(HTTP %s): %s %s", e.code, e.reason, body)
        except Exception as e:  # noqa: BLE001
            logger.error("디스코드 전송 실패: %s", e)
        return False

    def notify_showtime(
        self,
        *,
        kind: str,
        target_name: str,
        movie: str,
        theater: str,
        date: str,
        showtime: str,
        status_text: str,
        booking_url: str,
        seat_info: str = "",
        screen: str = "",
    ) -> bool:
        """상영/좌석 관련 알림."""
        title = {
            "open": "🎬 상영 오픈",
            "available": "🟢 잔여석 발생",
            "cancel": "🎟️ 취소표 발생 (매진→잔여)",
            "seat_held": "🪑 좌석 자동 선택 완료 — 결제만 하면 됩니다",
        }.get(kind, "CGV 알림")

        fields = [
            {"name": "영화", "value": movie or "-", "inline": True},
            {"name": "극장", "value": theater or "-", "inline": True},
            {"name": "상영관", "value": screen or "-", "inline": True},
            {"name": "날짜/시간", "value": f"{date} {showtime}".strip() or "-", "inline": True},
            {"name": "상태", "value": status_text or "-", "inline": True},
        ]
        if seat_info:
            fields.append({"name": "좌석", "value": seat_info, "inline": False})

        embed = {
            "title": title,
            "description": f"**{target_name}**",
            "color": _COLOR.get(kind, 0x95A5A6),
            "fields": fields,
        }
        if booking_url:
            embed["url"] = booking_url
            embed["fields"].append(
                {"name": "예매 페이지", "value": booking_url, "inline": False}
            )

        payload: dict[str, Any] = {"embeds": [embed]}
        if self.mention:
            payload["content"] = self.mention
        return self._post(payload)

    def notify_info(self, title: str, message: str) -> bool:
        """일반 정보 알림(감시 시작 요약 등)."""
        embed = {"title": title, "description": message[:1900], "color": 0x95A5A6}
        payload: dict[str, Any] = {"embeds": [embed]}
        if self.mention:
            payload["content"] = self.mention
        return self._post(payload)

    def notify_error(self, message: str, cooldown_seconds: int = 900) -> bool:
        """에러 알림. cooldown 내 반복 호출은 억제(도배 방지)."""
        now = time.time()
        if now - self._last_error_ts < cooldown_seconds:
            logger.debug("에러 알림 쿨다운 중 — 전송 생략")
            return False
        self._last_error_ts = now
        embed = {
            "title": "⚠️ CGV 감시 오류",
            "description": message[:1900],
            "color": _COLOR["error"],
        }
        payload: dict[str, Any] = {"embeds": [embed]}
        if self.mention:
            payload["content"] = self.mention
        return self._post(payload)
