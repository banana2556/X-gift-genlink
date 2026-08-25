#!/usr/bin/env python3
"""輸入 @帳號，向 X 開出 Premium 3/6 個月送禮的 Stripe 支付網址。"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs="
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# 前端 main.js 正式環境對照（subscriptions_stripe_testing 關閉時）
PRODUCTS = {
    3: "prod_TJXJtpzqCpI36N",  # BlueVerified3Months
    6: "prod_TJXKKNJwZJIhCM",  # BlueVerified6Months
}

GIFTING_QUERY_ID = "kn8hCE6bHstQV2MtfYDTKg"
GIFT_MUTATION_ID = "GqTVJ4S1526tLkxj69xIZw"
ORIGIN = "https://x.com"
RETRY_HTTP = {403, 407, 429, 502, 503, 504, 522, 523, 524}
ROOT = Path(__file__).resolve().parent


class ProxyError(Exception):
    """代理或網路層失敗，可換下一條重試。"""


def normalize_screen_name(raw: str) -> str:
    name = raw.strip().lstrip("@＠")
    if not name:
        raise SystemExit("請輸入 @帳號")
    return name


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}。請複製 cookies.example.json 為 cookies.json，"
            "並填入瀏覽器的 auth_token 與 ct0。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    auth = (data.get("auth_token") or "").strip()
    ct0 = (data.get("ct0") or "").strip()
    cookie = (data.get("cookie") or "").strip()
    if cookie:
        if "ct0=" in cookie and not ct0:
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("ct0="):
                    ct0 = part[4:]
                    break
    elif auth and ct0:
        cookie = f"auth_token={auth}; ct0={ct0}"
    else:
        raise SystemExit("cookies.json 需要 cookie 字串，或同時提供 auth_token 與 ct0。")
    if not ct0:
        raise SystemExit("缺少 ct0（CSRF token），請從瀏覽器 Cookie 複製。")
    return {"cookie": cookie, "ct0": ct0}


def normalize_proxy(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        return line
    parts = line.split(":")
    if len(parts) >= 4:
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        user_q = urllib.parse.quote(user, safe="")
        pass_q = urllib.parse.quote(password, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"
    return f"http://{line}"


def redact_proxy(proxy: str) -> str:
    parsed = urllib.parse.urlparse(proxy)
    if not parsed.password:
        return proxy
    user = urllib.parse.quote(parsed.username or "", safe="")
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{user}:***@{host}{port}"


def load_proxies(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        proxy = normalize_proxy(line)
        if proxy:
            out.append(proxy)
    return out


def load_proxy_pool(single: str | None, proxy_file: Path) -> list[str | None]:
    if single:
        return [normalize_proxy(single) or single]
    proxies = load_proxies(proxy_file)
    if not proxies and proxy_file.name == "proxies.txt":
        proxies = load_proxies(proxy_file.with_name("proxy.txt"))
    if not proxies:
        return [None]
    random.shuffle(proxies)
    return proxies


def make_opener(proxy: str | None) -> urllib.request.OpenerDirector:
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def api_headers(cfg: dict) -> dict[str, str]:
    return {
        "accept": "*/*",
        "authorization": f"Bearer {BEARER}",
        "content-type": "application/json",
        "origin": ORIGIN,
        "referer": f"{ORIGIN}/home",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "x-csrf-token": cfg["ct0"],
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "zh-tw",
        "cookie": cfg["cookie"],
    }


def request_json(opener, method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code in RETRY_HTTP:
            raise ProxyError(f"HTTP {e.code}") from e
        raise SystemExit(f"HTTP {e.code}: {detail[:800]}") from e
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        reason = getattr(e, "reason", e)
        raise ProxyError(f"連線失敗：{reason}") from e
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProxyError(f"回應不是 JSON：{raw[:120]}") from e
    if payload.get("errors"):
        err = payload["errors"][0]
        raise SystemExit(f"X API 錯誤：{err.get('message') or err}")
    return payload


def lookup_user(opener, headers: dict, screen_name: str) -> dict:
    variables = json.dumps({"screenName": screen_name}, separators=(",", ":"))
    url = (
        f"{ORIGIN}/i/api/graphql/{GIFTING_QUERY_ID}/PremiumGiftingQuery"
        f"?variables={urllib.parse.quote(variables)}"
    )
    data = request_json(opener, "GET", url, headers)
    user = (((data.get("data") or {}).get("user") or {}).get("result")) or {}
    if user.get("__typename") != "User" or not user.get("rest_id"):
        raise SystemExit(f"找不到帳號 @{screen_name}")
    if not user.get("premium_gifting_eligible"):
        raise SystemExit(f"@{screen_name} 目前不能收禮（已有 Premium 或不符資格）")
    return user


def create_checkout(opener, headers: dict, screen_name: str, rest_id: str, months: int) -> str:
    body = {
        "variables": {
            "cancel_url": f"{ORIGIN}/{screen_name}/gift-premium",
            "external_product_id": PRODUCTS[months],
            "success_url": f"{ORIGIN}/{screen_name}/gift-premium/success",
            "gift_recipient": rest_id,
        },
        "queryId": GIFT_MUTATION_ID,
    }
    url = f"{ORIGIN}/i/api/graphql/{GIFT_MUTATION_ID}/useOneTimePurchaseGiftMutation"
    data = request_json(opener, "POST", url, headers, body)
    gift = ((data.get("data") or {}).get("onetimepurchase_gift")) or {}
    session_url = gift.get("session_url")
    if not session_url:
        raise SystemExit(f"沒有支付網址：{json.dumps(data, ensure_ascii=False)[:800]}")
    return session_url


def prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="用 @帳號取出 X Premium 送禮支付網址")
    p.add_argument("username", nargs="?", help="@帳號，可省略後改互動輸入")
    p.add_argument("-m", "--months", type=int, choices=[3, 6], help="3 或 6 個月")
    p.add_argument("-p", "--proxy", help="指定單一代理（仍會對這條重試）")
    p.add_argument("--proxy-file", default=str(ROOT / "proxies.txt"), help="代理清單，一行一個，失敗就換下一條")
    p.add_argument("--tries", type=int, default=None, help="最多嘗試次數，預設至少 5 次並輪過清單")
    p.add_argument("-c", "--cookies", default=str(ROOT / "cookies.json"), help="cookie 檔路徑")
    p.add_argument("--open", action="store_true", help="取得網址後用瀏覽器打開")
    return p.parse_args()


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = parse_args()
    username = args.username or prompt("輸入 @帳號")
    screen_name = normalize_screen_name(username)

    months = args.months
    if months is None:
        raw = prompt("方案（3 或 6 個月）", "3")
        if raw not in {"3", "6"}:
            raise SystemExit("方案只能是 3 或 6")
        months = int(raw)

    cfg = load_config(Path(args.cookies))
    pool = load_proxy_pool(args.proxy, Path(args.proxy_file))
    tries = args.tries if args.tries else max(len(pool), 5)
    headers = api_headers(cfg)
    last_err: Exception | None = None
    user = None
    session_url = None

    for i in range(1, tries + 1):
        proxy = pool[(i - 1) % len(pool)]
        label = redact_proxy(proxy) if proxy else "直連"
        print(f"[{i}/{tries}] 使用代理：{label}")
        try:
            opener = make_opener(proxy)
            user = lookup_user(opener, headers, screen_name)
            rest_id = user["rest_id"]
            display = ((user.get("core") or {}).get("name")) or screen_name
            print(f"收禮人：{display} (@{screen_name})  id={rest_id}")
            print(f"商品：Premium {months} 個月")
            session_url = create_checkout(opener, headers, screen_name, rest_id, months)
            break
        except ProxyError as e:
            last_err = e
            print(f"失敗，換下一條：{e}")
            if i < tries:
                time.sleep(0.5)
    else:
        raise SystemExit(f"代理全部失敗（試了 {tries} 次）：{last_err}")

    print(session_url)
    if args.open:
        webbrowser.open(session_url)


def _self_check() -> None:
    assert normalize_screen_name("@lykt94h1NYtgvkH") == "lykt94h1NYtgvkH"
    assert PRODUCTS[3] == "prod_TJXJtpzqCpI36N"
    assert PRODUCTS[6] == "prod_TJXKKNJwZJIhCM"
    assert normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert normalize_proxy("# comment") is None
    assert normalize_proxy("http://user:pass@host:8080") == "http://user:pass@host:8080"
    assert (
        normalize_proxy("us.example.io:3010:user-region-BD:secret")
        == "http://user-region-BD:secret@us.example.io:3010"
    )
    shuffled = load_proxy_pool(None, Path("__missing_proxies__.txt"))
    assert shuffled == [None]


if __name__ == "__main__":
    _self_check()
    main()
