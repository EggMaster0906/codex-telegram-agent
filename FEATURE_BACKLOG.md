# 待開發功能清單

這份文件用來記錄 Codex Telegram Agent 後續要擴充的功能、目的與初步實作方向。

## 1. 根據 task id 追蹤後續提問（已完成）

完成日期：2026-06-10

### 目標

讓使用者可以針對指定 task id 查詢結果、查看 log，或接續同一個任務進行後續提問。

### 預期指令

```text
/result <task_id>
/log <task_id>
/continue <task_id> <後續問題>
```

### 初步設計

```text
/result <task_id>
  讀取指定任務的 output_path，將 final output 分段回傳 Telegram。

/log <task_id>
  讀取指定任務的 log_path，回傳尾端摘要或最近 N 行 log。

/continue <task_id> <後續問題>
  第一版：讀取原 task prompt 與 final output，組成上下文後建立新 task。
  第二版：解析並儲存 Codex session id，使用 codex exec resume 延續同一個 session。
```

### 已完成內容

- `/result <task_id>` 分段回傳指定任務的 final output。
- `/log <task_id>` 回傳最近 80 行、最多 12,000 字元的執行 log。
- `/continue <task_id> <後續問題>` 使用原 prompt、final output 與新問題建立
  後續 Task。
- 後續 Task 沿用原任務 workspace，並以 `parent_task_id` 保存追蹤鏈。
- 所有 Task ID 指令均驗證 Telegram chat id，禁止跨 chat 存取。
- SQLite 啟動時自動 migration `parent_task_id` 與 `codex_session_id`。
- 已預留 `codex_session_id`；Codex session resume 留待第二版實作。

### 需要新增的資料欄位

```text
codex_session_id
parent_task_id
```

`parent_task_id` 可用來建立任務追蹤鏈，例如 task #15 是從 task #12 延伸出來的後續提問。

## 2. 透過 Telegram 將 output file 傳送給使用者（已完成）

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
  傳送指定任務的 final.md 與 artifacts。
  若任務尚未產生檔案，回覆目前 task status。
  只允許原任務所屬的 Telegram chat 下載。
```

任務成功完成時，worker 也會自動使用 Telegram `send_document` 傳送
`artifacts/` 內的檔案，不必另外輸入 `/file`。

### Task 目錄結構

```text
tasks/
  task-000001/
    prompt.txt
    task.log
    final.md
    artifacts/
      report.pdf
      image.png
```

### 完成內容

- 每個 Task 建立獨立目錄，避免不同對話的 log 與產物混在一起。
- 原始 prompt、完整執行 log、Codex 最終回覆與使用者產物集中保存。
- Codex 執行時會收到 artifacts 目錄路徑，並透過 `--add-dir` 加入可寫目錄。
- 任務完成後自動掃描並傳送 artifacts。
- `/file <task_id>` 可重新下載 `final.md` 與所有 artifacts。
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
