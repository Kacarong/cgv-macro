"""디스코드 웹훅 알림."""
from __future__ import annotations

import json
import logging
import os
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
    "stage_event": 0xE67E22, # 주황: 무대인사/GV 감지
    "error": 0xE74C3C,       # 빨강: 에러
}


def _normalize_mention(m: str) -> str:
    """숫자ID/@ID 를 디스코드 핑 형식 <@ID> 로 정규화. @everyone/@here/<@...>는 그대로."""
    m = (m or "").strip()
    if not m:
        return ""
    if m in ("@everyone", "@here"):
        return m
    if m.startswith("<@") and m.endswith(">"):
        return m
    digits = m.lstrip("@").strip("<>@!&")
    if digits.isdigit():
        return f"<@{digits}>"
    return m


class DiscordNotifier:
    def __init__(self, webhook_url: str = "", mention: str = "",
                 bot_token: str = "", user_id: str = "") -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.mention = _normalize_mention(mention)
        self.bot_token = (bot_token or "").strip()
        self.user_id = (user_id or "").strip()
        self.dm_mode = bool(self.bot_token and self.user_id)   # 봇 DM 방식
        self._dm_channel = None
        self._last_error_ts = 0.0

    # ---- 전송 대상(웹훅 채널 vs 봇 DM 채널) ----
    def _dm_channel_id(self):
        if self._dm_channel:
            return self._dm_channel
        try:
            data = json.dumps({"recipient_id": self.user_id}).encode("utf-8")
            req = urllib.request.Request(
                "https://discord.com/api/v10/users/@me/channels", data=data,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bot {self.bot_token}",
                         "User-Agent": "cgv-macro/0.1"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                self._dm_channel = json.load(r).get("id")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            logger.error("DM 채널 생성 실패(HTTP %s): %s %s (봇/유저ID/공유서버 확인)", e.code, e.reason, body)
        except Exception as e:  # noqa: BLE001
            logger.error("DM 채널 생성 실패: %s", e)
        return self._dm_channel

    def _endpoint(self):
        if self.dm_mode:
            cid = self._dm_channel_id()
            return f"https://discord.com/api/v10/channels/{cid}/messages" if cid else None
        return self.webhook_url or None

    def _auth_headers(self) -> dict[str, str]:
        h = {"User-Agent": "cgv-macro/0.1 DiscordWebhook"}
        if self.dm_mode:
            h["Authorization"] = f"Bot {self.bot_token}"
        return h

    def _payload_with_mention(self, payload: dict[str, Any]) -> dict[str, Any]:
        # DM 은 그 자체로 알림이 오므로 멘션 불필요. 웹훅만 멘션 핑 추가.
        if self.mention and not self.dm_mode:
            payload["content"] = self.mention
            payload["allowed_mentions"] = {"parse": ["users", "everyone"]}
        return payload

    def _post(self, payload: dict[str, Any]) -> bool:
        endpoint = self._endpoint()
        if not endpoint:
            logger.error("디스코드 전송 대상 없음(웹훅/봇 설정 확인)")
            return False
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._auth_headers()}
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
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

    def _post_multipart(self, payload: dict[str, Any], image_path: str) -> bool:
        """embed + 이미지 첨부를 multipart 로 전송(결제 화면 스크린샷용)."""
        try:
            with open(image_path, "rb") as f:
                img = f.read()
        except Exception:  # noqa: BLE001
            return self._post(payload)   # 이미지 없으면 텍스트로라도
        boundary = "----cgvmacro7f3b2a"
        fname = os.path.basename(image_path) or "shot.png"
        pj = json.dumps(payload).encode("utf-8")
        parts = []
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="payload_json"\r\n')
        parts.append(b"Content-Type: application/json\r\n\r\n")
        parts.append(pj + b"\r\n")
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="files[0]"; filename="{fname}"\r\n'.encode())
        parts.append(b"Content-Type: image/png\r\n\r\n")
        parts.append(img + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        endpoint = self._endpoint()
        if not endpoint:
            return self._post(payload)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **self._auth_headers()}
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return 200 <= resp.status < 300
        except Exception as e:  # noqa: BLE001
            logger.error("디스코드 이미지 전송 실패: %s", e)
            return self._post(payload)

    def notify_held_image(self, *, movie: str, theater: str, date: str, showtime: str,
                          screen: str, seat_info: str, image_path: str = "",
                          reached: bool = True) -> bool:
        """좌석 선점 알림 — 멘션 + 결제 화면 스크린샷. reached=결제 페이지 도달 여부."""
        title = ("🪑 좌석 선점 완료 — 결제만 하면 됩니다!" if reached
                 else "🪑 좌석 선택됨 — 창에서 '결제하기'를 직접 눌러 확인하세요")
        embed = {
            "title": title,
            "color": _COLOR["seat_held"] if reached else 0xE67E22,
            "fields": [
                {"name": "영화", "value": movie or "-", "inline": True},
                {"name": "극장", "value": theater or "-", "inline": True},
                {"name": "상영관", "value": screen or "-", "inline": True},
                {"name": "날짜/시간", "value": f"{date} {showtime}".strip() or "-", "inline": True},
                {"name": "좌석", "value": seat_info or "-", "inline": True},
                {"name": "상태", "value": ("결제 페이지 도달 — 결제만 하세요" if reached
                                          else "결제하기 미도달 — 창에서 직접 결제하기 클릭 필요"), "inline": False},
            ],
        }
        payload = self._payload_with_mention({"embeds": [embed]})
        if image_path:
            return self._post_multipart(payload, image_path)
        return self._post(payload)

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
            "stage_event": "🎤 무대인사/GV 감지",
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
        return self._post(self._payload_with_mention(payload))

    def notify_info(self, title: str, message: str, cooldown_seconds: int = 300) -> bool:
        """일반 정보 알림. 같은 제목은 cooldown 내 1회만(여러 창 동시 알림 도배 방지)."""
        now = time.time()
        last = getattr(self, "_info_last", {})
        if now - last.get(title, 0.0) < cooldown_seconds:
            return False
        last[title] = now
        self._info_last = last
        embed = {"title": title, "description": message[:1900], "color": 0x95A5A6}
        payload: dict[str, Any] = {"embeds": [embed]}
        return self._post(self._payload_with_mention(payload))

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
        return self._post(self._payload_with_mention(payload))
