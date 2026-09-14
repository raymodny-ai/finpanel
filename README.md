# FinPanel · Virtual Capital

AI Agent 驅動的**虛擬對沖基金公司**。每個 Agent 是一名「員工」，每個部門是一組具備共同目標的 Agent 團隊；每一次研究任務、市場事件或使用者提問，都會觸發一次公司內部工作流，最終產出經過多角色協作、風險審查與 CIO 匯總的**決策備忘錄（Decision Memo）**。

定位：**盤後分析為主**（post-market research），非即時交易系統。LLM 延遲與成本不是瓶頸。

對應設計文件：`FinPanel_v3.1_Final.md`（本目錄上層）。

---

## 架構

前後端拆分：

```
outputs/
├── finpanel/
│   └── engine/                 # Python 3.12 · FastAPI · Agent Engine
│       ├── agent_engine/
│       │   ├── models/         # Pydantic schemas (Task/Opinion/Memo/Envelope…)
│       │   ├── data/           # connectors + indicators + pipeline (確定性 Phase A)
│       │   ├── agents/         # 6 個 MVP 角色 + registry + configs/*.yaml
│       │   ├── prompts/system/ # 8 段式 Prompt Contract (.md)
│       │   ├── orchestrator/   # workflow 狀態機 + permissions (硬性規則) + scheduler
│       │   ├── output/         # writer (寫 frontend/public) + archivist (SQLite)
│       │   ├── llm.py          # OpenRouter client + echo-mode fallback
│       │   ├── main.py         # FastAPI app (:8000)
│       │   └── scripts/daily_run.py   # CLI 盤後審查
│       └── requirements.txt
└── frontend/                   # Next.js 15 · TypeScript · Tailwind · App Router
    ├── app/                    # 頁面 (Lobby/Org/Dept/Agent/Task/Memo/Meetings/Data)
    ├── components/             # shell + agent + memo + task + opinion + query
    ├── lib/                    # types (mirror engine) + store (讀 public/*.json) + utils
    └── public/                 # ← Engine 寫入的 artifacts (data/tasks/memos/lobby/org)
```

**資料流（單向）：**

```
connectors (gold-api / treasury.gov)
   → normalizers → indicators → snapshot  (確定性，無 LLM)
   → agents 经 Orchestrator 状态机：
        Level 0 (parallel): metals-da  ||  macro-strategist
        Level 1           : metals-qm        (reads DA fact_brief)
        Level 2           : risk-cro         (reads DA+QM+Macro)
        Level 3           : cio              (reads all)
   → DecisionMemo + Meeting
   → OutputWriter → frontend/public/*.json
   → Next.js 讀取渲染
```

> **DAG 說明**：MACRO 只讀 market_data、不讀 DA 輸出，故與 DA 同層並行；QM
> 在 `agents/desks/metals_qm.py` 明確讀 DA 的 `fact_brief`，必須等 DA。實測
> Level 0 並行化省下 ~85s（wall clock 589s vs LLM sum 675s）。每個 agent 的
> elapsed_ms 記錄在 `TaskResult.envelope_timings`，`daily_run.py` 結尾會印
> per-agent timing 排序表。

Engine 與 Frontend 之間**只透過 `frontend/public/` 的 JSON 檔案**交換資料（Phase 1 設計：靜態檔案即介面）。`/api/query` 與 `/api/health` 是例外，它們 proxy 到 Engine 的 HTTP API。

---

## 快速開始

### 0. 前置

- Python ≥ 3.12
- Node ≥ 18.18

### 1. Engine — 安裝依賴

```bash
cd finpanel/engine
pip install -r requirements.txt
```

### 2. Engine — 跑一次盤後審查（產生 artifacts）

```bash
cd finpanel/engine
python -m agent_engine.scripts.daily_run --verbose
```

這會：

1. 抓 gold-api.com 現貨（XAU/XAG/BTC）與 Treasury.gov 收益率曲線（含 TIPS）
2. 計算指標（金銀比、2s10s/3m10y 利差、曲線形狀）
3. 依狀態機跑 5 個角色：metals-da → metals-qm → macro-strategist → risk-cro → cio
4. 產生 DecisionMemo + 寫入 `frontend/public/`（data / tasks / memos / lobby.json / org.json）
5. 歸檔到 SQLite（`engine/storage/memory.sqlite`）

> **Echo mode**：未設定 `OPENROUTER_API_KEY` 時，Engine 以確定性佔位輸出跑通全流程（narrative 會以 `[ECHO MODE …]` 開頭）。設定 key 後即切換為真實 LLM 分析。

### 3. Engine —（選用）啟動 HTTP API

```bash
cd finpanel/engine
python -m agent_engine.main          # :8000
```

提供 `POST /api/query`、`POST /api/daily-run`、`GET /api/health|registry|memos|tasks/{id}`。Frontend 的 `/api/query` 會 proxy 到這裡。

### 4. Frontend — 安裝 + 啟動

```bash
cd frontend
npm install
npm run dev        # :3000
# 或 production：
npm run build && npm run start
```

開啟 http://localhost:3000。

### 5. 啟用真實 LLM（三種 backend）

Engine 的 LLM 層有三種模式，由 `LLM_BACKEND` 與 key 有無決定：

| 模式 | 觸發條件 | 說明 |
|---|---|---|
| **echo** | `LLM_BACKEND=openai` 且無 `OPENROUTER_API_KEY` | 確定性佔位輸出，驗證管線用 |
| **openai** | `LLM_BACKEND=openai` + `OPENROUTER_API_KEY` | OpenAI-SDK 打 `OPENROUTER_BASE_URL`（可改指 DashScope compatible-mode） |
| **cli** | `LLM_BACKEND=cli` | shell out 到 Qoder CN CLI（`qoderclicn -p`）；認證走你的 Qoder CN 帳號、扣平台共享積分，**不需要任何第三方 API key** |

#### 5a. openai backend

```bash
# engine/.env 或環境變數
export OPENROUTER_API_KEY=sk-or-...
export LLM_MODEL=openai/gpt-4o-mini        # 或 DashScope：qwen3.8-max
# 走 DashScope 直連時另設：
# export OPENROUTER_BASE_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

#### 5b. cli backend（Qoder CN CLI）

**Step 1 — 登入一次**（在「你自己的終端」，不要在 QoderWork 內）：

```bash
qoderclicn --version        # 確認 ≥ 1.1.26
qoderclicn login            # 開瀏覽器走 Qoder CN OAuth；授權後終端顯示成功
qoderclicn --list-models    # 驗證：應列出帳號可用模型
```

若瀏覽器沒自動開啟，複製終端印出的 URL 手動開啟。登錯帳號先 `qoderclicn logout` 再 login。

> **模型 ID 是 PascalCase-with-hyphen**：`--list-models` 列出的是
> `Qwen3.8-Max`、`Qwen3.8-Flash`、`DeepSeek-V4-Pro`、`GLM-5.3`、
> `Kimi-K3` 等，**不是** `qwen3.8-max`。寫錯不會报錯，CLI 會靜默 fallback
> 到 `Auto`，engine 的 HR-04 守則會偵測到並拋 `LLMError`（見下文）。

**Step 2 — 設定 engine**：

```bash
# engine/.env
LLM_BACKEND=cli
LLM_MODEL=Qwen3.8-Max        # 以 --list-models 實際列出者為準
LLM_TIMEOUT=480              # 盤後審查建議 ≥ 480s；risk-cro 是最慢的 agent
# QODERCLI_BINARY=qoderclicn # 選填；非 PATH 內時給絕對路徑
```

**Step 3 — 跑**：`python -m agent_engine.scripts.daily_run --verbose`。

CLI backend 的行為與取捨：

- **調用形式**：`qoderclicn -p --tools "" --no-session-persistence --permission-mode dont_ask -o text -m <model> --max-output-tokens <n> --system-prompt <text> --attachment <prompt-file>`。
  `--system-prompt` 用 CLI 的真實 slot 取代預設 agent system prompt；user prompt 一律以 `--attachment` 走檔案，避免 cmd.exe argv mangling。
- **Windows node-direct 策略（自動）**：npm shim `qoderclicn.CMD` 是 batch 檔，spawn 時會走 `cmd.exe /c` 二次解析 argv，**multi-line 值會被吃掉**（實測 `--system-prompt` 含換行 → rc=42、stdout/stderr 全空）。Adapter 偵測到 `.CMD`/`.bat` 結尾時自動改走 `node <prefix>/node_modules/@qodercn-ai/qoderclicn/bundle/qoderclicn.js`，繞過 shim。非 Windows（或 shim 是 shell script）則直接 spawn。
- **環境變數隔離（prefix-based）**：spawn 子進程時 scrub 所有 `QODER*` 環境變數（不只 `QODER_CONFIG_DIR` — QoderWork host 會注入 21 個 `QODER*`/`QODERCN*`/`QODERWORK*` 變數，其中 `QODERCN_CONFIG_DIR` 是 CN 版二進位讀的、最陰）。前綴清單在 `config.py:CLI_SCRUB_PREFIXES`，可擴充。從普通終端跑 engine 則無此問題。
- **HR-04 治理守則（程式化執行）**：CLI 對未知模型會印 `"Model X is not available right now; using Auto instead"` 並**靜默降級**，違反 HR-04「不得靜默替換模型/資料源」。Adapter 在 stdout+stderr 掃描此 fallback marker，命中即拋 `LLMError`，讓 orchestrator 走 `_empty_envelope` 而不是把 Auto 的輸出當成 Max 的輸出。
- **schema_hint 注入 prompt**：CLI backend 沒有 server-side `response_format`，`complete_json()` 把 JSON schema 以 `## Required Output Schema` 段落追加到 user prompt 尾端，並強制「每個 array item 的所有 subfield 都必須填」。移除這段會導致 narrative / key_points / evidence_refs 大面積空白。
- **Per-agent model override**：`agents/configs/*.yaml` 可設 `llm_model: Qwen3.8-Flash`，`get_llm(model_override=...)` 會 cache 一個獨立 client 實例。目前 `metals-da` 走 Flash（純資料抽取，不需 Max 推理），其餘走 `LLM_MODEL`。
- 每次呼叫有數秒 CLI 啟動開銷（盤後分析可接受）、扣 Qoder 平台積分。
- 輸出為自由文字；JSON 靠 engine 既有 `_extract_json` 三層兜底解析（direct parse → code fence → balanced-brace scan）。
- 未登入時會拋 `LLMError: qoderclicn is not logged in...`，不會靜默失敗。

---

## 環境變數

| 變數 | 位置 | 預設 | 說明 |
|---|---|---|---|
| `LLM_BACKEND` | Engine | `openai` | `openai` 或 `cli` |
| `OPENROUTER_API_KEY` | Engine | 空 | openai backend 用；空則 echo mode |
| `OPENROUTER_BASE_URL` | Engine | OpenRouter | openai backend 的 endpoint（可改 DashScope） |
| `LLM_MODEL` | Engine | `openai/gpt-4o-mini` | 模型字串（cli backend 例：`Qwen3.8-Max`，**大小寫敏感**） |
| `QODERCLI_BINARY` | Engine | `qoderclicn` | cli backend 的二進位（自動 `shutil.which` 解析；Windows 上 `.CMD` shim 自動繞道 node-direct） |
| `LLM_TIMEOUT` | Engine | `120` | 單次 LLM 呼叫超時秒數；cli backend + risk-cro 建議設 `480` |
| `ENGINE_URL` | Frontend | `http://127.0.0.1:8000` | `/api/query` proxy 目標 |

> Per-agent model override 不走環境變數，改在 `agents/configs/<agent>.yaml`
> 裡加 `llm_model: <Model-Id>`（例：`metals-da.yaml` 已設 `Qwen3.8-Flash`）。

---

## 頁面一覽

| 路徑 | 內容 |
|---|---|
| `/` | 公司大廳：報頭、行情 ticker、向公司提問、最新 memo、active task、部門牆、員工牆 |
| `/org` | 組織檔案：部門樹、角色節點、治理硬性規則 |
| `/d/[deptId]` | 部門頁 |
| `/d/[deptId]/[agentId]` | 員工人事檔案：persona、協作圖、記憶權限、最近發言 |
| `/tasks` `/tasks/[taskId]` | 任務流 + 狀態機視覺化 + Deliberation Stream |
| `/memos` `/memos/[memoId]` | 決策備忘錄列表 + 實體文件風詳情（印章 / 簽名 / 火漆） |
| `/meetings` | 會議中心（Phase 2 預覽） |
| `/data` | 數據控制台：現貨、指標、收益率曲線、資料源 |
| `/api/health` | 前後端 + Engine 健康檢查 |
| `/api/query` | proxy → Engine `POST /api/query` |

---

## MVP 角色（6）

| agent_id | 角色 | 部門 |
|---|---|---|
| `cio` | 首席投資官 | cio-office |
| `pmo` | 專案管理（任務入口） | pmo |
| `metals-da` | 貴金屬數據與新聞分析師 | metals |
| `metals-qm` | 貴金屬量化基金經理 | metals |
| `macro-strategist` | 宏觀策略師 | macro |
| `risk-cro` | 風險官 | risk |

## 治理硬性規則（程式化執行，非 prompt 約束）

- HR-01 DA 不得產出 Decision Memo
- HR-02 QM 不得跳過 RA 成為公司最終意見
- HR-03 CIO 不得刪除 dissenting view
- HR-04 風險紅燈不可被 CIO 覆蓋；**且 LLM/資料源不得靜默替換**（cli backend 偵測到 `"is not available right now"` fallback marker 即拋 `LLMError`）
- HR-05 失效條件為必要欄位（缺失降為 draft）
- HR-06 Archivist 不得改寫原始 narrative

實作於 `engine/agent_engine/orchestrator/permissions.py` 與 `engine/agent_engine/llm_cli.py`。

> **Infra-failure 語意**：agent 超時 / LLM error 會走 `_empty_envelope`，
> 帶 `agent_failure:<reason>` risk flag、role-appropriate fallback stance
> （例：RA → `risk_alert`，DA → `neutral`），並**跳過** permission validation
> — 否則會被誤判成 `stance_not_allowed` 而把真正的 infra 錯誤蓋掉。

---

## 設計語言

**Editorial Trading Terminal** — Bloomberg 終端的技術感 × Financial Times 編輯部的排版氣質。

- 背景 `#0a0c0f`、主色琥珀 `#d97706`、漲 `#10b981`、跌 `#dc2626`、羊皮紙 `#f5efe1`、印章紅 `#a03030`
- 字體：Instrument Serif（標題／敘事）、Chakra Petch（UI）、IBM Plex Mono（數據）
- Agent 卡片＝員工識別證；Memo＝帶印章與火漆的實體文件

---

## 疑難排解

- **前端顯示「尚未產生公司狀態」**：先跑 `python -m agent_engine.scripts.daily_run`。
- **`/api/query` 回 503 engine_unreachable**：Engine HTTP 未啟動，跑 `python -m agent_engine.main`。
- **收益率為空**：週末／假日 Treasury.gov 無當日資料，屬正常；現貨（gold-api）仍會有值。
- **memo 狀態為 draft**：echo mode 下 CIO conclusion 過短／缺證據，觸發 HR-05 降級，屬預期；接真實 LLM 後會轉 published。
- **`LLMError: qoderclicn silently substituted the model (HR-04)`**：`LLM_MODEL` 拼錯（大小寫／連字號）。跑 `qoderclicn --list-models` 對照實際 ID（例：`Qwen3.8-Max` 不是 `qwen3.8-max`）。
- **`LLMError: qoderclicn is not logged in`（明明已 login）**：你在 QoderWork 內跑 engine，host 注入的 `QODERCN_CONFIG_DIR` 讓 CLI 讀錯 config。Adapter 已 prefix-scrub 所有 `QODER*`；若仍失敗，檢查 `config.py:CLI_SCRUB_PREFIXES` 是否被改動，或改從普通終端跑。
- **CLI 回 rc=42、stdout/stderr 全空**：Windows `.CMD` shim 把 multi-line `--system-prompt` 吃了。Adapter 應自動走 node-direct（log 會印 `strategy=node-direct`）；若看到 `strategy=cmd-shim` 代表 `node` 不在 PATH 或 bundle 路徑不對，檢查 `shutil.which("node")` 與 `<npm-prefix>/node_modules/@qodercn-ai/qoderclicn/bundle/qoderclicn.js` 是否存在。
- **某 agent 超時被標成 `stance_not_allowed`**：舊版 bug，已修。infra-failure envelope（帶 `agent_failure:*` flag）現在會跳過 permission validation；若仍看到，確認 `workflow.py:_run_agent_safe` 的 `is_infra_failure` 分支存在。
- **risk-cro 動輒 200s+**：prompt §5 已精簡（每信號 ≤ 3 行）。若仍慢，檢查 `LLM_TIMEOUT` 是否 ≥ 480、`governance.max_output_length` 是否過大（會映射到 `--max-output-tokens`）。
