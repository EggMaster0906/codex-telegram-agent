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
  -> Task 獨立目錄
  -> Telegram 文字與檔案回傳
```

## 現有功能

- 只接受 `ALLOWED_CHAT_IDS` 內的 Telegram chat。
- 使用 SQLite 保存 prompt、狀態、workspace 與輸出路徑。
- 背景 worker 依序執行 pending tasks。
- 透過 Codex CLI 非互動模式執行任務。
- 每個 Task 使用獨立目錄保存 prompt、log、final output 與 artifacts。
- 任務完成後將 final output 分段回傳 Telegram。
- 一般任務預設只回傳 Telegram 文字。
- 僅自動傳送 Agent 在 delivery manifest 中指定的 artifacts。
- 支援 `/result <task_id>` 重新查看文字結果。
- 支援 `/log <task_id>` 查看最近 80 行執行 log。
- 支援 `/continue <task_id> <後續問題>` 延伸已完成的任務。
- 支援 `/file <task_id>` 重新下載 final output 與 artifacts。
- 支援 `/help` 列出所有目前可用的指令與功能。
- 下載時驗證 task 所屬 chat id，避免跨使用者讀取。
- 啟動時自動 migration 既有 SQLite schema。

## 任務流程

1. 使用者透過 `/run <prompt>` 建立任務。
2. Bot 將任務寫入 SQLite，狀態設為 `pending`。
3. Bot 建立 `tasks/task-XXXXXX/` 與 `artifacts/`。
4. Worker 將任務改為 `running` 並呼叫 Codex CLI。
5. Codex 在原 workspace 工作；只有檔案型成果才寫入該 Task 的 `artifacts/`。
6. Codex 寫入 `.delivery.json`，聲明本次為純文字或列出應交付附件。
7. Worker 保存完整 log 與 Codex 最終回覆。
8. 成功時回傳文字結果，並只自動傳送 manifest 指定的 artifacts。
9. 使用者之後可查詢結果與 log，或建立保留原始上下文的後續任務。
10. 使用者仍可透過 `/file <task_id>` 重新下載所有任務產物。

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
    task_followup.py    # Task 結果、log 與後續提問上下文
    telegram_utils.py   # Telegram message helpers
  data/
    tasks.sqlite3       # Runtime database
  tasks/
    task-000001/
      prompt.txt        # 原始任務內容
      task.log          # 完整執行 log
      final.md          # Codex 最終回覆
      artifacts/
        .delivery.json  # 純文字或自動附件交付清單
        ...             # 圖片、文件等使用者產物
  systemd/
    codex-telegram-agent.service  # 尚未安裝的 systemd unit 範本
  tests/
    test_artifacts.py
    test_db.py
    test_task_followup.py
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
/run <task prompt>
/status
/file <task_id>
/result <task_id>
/log <task_id>
/continue <task_id> <follow-up question>
```

`/start` 會回傳目前 chat id 與授權狀態，方便第一次設定 `ALLOWED_CHAT_IDS`。

`/help` 會列出所有目前支援的指令、參數格式與功能說明，並同步更新
Telegram 的 Bot 指令選單。

`/run <task prompt>` 會建立新 Task 並立即回覆 task id。

`/status` 會顯示目前 chat 最近五筆任務與狀態。

`/file <task_id>` 只允許原任務所屬的 Telegram chat 下載，並傳送該 Task
的 `final.md` 與所有 artifacts。新任務完成時只會自動傳送
`.delivery.json` 明確列出的附件；一般問答即使意外建立 Markdown 檔，也不會
自動作為附件傳送。

`/result <task_id>` 會將指定任務的 final output 分段回傳。

`/log <task_id>` 會回傳指定任務最近 80 行 log，最多 12,000 字元。

`/continue <task_id> <follow-up question>` 只接受已完成且有 final output 的
任務。Bot 會將原始 prompt、前次 final output 與新問題組成新的 Task，
沿用原本 workspace，並以 `parent_task_id` 保存任務追蹤關係。

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
TASK_TIMEOUT_SECONDS=5400
DATABASE_PATH=/home/ai-agent/codex-telegram-agent/data/tasks.sqlite3
TASKS_DIR=/home/ai-agent/codex-telegram-agent/tasks
WORKER_POLL_SECONDS=2
```

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

## 待開發功能

- `/result <task_id>` 與 `/log <task_id>`。
- `/continue <task_id>` 與 Codex session resume。
- 任務取消。
- `/files <task_id>` artifact 清單。
- Artifact metadata 資料表。
- 多專案 workspace 白名單。
- systemd 常駐服務與自動重啟。
