"""
세션 만료 시 자동 재로그인(선택 기능, 토글로 on/off).

CGV(CJ ONE) 로그인은 6자리 숫자 캡챠(자동입력 방지문자)가 있다. '새로고침'이 있어
틀리면 다시 받아 재시도할 수 있으므로, 인식률이 완벽하지 않아도 여러 번 시도하면 된다.

캡챠 해석기:
  - 2Captcha API 키가 있으면 그걸로 해석(안정적).
  - 없으면 오프라인 해석은 미지원 → 실패로 두고 '수동 로그인 필요' 알림.

주의: 실제 CGV 로그인 DOM(입력/버튼 셀렉터)은 개편으로 바뀔 수 있어, 최초 1회 실측 튜닝이
필요할 수 있다. 셀렉터는 placeholder/텍스트 기반으로 최대한 견고하게 잡는다.
"""
from __future__ import annotations

import base64
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("cgv_macro")

LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"

_SET_VAL_JS = r"""(a)=>{
  const setV=(el,v)=>{const d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
    (d&&d.set?d.set:function(x){this.value=x;}).call(el,v);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));};
  const ins=[...document.querySelectorAll('input')];
  const id=ins.find(e=>(e.placeholder||'').includes('아이디'));
  const pw=ins.find(e=>e.type==='password'||(e.placeholder||'').includes('영문'));
  if(id) setV(id,a.id);
  if(pw) setV(pw,a.pw);
  return (id?1:0)+(pw?2:0);
}"""

_FILL_CAP_JS = r"""(v)=>{
  const setV=(el,x)=>{const d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
    (d&&d.set?d.set:function(y){this.value=y;}).call(el,x);
    el.dispatchEvent(new Event('input',{bubbles:true}));};
  const ins=[...document.querySelectorAll('input')];
  const cap=ins.find(e=>(e.placeholder||'').includes('자동입력'));
  if(cap){setV(cap,v);return 1;} return 0;
}"""

_CLICK_LOGIN_JS = r"""()=>{
  const btns=[...document.querySelectorAll('button,a,div[role=button]')];
  const b=btns.find(e=>{const t=(e.textContent||'').replace(/\s+/g,'');
    return t==='로그인'&&e.offsetParent!==null;});
  if(b){b.click();return 1;} return 0;
}"""

_REFRESH_CAP_JS = r"""()=>{
  const els=[...document.querySelectorAll('button,a,span,div')];
  const r=els.find(e=>(e.textContent||'').includes('새로고침')&&e.offsetParent!==null);
  if(r){r.click();return 1;} return 0;
}"""


def _captcha_png(page) -> bytes | None:
    """로그인 화면의 캡챠 이미지 요소를 찾아 PNG 바이트로 캡처."""
    for sel in ("img[src*='captcha']", "img[alt*='자동']", "canvas",
                "img[src^='data:image']", "img"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                box = loc.bounding_box()
                if box and box["width"] > 40 and box["height"] > 15:
                    return loc.screenshot()
        except Exception:  # noqa: BLE001
            continue
    return None


def _solve_2captcha(img_png: bytes, key: str, log) -> str | None:
    """2Captcha 로 숫자 캡챠 해석. 성공 시 문자열 반환."""
    try:
        b64 = base64.b64encode(img_png).decode()
        data = urllib.parse.urlencode({
            "key": key, "method": "base64", "body": b64,
            "numeric": "1", "min_len": "6", "max_len": "6", "json": "1"}).encode()
        req = urllib.request.Request("https://2captcha.com/in.php", data=data)
        import json as _json
        r = _json.load(urllib.request.urlopen(req, timeout=20))
        if str(r.get("status")) != "1":
            log(f"[재로그인] 2captcha 등록 실패: {r.get('request')}"); return None
        cid = r["request"]
        for _ in range(24):   # 최대 ~2분 폴링
            time.sleep(5)
            res = _json.load(urllib.request.urlopen(
                f"https://2captcha.com/res.php?key={key}&action=get&id={cid}&json=1", timeout=20))
            if str(res.get("status")) == "1":
                return "".join(ch for ch in str(res["request"]) if ch.isdigit())
            if res.get("request") != "CAPCHA_NOT_READY":
                log(f"[재로그인] 2captcha 오류: {res.get('request')}"); return None
    except Exception as e:  # noqa: BLE001
        log(f"[재로그인] 2captcha 예외: {e}")
    return None


def solve_captcha(img_png: bytes, twocaptcha_key: str, log) -> str | None:
    if twocaptcha_key:
        return _solve_2captcha(img_png, twocaptcha_key, log)
    log("[재로그인] 캡챠 해석기(2Captcha 키)가 없어 자동 해석 불가 — 수동 로그인 필요")
    return None


def attempt_relogin(page, cid: str, cpw: str, twocaptcha_key: str = "",
                    log=logger.info, max_tries: int = 6) -> bool:
    """로그인 화면에서 ID/PW 입력 + 캡챠 해석 + 로그인. 틀리면 새로고침 후 재시도."""
    if not (cid and cpw):
        log("[재로그인] CGV 아이디/비밀번호가 설정되지 않아 자동 로그인 불가")
        return False
    for k in range(max_tries):
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1300)
            if "movieBook" in page.url and "login" not in page.url:
                return True   # 이미 로그인됨
            page.evaluate(_SET_VAL_JS, {"id": cid, "pw": cpw})
            page.wait_for_timeout(300)
            img = _captcha_png(page)
            if img:
                code = solve_captcha(img, twocaptcha_key, log)
                if code:
                    page.evaluate(_FILL_CAP_JS, code)
                    log(f"[재로그인] 캡챠 입력 '{code}' (시도 {k+1})")
                elif not twocaptcha_key:
                    return False   # 해석기 없음 → 즉시 포기(수동)
            page.wait_for_timeout(300)
            page.evaluate(_CLICK_LOGIN_JS)
            page.wait_for_timeout(2200)
            if "movieBook" in page.url and "login" not in page.url:
                log(f"[재로그인] ✅ 성공 (시도 {k+1})")
                return True
            page.evaluate(_REFRESH_CAP_JS)   # 캡챠 새로고침 후 재시도
            page.wait_for_timeout(600)
        except Exception as e:  # noqa: BLE001
            log(f"[재로그인] 시도 {k+1} 오류: {e}")
            page.wait_for_timeout(800)
    log("[재로그인] 자동 로그인 실패 — 수동 로그인이 필요합니다")
    return False
