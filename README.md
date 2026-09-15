# 膳雉坊線上訂餐系統

Python FastAPI + Google Firestore 的可收單版本。

## 已完成

- 客人選擇餐點、取餐地點並送出訂單
- 訂單編號與完成頁
- Firestore 永久保存訂單、餐點、地點及設定
- 管理員帳密登入
- 後台查看訂單與更新狀態
- 客人可用下單手機號碼查詢訂單內容與處理狀態
- 客人可在訂單查詢頁取消尚未取餐、尚未取消的訂單，並同步通知 LINE 群組
- 訂單查詢與後台顯示台灣下單時間（到分鐘）
- 結帳可選實體發票或手機載具條碼
- 後台可點擊手機載具號碼，放大顯示 Code 39 條碼供發票設備掃描
- 後台提供卡片／條列表格切換、日期時間篩選、排序及餐點數量統計
- LINE 官方帳號加入群組後，新訂單自動推送群組通知
- 訂單狀態包含新訂單、已確認、已完成、已取餐及已取消
- LINE 群組只通知新訂單及已取消，其他狀態更新不推送
- 新增、修改、上下架餐點
- 新增多個取餐日期
- 每個日期分別設定地點、時間點及開放／停用
- 新增及啟停取餐地點
- 圖片網址；設定 Cloudinary 後可直接上傳圖片

## 網址

- 客戶訂餐：`/`
- 客戶訂單查詢：`/order-lookup`
- 管理後台：`/admin/login`
- 健康檢查：`/health`

## Heroku Config Vars（必要）

| Key | Value |
| --- | --- |
| `FIREBASE_CREDENTIALS_BASE64` | Firebase 服務帳戶 JSON 的 Base64 |
| `FIRESTORE_NAMESPACE` | 建議固定填 `shanzhifang`，讓資料與原網站分開 |
| `ADMIN_USERNAME` | 建議 `shanzhifang-admin` |
| `ADMIN_PASSWORD` | 自行設定的強密碼，不要寫入 GitHub |
| `SESSION_SECRET` | 自行產生的長隨機字串 |
| `LINE_CHANNEL_SECRET` | LINE Messaging API 的 Channel secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API 的長效 Channel access token |

選用：`CLOUDINARY_URL`，設定後後台可直接上傳餐點圖片。

選用：`HOME_CACHE_SECONDS`，首頁資料快取秒數，預設為 `60`。後台修改菜單、店家、地點或日期時會立即清除快取。

## 更新 GitHub

解壓縮後，請建立新的 GitHub Repository，再將所有檔案上傳；不要覆蓋原本訂餐網站的 Repository。

Heroku 與 GitHub 自動部署連接後，推送到 `main` 即可重新部署。

## LINE 群組通知啟用方式

1. 部署新版後，在 LINE Developers 將 Webhook URL 設為 `https://你的網域/line/webhook` 並開啟 Use webhook。
2. 驗證 Webhook 成功後，開啟允許官方帳號加入群組。
3. 登入 `/admin/settings` 查看一次性群組連接指令。
4. 將官方帳號邀請進通知群組，在群組貼上該指令；收到連接成功訊息後即可測試下單。

## 兩個網站的資料與 LINE 設定

- 可以沿用同一組 `FIREBASE_CREDENTIALS_BASE64`，本版會以 `FIRESTORE_NAMESPACE=shanzhifang` 建立獨立資料集合，不會讀取原網站訂單。
- 若只需要將新訂單推播到同一個 LINE 群組，可沿用原本的 LINE Channel secret 與 access token，並在新網站後台重新綁定群組。
- LINE Messaging API 的一個 Channel 同時只能設定一個 Webhook URL。若原網站與膳雉坊都需要在群組內點按鈕更新訂單，膳雉坊應另外建立一個 Messaging API Channel／官方帳號，避免其中一個網站收不到 Webhook。
- LINE Pay 測試環境可先沿用；正式營運時應依實際收款商店設定正式金鑰。
# LINE Pay Sandbox 測試

在 Heroku `Settings > Config Vars` 新增以下三項（請勿將金鑰放進 GitHub）：

```text
LINE_PAY_CHANNEL_ID=測試環境的 Channel ID
LINE_PAY_CHANNEL_SECRET=測試環境的 Channel Secret Key
LINE_PAY_ENV=sandbox
```

測試版使用 `https://sandbox-api-pay.line.me/v3`。付款成功後，訂單會記錄為
`payment_status=paid`、`invoice_status=pending`；本版不會實際開立發票。

正式上線前才將兩組金鑰換成正式環境資料，並把 `LINE_PAY_ENV` 改成
`production`。正式環境會產生真實扣款，切換前務必先完成退款流程測試。
