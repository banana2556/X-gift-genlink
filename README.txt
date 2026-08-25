X Premium 送禮：輸入 @帳號，取出 Stripe 支付網址（自己付款）

準備
1. 安裝 Python 3
2. 複製 cookies.example.json 為 cookies.json，填入瀏覽器 x.com 的 auth_token、ct0
3. 複製 proxies.example.txt 為 proxies.txt，一行一個代理

代理格式（皆可）
  http://127.0.0.1:7890
  http://user:pass@host:port
  host:port:user:pass

使用
  python gift.py @帳號 -m 3
  python gift.py @帳號 -m 6
  或雙擊 run.bat 互動輸入

失敗會自動換下一條代理重試。印出的 checkout.stripe.com 網址請自行開啟付款。

本包不含任何登入 Cookie 或代理帳密。
