"""설정 파일 로드 및 검증."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class Target:
    name: str
    movie: str = ""
    movie_code: str = ""
    theater: str = ""
    theater_code: str = ""
    date: str = ""
    time_from: str = "00:00"
    time_to: str = "23:59"
    screen_type: str = ""
    booking_url: str = ""

    def key(self) -> str:
        """상태 저장/중복 알림 방지를 위한 고유 키(회차 무관, 대상 단위)."""
        base = self.movie_code or self.movie
        th = self.theater_code or self.theater
        return f"{base}|{th}|{self.date}|{self.time_from}-{self.time_to}|{self.screen_type}"


@dataclass
class Config:
    targets: list[Target]
    poll: dict[str, Any]
    alerts: dict[str, Any]
    auto_select: dict[str, Any]
    discord: dict[str, Any]
    browser: dict[str, Any]
    errors: dict[str, Any]
    logging: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


class ConfigError(Exception):
    pass


_DEFAULTS: dict[str, dict[str, Any]] = {
    "poll": {"interval_seconds": 45, "jitter_seconds": 15},
    "alerts": {
        "on_showtime_open": True,
        "on_seats_available": True,
        "on_soldout_to_available": True,
        "min_remaining_seats": 1,
    },
    "auto_select": {
        "enabled": False,
        "count": 2,
        "prefer": "center",
        "preferred_seats": [],
    },
    "discord": {"webhook_url": "", "mention": ""},
    "browser": {
        "user_data_dir": "./session",
        "headless": True,
        "slow_mo_ms": 0,
        "nav_timeout_ms": 30000,
    },
    "errors": {
        "max_retries": 3,
        "retry_backoff_seconds": 5,
        "alert_after_consecutive_failures": 5,
        "error_alert_cooldown_seconds": 900,
    },
    "logging": {"dir": "./logs", "level": "INFO"},
}


def _merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = v
    return out


def save_config_dict(path: str, data: dict[str, Any]) -> None:
    """GUI 등에서 만든 설정 dict 를 YAML 로 저장."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_raw(path: str) -> dict[str, Any]:
    """검증 없이 원본 YAML dict 만 로드(GUI 폼 채우기용). 없으면 빈 dict."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str) -> Config:
    if not os.path.exists(path):
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            f"config.example.yaml 을 복사해서 만드세요: cp config.example.yaml config.yaml"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_targets = data.get("targets") or []
    if not raw_targets:
        raise ConfigError("설정에 targets 가 하나도 없습니다. 감시할 대상을 최소 1개 넣으세요.")

    targets: list[Target] = []
    for i, t in enumerate(raw_targets):
        if not isinstance(t, dict):
            raise ConfigError(f"targets[{i}] 형식이 잘못되었습니다(딕셔너리여야 함).")
        name = t.get("name") or t.get("movie") or f"target-{i+1}"
        tgt = Target(
            name=name,
            movie=str(t.get("movie", "")),
            movie_code=str(t.get("movie_code", "")),
            theater=str(t.get("theater", "")),
            theater_code=str(t.get("theater_code", "")),
            date=str(t.get("date", "")),
            time_from=str(t.get("time_from", "00:00")),
            time_to=str(t.get("time_to", "23:59")),
            screen_type=str(t.get("screen_type", "")),
            booking_url=str(t.get("booking_url", "")),
        )
        if not (tgt.movie or tgt.movie_code):
            raise ConfigError(f"targets[{i}] 에 movie 또는 movie_code 가 필요합니다.")
        if not (tgt.theater or tgt.theater_code):
            raise ConfigError(f"targets[{i}] 에 theater 또는 theater_code 가 필요합니다.")
        if not tgt.date:
            raise ConfigError(f"targets[{i}] 에 date(YYYY-MM-DD) 가 필요합니다.")
        targets.append(tgt)

    cfg = Config(
        targets=targets,
        poll=_merge(_DEFAULTS["poll"], data.get("poll")),
        alerts=_merge(_DEFAULTS["alerts"], data.get("alerts")),
        auto_select=_merge(_DEFAULTS["auto_select"], data.get("auto_select")),
        discord=_merge(_DEFAULTS["discord"], data.get("discord")),
        browser=_merge(_DEFAULTS["browser"], data.get("browser")),
        errors=_merge(_DEFAULTS["errors"], data.get("errors")),
        logging=_merge(_DEFAULTS["logging"], data.get("logging")),
        raw=data,
    )

    # 안전 가드: 폴링 주기 하한(5초). 너무 빠르면 IP 차단 위험.
    if cfg.poll["interval_seconds"] < 5:
        cfg.poll["interval_seconds"] = 5

    if not cfg.discord.get("webhook_url"):
        raise ConfigError("discord.webhook_url 이 비어 있습니다. 디스코드 웹훅 URL 을 넣으세요.")

    return cfg
