# 📈 台股型態分析

每日自動掃描台股大型股，偵測 **W 底 / M 頭 / 突破跌破頸線 / 量價背離**
等古典技術分析型態，並以網頁方式公開展示。

> ⚠️ **免責聲明**：本專案僅為技術分析教學與研究用途，所有訊號為演算法依公開歷史資料偵測之型態結果，**不構成任何投資建議或買賣依據**。投資有風險，請自行評估並承擔損益。

---

## ✨ 功能

- 每日 08:30 (Asia/Taipei) 自動執行 GitHub Actions
- 從 [FinMind](https://finmindtrade.com/) 拉取台股日K（約 200 個交易日）
- 偵測：
  - W 底（雙底）與頸線、滿足點
  - M 頭（雙頂）與頸線、滿足點
  - 多/空頭趨勢、20 日突破前高/跌破前低
  - 量比、爆量/縮量、量價背離、主力進貨/出貨判斷
- 訊號分類：**買進 / 賣出 / 觀望**
- SPA 前端：依產業分組、訊號排序、可篩選、深淺色切換、響應式
- 歷史日期下拉，可瀏覽過往掃描結果

## 🛠 技術 Stack

- **資料源**：FinMind Open API (`TaiwanStockPrice`)
- **掃描腳本**：Python 3.11，僅用標準函式庫（`urllib`, `json`, `datetime`）
- **前端**：純 HTML / CSS / JS（無框架，無打包），讀取 `data/latest.json` 動態渲染
- **排程**：GitHub Actions cron（`.github/workflows/daily-scan.yml`）
- **託管**：GitHub Pages（main 分支 root）

## 📁 專案結構

```
.
├── index.html                    # 前端 SPA
├── data/
│   ├── latest.json               # 最新一日掃描結果
│   ├── index.json                # 可選日期清單
│   └── YYYY-MM-DD.json           # 歷史每日快照
├── scripts/
│   ├── analyze.py                # 掃描 + 型態分析主腳本
│   └── industries.json           # 個股 → 產業/子產業/題材標籤
├── .github/workflows/
│   └── daily-scan.yml            # 每日排程
└── requirements.txt
```

## 🚀 本地執行

```bash
python3 scripts/analyze.py
# 輸出寫入 data/latest.json 與 data/YYYY-MM-DD.json
```

之後在 repo 根目錄起個簡單 server 預覽：

```bash
python3 -m http.server 8000
# 瀏覽 http://localhost:8000
```

## 📊 型態定義（簡述）

| 型態 | 偵測條件 | 訊號 |
|---|---|---|
| **W 底** | 最近 80 天內出現兩個低點，差距 ≤ 5%，間隔 8-60 天，中間反彈點為頸線 | 突破頸線 → 買進；尚未突破 → 觀望 |
| **M 頭** | 最近 80 天內出現兩個高點，差距 ≤ 5%，間隔 8-60 天，中間回測點為頸線 | 跌破頸線 → 賣出；尚未跌破 → 觀望 |
| **突破前高** | 收盤 ≥ 20 日最高，且 MA5 > MA20 > MA60 | 買進 |
| **跌破前低** | 收盤 ≤ 20 日最低，且 MA5 < MA20 < MA60 | 賣出 |
| **量價背離** | 價漲量縮 / 價跌量增 | 標記於量價資訊區 |

**滿足點**：頸線 ± (頸線 − 底 / 頂 − 頸線)。
**停損**：W 底取底部 × 0.97，M 頭取頂部 × 1.03。

## 🤝 貢獻

歡迎 PR 增補產業表 `scripts/industries.json`、調整型態演算法、改善前端。

## 📜 License

MIT
