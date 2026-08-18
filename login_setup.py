"""
최초 1회 로그인 세션 저장 스크립트.

CGV 자동 로그인은 캡차/추가인증으로 막힐 수 있어, 사람이 직접 로그인한 세션(쿠키)을
브라우저 프로필 폴더에 저장해두고 감시 때 재사용합니다.

사용법:
    python login_setup.py                # config.yaml 의 browser.user_data_dir 사용
    python login_setup.py --config config.yaml

실행하면 브라우저 창이 열립니다. CGV 에 로그인한 뒤, 터미널에서 Enter 를 누르면
세션이 저장되고 종료됩니다. 이후 감시(python -m cgv_macro)는 headless 로 이 세션을 씁니다.
"""
from __future__ import annotations

import argparse

from playwright.sync_api import sync_playwright

from cgv_macro.config import load_config
from cgv_macro import selectors as S


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", "-c", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    user_data_dir = cfg.browser.get("user_data_dir", "./session")

    print(f"[login] 브라우저 프로필: {user_data_dir}")
    print("[login] 열리는 창에서 CGV 에 로그인하세요. 끝나면 이 터미널에서 Enter.")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,  # 로그인은 반드시 화면에 띄움
            locale="ko-KR",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(S.LOGIN_URL)
        input("로그인을 마쳤으면 Enter 를 누르세요... ")

        # 로그인 확인(베스트에포트)
        try:
            page.goto(S.SITE_BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            ok = page.locator(S.LOGGED_IN_HINT_SELECTOR).count() > 0
            print("[login] 로그인 상태로 보입니다 ✅" if ok
                  else "[login] 로그인 표시를 못 찾았습니다. 그래도 세션은 저장됩니다.")
        except Exception as e:  # noqa: BLE001
            print(f"[login] 확인 중 예외(무시): {e}")

        ctx.close()

    print("[login] 세션 저장 완료. 이제 python -m cgv_macro 로 감시를 시작하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
