# Codex Telegram Agent

Codex Telegram Agent 是部署於 Linux 主機的輕量任務代理。授權使用者可以
透過 Telegram 建立 Codex 任務、查詢狀態、接收文字結果，並直接下載任務
產生的圖片、文件或其他檔案。

```text
Telegram 使用者
  -> Telegram Bot
  -> SQLite 任務佇列
  -> 背景 Worker
  -> Codex CLI
  -> Task / Turn 獨立目錄
  -> Telegram 文字與檔案回傳
```

## 目前功能

- 只接受 `ALLOWED_CHAT_IDS` 內的 Telegram chat。
- `/new <prompt>` 建立新的 Task、Turn 與 Codex session。
- 同一 Telegram chat 的普通文字會接續目前作用中的 Task。
- 支援接收文件、圖片與其他 Telegram 附件，保存至對應 Task/Turn 的
  `inputs/` 後交給 Codex。
- 附件 caption 以 `/new` 或 `/new@BotName` 開頭時會強制建立新的
  Task/session；其餘附件會接續目前作用中的 session。
- Telegram Bot API 可下載附件上限為 20 MB，超過時不建立 Turn。
- session 使用 24 小時滑動期限；每次接受新訊息時重新計時。
- `/end` 停用目前 session，`/continue <task_id>` 可恢復已結束或逾時的 session。
- 使用 SQLite 保存 Task、Turn、Codex session ID、狀態、workspace 與輸出路徑。
- 背景 worker 依建立順序執行 pending Turn，同一 session 不會並行 resume。
- 第一輪使用 `codex exec --json`，後續輪使用
  `codex exec resume <session_id>`。
- 每個 Turn 使用獨立目錄保存 prompt、log、final output 與 artifacts。
- 任務完成後將 final output 以 Telegram Markdown 格式分段回傳。
- 一般任務預設只回傳 Telegram 文字。
- 僅自動傳送 `.delivery.json` 明確指定的 artifacts。
- 支援 `/result <task_id>` 重新查看文字結果。
- 支援 `/log <task_id>` 查看最近 80 行執行 log。
- 支援 `/file <task_id>` 以互動按鈕選擇下載 artifacts。
- 保留 `/run <prompt>` 作為舊式單次任務相容指令。
- 支援 `/help` 列出所有目前可用的指令與功能。
- 所有 Task ID 操作都驗證所屬 chat ID，避免跨 chat 存取。
- 啟動時自動 migration 既有 SQLite schema。

## 對話流程

1. 使用者透過 `/new <prompt>` 建立新的 Task 與 Codex session。
2. Bot 將第一輪訊息寫入 SQLite，狀態設為 `pending`。
3. Bot 建立 `tasks/task-XXXXXX/turn-XXXXXX/` 與 `artifacts/`。
4. Worker 將該輪改為 `running` 並呼叫 `codex exec --json`。
5. Worker 從 JSONL 事件保存 Codex session ID。
6. 使用者在 24 小時內傳送普通文字時，Bot 建立新的 Turn。
7. 使用者傳送附件時，Bot 先完整下載至該 Turn 的 `inputs/`，再排入 worker。
8. Worker 使用 `codex exec resume <session_id>` 接續同一段對話。
9. 每次接受新訊息時，24 小時閒置期限重新計算。
10. `/new` 會停用舊 session；`/end` 會結束目前 session。
11. `/continue <task_id>` 會將後續普通文字切換至指定舊 session。
12. Codex 在原 workspace 工作；只有檔案型成果才寫入該 Turn 的 `artifacts/`。
13. 成功時以 Telegram Markdown 回傳文字結果，並只自動傳送 manifest
    指定的 artifacts。

### 使用範例

```text
使用者：/new 幫我檢查登入功能
Bot：Task #100 queued as a new session.
Bot：Task #100 started.
Bot：檢查完成……

使用者：接著修正剛才找到的問題
Bot：Task #100 follow-up queued.
Bot：修正完成……

使用者：/end
Bot：Task #100 session ended.

使用者：/continue 100
Bot：Task #100 session resumed. 後續普通文字會接續此 session。

使用者：再補上測試
Bot：Task #100 follow-up queued.
Bot：測試已補上……
```

### Session 規則

- 每個 Telegram chat 同一時間只有一個作用中的 Task/session。
- `/new` 會停用原本作用中的 session，但不刪除其紀錄。
- 普通文字只會送往目前作用中的 session。
- 每次普通文字被接受並建立 Turn 時，24 小時期限重新起算。
- 超過 24 小時後，下一則普通文字不會自動建立新 session；Bot 會要求使用
  `/new` 或 `/continue <task_id>`。
- `/continue <task_id>` 會停用目前 session，並將指定 Task 設為作用中，同時
  重新起算 24 小時期限。
- `/end` 只停止後續普通文字自動接續，不會取消已排隊或正在執行的 Turn。
- 使用者在上一輪尚未完成時傳送的新訊息會排入佇列，依序使用同一個 Codex
  session 執行。
- session 逾時或 `/end` 都不會刪除 Codex session ID、log、結果或附件。

### Task 與 Turn

- `Task ID` 代表一段可恢復的對話，對使用者保持固定。
- `Turn` 代表該對話中的一次使用者訊息與一次 Codex 執行。
- 每個 Turn 分別保存狀態、prompt、log、final output 與 artifacts。
- `/status` 顯示 Task 的執行狀態與 session 狀態，例如
  `done [active]`、`done [ended]` 或 `done [expired]`。

## 專案結構

```text
codex-telegram-agent/
  app/
    artifacts.py        # Task 目錄與產物掃描
    bot.py              # Telegram command entrypoint
    worker.py           # Background task worker
    db.py               # SQLite schema and task operations
    config.py           # Environment configuration
    codex_runner.py     # Codex CLI subprocess wrapper
    task_followup.py    # Task 結果與 log 讀取
    telegram_utils.py   # Telegram message helpers
  data/
    tasks.sqlite3       # Runtime database
  tasks/
    task-000001/
      turn-000001/
        prompt.txt        # 本輪訊息
        inputs/           # 使用者由 Telegram 上傳的附件
        task.log          # 本輪完整執行 log
        final.md          # 本輪 Codex 最終回覆
        artifacts/
          .delivery.json  # 純文字或自動附件交付清單
          ...             # 圖片、文件等使用者產物
  systemd/
    codex-telegram-agent.service  # 尚未安裝的 systemd unit 範本
  tests/
    test_artifacts.py
    test_codex_runner.py
    test_db.py
    test_task_followup.py
    test_worker.py
  .gitignore            # 排除 secrets 與 runtime data
  .env                  # Runtime secrets and local configuration
  .env.example          # Environment template
  FEATURE_BACKLOG.md    # 待開發與已完成功能
  requirements.txt
  README.md
```

下列內容只存在部署主機，不會提交至 Git：

```text
.env
.venv/
__pycache__/
data/
logs/
outputs/
tasks/
```

## 目前支援的 Telegram 指令

```text
/start
/help
/new <task prompt>
/end
/run <task prompt>
/status
/model [model_id]
/file <task_id> [artifact_id]
/result <task_id>
/log <task_id>
/continue <task_id>
```

`/start` 會回傳目前 chat id 與授權狀態，方便第一次設定 `ALLOWED_CHAT_IDS`。

`/help` 會列出所有目前支援的指令、參數格式與功能說明，並同步更新
Telegram 的 Bot 指令選單。

`/new <task prompt>` 會建立新的 Task 與 Codex session。後續直接傳送普通
文字即可接續同一個 session。建立新 Task 時，原本作用中的 Task 會改為
`ended`。每次接受新訊息後，24 小時閒置期限會重新計算。

`/end` 會結束目前作用中的 session，但不會刪除歷史紀錄，也不會取消已排隊
或正在執行的 Turn。

`/continue <task_id>` 會恢復指定 Task 的 Codex session，並停用目前其他
作用中的 session。下一則普通文字會送往指定 session；此指令本身不會建立
新的 Turn。

`/run <task prompt>` 保留為相容指令，會建立獨立的舊式 Task；新流程建議使用
`/new`。

`/status` 會顯示目前 chat 最近五筆 Task 的執行狀態、session 狀態與初始
prompt 摘要。

`/model` 會顯示目前模型與伺服器允許的模型白名單，並提供按鈕切換。
也可使用 `/model <model_id>` 直接切換。模型偏好以 Telegram chat 為範圍
保存，Bot 重啟後仍會保留，並從下一個新建 Turn 開始生效。

`/file <task_id>` 只允許原任務所屬的 Telegram chat 使用，並列出該 Task
最新一輪的 artifacts，不包含作為文字回覆來源的 `final.md`。使用者可用
Inline Keyboard 選擇單一產物、換頁或下載全部；也可使用
`/file <task_id> <artifact_id>` 作為文字備援。文字結果請使用
`/result <task_id>`。新任務完成時只會自動傳送
`.delivery.json` 明確列出的附件；一般問答即使意外建立 Markdown 檔，也不會
自動作為附件傳送。

`/result <task_id>` 會將指定任務最新一輪的 final output 以 Telegram
Markdown 格式分段回傳。若 Telegram 無法解析該段格式，會自動退回純文字，
避免結果傳送失敗。

`/log <task_id>` 會回傳指定任務最新一輪最近 80 行 log，最多 12,000 字元。

所有依 Task ID 查詢的指令都會驗證 task 所屬 chat id。

## 環境變數

設定檔位置：

```text
/home/ai-agent/codex-telegram-agent/.env
```

必要欄位：

```env
TELEGRAM_BOT_TOKEN=replace_me
ALLOWED_CHAT_IDS=123456789
DEFAULT_WORKSPACE=/home/ai-agent
CODEX_BIN=/home/ai-agent/.local/bin/codex
CODEX_SANDBOX_MODE=danger-full-access
CODEX_MODELS=gpt-5.6,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5,gpt-5.4,gpt-5.4-mini
CODEX_DEFAULT_MODEL=gpt-5.6
TASK_TIMEOUT_SECONDS=5400
DATABASE_PATH=/home/ai-agent/codex-telegram-agent/data/tasks.sqlite3
TASKS_DIR=/home/ai-agent/codex-telegram-agent/tasks
WORKER_POLL_SECONDS=2
SESSION_TIMEOUT_SECONDS=86400
```

`SESSION_TIMEOUT_SECONDS` 預設為 `86400` 秒，也就是 24 小時。期限從
`/new`、`/continue` 或最近一次被接受的普通文字開始計算。

不要將真實 bot token 寫入 Git 或公開文件。

建立 `.env` 後應限制檔案權限：

```bash
chmod 600 /home/ai-agent/codex-telegram-agent/.env
```

`.env.example` 預設使用較保守的 `workspace-write`。目前遠端主機無法
初始化 bubblewrap sandbox，經管理者明確核准後，實際部署環境使用
`CODEX_SANDBOX_MODE=danger-full-access`。

此模式不提供 Codex 內建檔案 sandbox，Codex 可以讀寫 `ai-agent` Linux
帳號原本就有權限存取的檔案。請勿任意擴大該帳號的 sudo 或檔案權限。

## 首次安裝

先在部署主機建立專用 SSH Deploy Key：

```bash
key="$HOME/.ssh/codex_telegram_agent_github_ed25519"
ssh-keygen -t ed25519 -N "" \
  -C "codex-telegram-agent deploy key" \
  -f "$key"
cat "$key.pub"
```

將輸出的公鑰加入私人 GitHub repository 的
**Settings > Deploy keys**。部署主機只需要抓取更新時，不要勾選
`Allow write access`。

確認 SSH 驗證成功後再 clone repository：

```bash
key="$HOME/.ssh/codex_telegram_agent_github_ed25519"
ssh -i "$key" -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new -T git@github.com

GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes" \
  git clone git@github.com:EggMaster0906/codex-telegram-agent.git \
  /home/ai-agent/codex-telegram-agent
cd /home/ai-agent/codex-telegram-agent
git config core.sshCommand \
  "ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

確認 repository 讀取權限：

```bash
cd /home/ai-agent/codex-telegram-agent
git fetch origin
```

`ssh -T` 成功時 GitHub 仍會回傳不提供 shell access，這是正常行為。

## 遠端部署與更新

目前部署位置與 Codex CLI 為：

```text
/home/ai-agent/codex-telegram-agent
/home/ai-agent/.local/bin/codex
```

更新前先確認工作樹乾淨，並備份不可由 Git 還原的 runtime data：

```bash
cd /home/ai-agent/codex-telegram-agent
git status --short --branch

archive="$HOME/codex-telegram-agent-runtime-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$archive" .env data logs outputs tasks
chmod 600 "$archive"

git pull --ff-only origin main
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest discover -s tests -v
```

`.venv/` 與 `__pycache__/` 可重新建立，因此不必放入日常 runtime 備份。

目前 Bot 以手動背景程序執行，程式更新後需要手動重啟。先找出現有 PID：

```bash
pgrep -af -- "python -m app.bot"
```

使用 `kill <PID>` 停止確認過的程序，接著重新啟動：

```bash
cd /home/ai-agent/codex-telegram-agent
mkdir -p logs
nohup ./.venv/bin/python -m app.bot \
  >> logs/bot-manual.log 2>&1 &
```

執行 log 位於：

```text
/home/ai-agent/codex-telegram-agent/logs/bot-manual.log
```

repository 內的 `systemd/codex-telegram-agent.service` 目前只是 unit 範本，
尚未安裝至主機；因此 `systemctl restart codex-telegram-agent` 目前不可用。

## 驗證

執行單元測試與語法檢查：

```bash
cd /home/ai-agent/codex-telegram-agent
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m compileall -q app tests
```

已完成下列端對端驗證：

- Telegram Bot API `getMe` 成功。
- Telegram `/start`、`/run` 與主動訊息發送成功。
- 背景 worker 可執行 Telegram 建立的 Task。
- Codex CLI 可在 Task `artifacts/` 內實際建立檔案。
- `final.md`、artifact 掃描、SQLite migration 與 bot 重啟均正常。

本次 session/Turn 功能另完成：

- Codex JSONL `thread.started` session ID 解析測試。
- 第一輪建立 session、後續 Turn 使用相同 session ID 的 worker 測試。
- 24 小時滑動期限、session 切換、結束與恢復測試。
- 舊 SQLite 資料庫 migration 測試，原有 Task 紀錄保持不變。

## 待開發功能

- 任務取消。
- 超過 Telegram 上限時的替代檔案下載方式。
- 多專案 workspace 白名單。
- systemd 常駐服務與自動重啟。
