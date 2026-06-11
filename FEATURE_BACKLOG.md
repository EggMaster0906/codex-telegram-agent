# 待開發功能清單

這份文件用來記錄 Codex Telegram Agent 後續要擴充的功能、目的與初步實作方向。

## 功能狀態總覽

- [x] Task ID 與 Codex session 綁定
- [x] `/new` 建立新 session
- [x] 普通文字自動接續作用中 session
- [x] 24 小時滑動 session 期限
- [x] `/end` 結束目前 session
- [x] `/continue <task_id>` 恢復並切換 session
- [x] Task / Turn 分層與獨立執行紀錄
- [x] Telegram 文字結果與指定附件交付
- [x] `/result`、`/log`、`/file` 與 `/status`
- [x] Telegram 附件輸入
- [ ] 任務取消
- [ ] Artifact metadata 與 `/files`
- [ ] 多 workspace 管理
- [ ] systemd 常駐服務

## 1. 多輪 Codex Session（已完成）

完成日期：2026-06-11

### 目標

讓使用者以 Task ID 代表一段可恢復的 Codex 對話。使用者以 `/new` 開始，
後續直接傳送普通文字即可接續同一個 session。

### 已實作介面

```text
/new <task prompt>
普通文字
/end
/continue <task_id>
/status
/result <task_id>
/log <task_id>
/file <task_id>
```

### 已完成行為

- `/new <prompt>` 建立新的 Task、第一個 Turn 與作用中 session。
- 建立新 session 時，原本作用中的 session 會改為 `ended`。
- 普通文字會建立新的 Turn，並接續目前作用中的 Task。
- 每次接受普通文字時更新 `last_activity_at`，重新計算 24 小時期限。
- session 逾時後不自動建立新 session，而是提示使用 `/new` 或 `/continue`。
- `/end` 將目前 session 設為 `ended`，但不取消既有 Turn。
- `/continue <task_id>` 可恢復 `ended` 或 `expired` session，並停用其他
  作用中的 session。
- `/continue` 本身只切換作用中 session，不建立新的 Turn。
- 第一輪透過 `codex exec --json` 執行，並解析 `thread.started` 的 session ID。
- 後續 Turn 使用 `codex exec resume <session_id> <prompt>`。
- 同一 session 的 Turn 依序排隊，不會同時 resume。
- Task ID 在整段對話中保持不變，每則訊息使用獨立 Turn 保存執行紀錄。
- 所有 Task ID 指令均驗證 Telegram chat id，禁止跨 chat 存取。
- SQLite 啟動時自動 migration session 欄位並建立 `task_turns`。
- 既有 `/run` 保留為舊式單次任務相容指令。

### 查詢與交付

- `/status` 顯示最近五筆 Task 的執行狀態與 session 狀態。
- `/result <task_id>` 分段回傳最新一輪 final output。
- `/log <task_id>` 回傳最新一輪最近 80 行、最多 12,000 字元的 log。
- `/file <task_id>` 傳送最新一輪 final output 與 artifacts。
- final output 超過 Telegram 單則訊息限制時會自動分段。
- 一般回答預設只交付文字。
- 只有 `.delivery.json` 指定的 artifacts 會在完成時自動傳送。

### 主要資料欄位

```text
codex_session_id
session_status
last_activity_at
task_turns
```

### 驗證

- session ID JSONL 解析測試。
- 第一輪與 resume Turn 的 worker 整合測試。
- session 建立、切換、結束、逾時與滑動期限測試。
- 既有 SQLite 資料庫 migration 驗證，原 Task 筆數與資料保持不變。
- Codex CLI `exec resume` 參數格式驗證。

## 2. Telegram 結果與檔案交付（已完成）

完成日期：2026-06-10

### 目標

讓使用者可以透過 Telegram 直接取得任務產生的 output 檔案，例如 markdown 文件。

### 已實作指令

```text
/file <task_id>
```

### 使用方式

```text
/file <task_id>
  傳送指定任務最新一輪的 final.md 與 artifacts。
  若任務尚未產生檔案，回覆目前 task status。
  只允許原任務所屬的 Telegram chat 下載。
```

任務成功完成時，worker 也會自動使用 Telegram `send_document` 傳送
`.delivery.json` 指定的附件，不必另外輸入 `/file`。

### Task 目錄結構

```text
tasks/
  task-000001/
    turn-000001/
      prompt.txt
      task.log
      final.md
      artifacts/
        report.pdf
        image.png
```

### 完成內容

- 每個 Task 與 Turn 建立獨立目錄，避免不同對話的 log 與產物混在一起。
- 每輪 prompt、完整執行 log、Codex 最終回覆與使用者產物集中保存。
- Codex 執行時會收到 artifacts 目錄路徑，並透過 `--add-dir` 加入可寫目錄。
- 任務完成後依 `.delivery.json` 傳送指定 artifacts。
- manifest 缺少、格式錯誤或指定純文字時，不會自動傳送附件。
- 附件路徑會拒絕絕對路徑、路徑穿越、隱藏檔、重複檔案與 symlink。
- `/file <task_id>` 可重新下載最新一輪的 `final.md` 與 artifacts。
- 下載時檢查 task 所屬 `chat_id`，避免跨使用者存取。
- SQLite 啟動時自動 migration，為既有資料庫加入 `task_dir` 欄位。
- `CODEX_SANDBOX_MODE` 可由環境變數設定。

### 部署與驗收

- 遠端主機因無法初始化 bubblewrap sandbox，已由管理者核准使用
  `CODEX_SANDBOX_MODE=danger-full-access`。
- Codex 實際權限仍受 `ai-agent` Linux 帳號限制。
- 已完成真實 Codex CLI 寫檔測試，成功在 artifacts 目錄產生檔案。
- 已確認最終回覆檔案、artifact 掃描及 Telegram Bot 程序正常。
- artifact 與 SQLite migration 單元測試均已通過。

### 後續擴充方向

- 將 artifact metadata 寫入資料庫，而非每次掃描 Task 目錄。
- 支援 `/files <task_id>` 列出指定任務可下載的產物。
- 加入 Telegram 檔案大小檢查與超過限制時的替代下載方式。

## 3. Telegram 附件輸入（已完成）

完成日期：2026-06-12

### 背景

目前部署的 Telegram Bot 只能接收文字 prompt 並建立 Task，尚無法將使用者
傳送的附件交給 Codex 讀取。

### 目標

讓使用者可以透過 Telegram 傳送文件、圖片或其他支援的附件，Bot 將附件
下載至對應 Task 的目錄，並把檔案路徑與文字說明一併交給 Codex 處理。

### 初步使用方式

```text
傳送附件並在 caption 填寫任務說明

或

先傳送附件，再透過指令或後續訊息補充 prompt
```

### 已完成行為

- 支援 Telegram 文件、圖片、音訊、影片、動畫、語音、視訊留言與貼圖。
- 附件有 caption 時以 caption 作為任務說明；沒有 caption 時使用預設附件
  處理指示，使用者可在同一 session 的下一則文字繼續補充需求。
- caption 以 `/new` 或 `/new@BotName` 開頭時，強制建立新的 Task/session，
  並只將指令後方文字作為附件處理 prompt。
- 有作用中 session 時建立新的 Turn；否則建立新的 Task/session。
- 附件保存於
  `tasks/task-XXXXXX/turn-XXXXXX/inputs/`，並將絕對路徑加入該輪 prompt。
- 原始檔名會移除路徑與控制字元，重名檔案自動加上流水號。
- Turn 在下載期間使用 `uploading` 狀態，完整下載並寫入 prompt 後才進入
  `pending`，避免 worker 提前讀取不完整檔案。
- Codex sandbox command 會將該輪 `inputs/` 加入可存取目錄。
- 超過 Telegram Bot API 20 MB 下載上限的附件會直接提示使用者，不建立 Turn。

### 第一版實作決策

- 每則 Telegram 附件訊息建立一個 Turn；媒體群組中的各附件會依序建立
  多個 Turn，暫不合併為單一執行。
- 沒有 caption 時立即使用預設附件處理 prompt 排入佇列，不等待下一則訊息。
- 後續普通文字會接續同一個 session，可再補充附件處理需求。
- 使用 Telegram 雲端 Bot API 的 20 MB 下載上限；尚未導入 Local Bot API
  Server 或替代下載管道。
