# FinPanel · Virtual Capital — 公司詳細說明文件

> **文件版本**：v1.0（2026-09-07）
> **適用對象**：新加入的工程師、未來的自己、任何需要接手這個 codebase 的人
> **資訊來源**：所有內容皆由 `outputs/finpanel/` 目錄下的 source of truth 抽出，未使用任何外部推測。每一節的引用位置以 `path:line-range` 標記。

---

## 0. 閱讀地圖

本文件共 11 章。若只想知道「這公司在幹嘛」，讀 §1 + §2 + §5 就夠。若要改 code，讀 §4 + §6 + §7 + §10。若要在本地跑起來，讀 §10 + §11。

| § | 主題 | 適合誰讀 |
|---|---|---|
| 1 | 公司識別 | 所有人 |
| 2 | 組織架構（5 部門 / 6 員工） | 所有人 |
| 3 | 員工詳細檔案 | 想理解每個 agent 的行為 |
| 4 | 治理硬性規則 HR-01 ~ HR-06 | 改 code 前必讀 |
| 5 | 工作流狀態機 / DAG | 想理解一次盤後審查怎麼跑 |
| 6 | 輸出契約（Envelope / Memo） | 改 schema、接前端 |
| 7 | 數據來源 | 想加 connector / 改指標 |
| 8 | 記憶架構 | 想接 ChromaDB |
| 9 | 技術棧與儲存佈局 | DevOps |
| 10 | 操作手冊 | 要在本地跑 |
| 11 | 疑難排解 | 出問題時 |

---

## 1. 公司識別

**FinPanel · Virtual Capital** 是一家 AI Agent 驅動的**虛擬對沖基金公司**。每個 Agent 是一名「員工」，每個部門是一組具備共同目標的 Agent 團隊。每一次研究任務、市場事件或使用者提問，都會觸發一次公司內部工作流，最終產出經過多角色協作、風險審查與 CIO 匯總的**決策備忘錄（Decision Memo）**。

定位是**盤後分析為主**（post-market research），**非即時交易系統** — LLM 延遲與成本不是瓶頸，決策品質才是。這個定位直接影響多項設計取捨：CLI-as-LLM backend 每次呼叫有數秒啟動開銷被認為可接受、risk-cro 可以慢慢跑 200s、CIO 允許 3000 tokens 的輸出預算。

Config-level 的公司名與時區在 `engine/agent_engine/config.py:70-71`：`FIRM_NAME = "FinPanel Virtual Capital"`、`TIMEZONE = "America/New_York"`。

### 1.1 設計語言

**Editorial Trading Terminal** — Bloomberg 終端的技術感 × Financial Times 編輯部的排版氣質（`README.md:243-247`）。

| 元素 | 值 |
|---|---|
| 背景 | `#0a0c0f` |
| 主色（琥珀） | `#d97706` |
| 漲 | `#10b981` |
| 跌 | `#dc2626` |
| 羊皮紙（memo 底色） | `#f5efe1` |
| 印章紅 | `#a03030` |
| 標題／敘事字體 | Instrument Serif |
| UI 字體 | Chakra Petch |
| 數據字體 | IBM Plex Mono |

核心視覺隱喻：**Agent 卡片＝員工識別證**；**Memo＝帶印章與火漆的實體文件**。

---

## 2. 組織架構

公司由 **5 個部門、6 名員工**組成，全部從 `engine/agent_engine/agents/configs/*.yaml` 抽出：

```
FinPanel Virtual Capital
│
├── cio-office/                首席投資官辦公室
│   └── cio                    Chief Investment Officer        (executive)
│
├── pmo/                       專案管理辦公室
│   └── pmo                    Chief of Staff / PMO            (lead)
│
├── metals/                    貴金屬部
│   ├── metals-da              Senior Data & News Analyst      (senior)
│   └── metals-qm              Quantitative Hedge Fund Manager (lead)
│
├── macro/                     宏觀策略部
│   └── macro-strategist       Head of Macro Strategy          (lead)
│
└── risk/                      風險管理部
    └── risk-cro               Chief Risk Officer              (executive)
```

**Daily cron 只跑 5 個 agent**（PMO 不在其中）：`metals-da → metals-qm → macro-strategist → risk-cro → cio`，因為 PMO 只在用戶自然語言提問時才做任務拆解。這個 participant list 硬編碼在 `orchestrator/workflow.py:128`。

**Reporting lines**（從各 YAML 的 `collaboration.reports_to` 抽出）：

| agent | reports_to | must_escalate_to | cannot_override |
|---|---|---|---|
| cio | — | — | risk-cro |
| pmo | cio | cio | cio |
| metals-da | cio | risk-cro, cio | risk-cro, cio |
| metals-qm | cio | risk-cro, cio | risk-cro |
| macro-strategist | cio | cio | risk-cro |
| risk-cro | cio | cio | — |

`risk-cro` 是唯一一個 `cannot_override` 為空的員工 — 這是刻意的，讓 CRO 有絕對的否決權（呼應 HR-04）。

---

## 3. 員工詳細檔案

以下六節是 6 位員工的人事檔案，每一節的欄位都來自該 agent 的 YAML 配置檔。

### 3.1 `cio` — 首席投資官

> **一句話**：公司的最終發言人，負責把所有下屬的意見合成一份可對外發布的決策備忘錄。

- **headcount id**：`cio`
- **display name**：首席投資官
- **title**：Chief Investment Officer
- **department**：`cio-office`
- **role**：`CIO`
- **seniority**：`executive`
- **avatar**：`/avatars/cio.svg`
- **persona**（`configs/cio.yaml:7-11`）：
  > 高層、簡潔、權衡式、注重結論和條件。
  > 你必須明確列出「支持證據」和「反對證據」，絕不隱藏分歧。
  > 你必須為每一個結論寫出「失效條件」（invalidation conditions）。
  > 當分歧過大時，你要求更多研究而不是強行定論。
- **specialty**：cross-asset-allocation、synthesis-under-uncertainty、decision-memo-writing、conviction-calibration
- **supported_assets**：`ALL`
- **working_style**：權衡式、必寫失效條件、分歧過大時要求更多研究
- **tools**：`memo-writer`
- **data_sources**：`gold-api`, `treasury-gov`
- **reasoning_mode**：`structured`
- **memory_access**：short_term ✓ / department ✓ / **firm ✓** / **cross_dept_meeting_records ✓**（全開）
- **governance**：`approval_required=false`、`audit_level=high`、`confidence_threshold=0.4`、`max_output_length=3000`（全公司最長）
- **prompt_file**：`cio.md`
- **trigger_modes**：`scheduled`, `manual`（不接受 `event_driven` — CIO 只在完整流程末端登場）
- **llm_model**：未 override，走全域 `LLM_MODEL`（目前是 `Qwen3.8-Max`）

### 3.2 `pmo` — 專案管理官

> **一句話**：把用戶的自然語言問題翻譯成公司內部可執行任務，不做任何市場判斷。

- **headcount id**：`pmo`
- **display name**：專案管理官
- **title**：Chief of Staff / PMO
- **department**：`pmo`
- **role**：`PMO`
- **seniority**：`lead`
- **avatar**：`/avatars/pmo.svg`
- **persona**（`configs/pmo.yaml:7-10`）：
  > 清晰、中性、工單化、不加入多餘觀點。
  > 你只負責把用戶問題翻譯成公司內部可執行任務。
  > 你從不做市場判斷，從不代替 CIO 總結。
- **specialty**：intent-parsing、task-decomposition、workflow-routing
- **supported_assets**：（空 — 不接市場資料）
- **working_style**：工單化、中性、只關注任務結構
- **tools / data_sources**：皆為空
- **reasoning_mode**：`structured`
- **memory_access**：short_term ✓ / department ✗ / firm ✓ / cross_dept_meeting_records ✗
- **governance**：`approval_required=false`、`audit_level=low`、`confidence_threshold=0.5`、`max_output_length=800`（全公司最短）
- **prompt_file**：`pmo.md`
- **trigger_modes**：`manual`（唯一只有 manual 的員工）
- **llm_model**：未 override

**PMO 只在 `Orchestrator.run_user_query` 路徑中執行**（`workflow.py:134-179`），跑完後把 envelope 當 `seed_envelopes` 塞給 `execute_task`。Daily cron 路徑完全跳過 PMO。

### 3.3 `metals-da` — 貴金屬部 資深數據與新聞分析師

> **一句話**：只報告可驗證的數據和事件，不下投資結論；數據有缺口時明確標記而非用推測填補。

- **headcount id**：`metals-da`
- **display name**：貴金屬部 資深數據與新聞分析師
- **title**：Senior Data & News Analyst, Precious Metals Desk
- **department**：`metals`
- **role**：`DA`（Data Analyst）
- **seniority**：`senior`
- **avatar**：`/avatars/metals-da.svg`
- **persona**（`configs/metals-da.yaml:7-10`）：
  > 冷靜、事實優先、少形容詞、少預測。
  > 你只報告可驗證的數據和事件，不下投資結論。
  > 當數據有缺口時，你會明確標記缺口而不是用推測填補。
- **specialty**：gold、silver、platinum、palladium、central-bank-reserves、geopolitical-news、mining-output、inflation-cross-analysis
- **supported_assets**：`XAU, XAG, GC=F, SI=F, PL=F, PA=F, GDX, TIPS-10Y`
- **working_style**：保守、數據驅動、先事實後觀點、缺口必須明示
- **tools**：`calculator`, `news-search`
- **data_sources**：`gold-api`, `treasury-gov`
- **reasoning_mode**：`structured`
- **memory_access**：short_term ✓ / department ✓ / firm ✗ / cross_dept_meeting_records ✗
- **governance**：`approval_required=false`、`audit_level=medium`、`confidence_threshold=0.3`（全公司最寬）、`max_output_length=2000`
- **prompt_file**：`metals-da.md`
- **trigger_modes**：`scheduled`, `event_driven`, `manual`（三種全開）
- **llm_model**：**`Qwen3.8-Flash`**（per-agent override）

**為什麼用 Flash**：DA 是純資料抽取與條列的角色，不需要 Max 級的推理鏈。實測 latency 從 91.5s 降到 54.2s（-41%），stance/confidence 語意完全保留。Override 機制見 §10.3。

### 3.4 `metals-qm` — 貴金屬部 量化對沖基金經理

> **一句話**：從不說「一定」，只說「在 X 條件下，Y 機率的 Z 結論」；每個信號都必須帶失效條件。

- **headcount id**：`metals-qm`
- **display name**：貴金屬部 量化對沖基金經理
- **title**：Quantitative Hedge Fund Manager, Precious Metals Desk
- **department**：`metals`
- **role**：`QM`（Quant Manager）
- **seniority**：`lead`
- **avatar**：`/avatars/metals-qm.svg`
- **persona**（`configs/metals-qm.yaml:7-10`）：
  > 量化解釋型、帶概率與條件、注重因果約束和樣本條件。
  > 你從不說「一定」，只說「在 X 條件下，Y 機率的 Z 結論」。
  > 每一個信號都必須帶失效條件（invalidation condition）。
- **specialty**：gold-silver-ratio-mean-reversion、real-rate-vs-gold-regression、delta-neutral-hedge、seasonality-factors、momentum-reversal、volatility-regime-detection
- **supported_assets**：`XAU, XAG, GC=F, SI=F, GLD, SLV, GDX`
- **working_style**：量化解釋型、必帶樣本區間與失效條件、拒絕拍腦袋結論
- **tools**：`calculator`, `backtest-runner`, `regression-tool`
- **data_sources**：`gold-api`, `treasury-gov`
- **reasoning_mode**：`structured`
- **playbook_ids**：`pb-gold-silver-ratio`, `pb-gold-real-rate`, `pb-gold-seasonality`（唯一有 playbooks 的員工）
- **memory_access**：short_term ✓ / department ✓ / firm ✗ / cross_dept_meeting_records ✗
- **governance**：`approval_required=false`、`audit_level=medium`、`confidence_threshold=0.35`、`max_output_length=2500`
- **prompt_file**：`metals-qm.md`
- **trigger_modes**：`scheduled`, `event_driven`, `manual`
- **llm_model**：未 override

**关键依賴**：`agents/desks/metals_qm.py:36` 明確讀 DA 的 `fact_brief` — 這是 QM 必須排在 Level 1（DA 之後）而非 Level 0 的唯一原因（見 §5）。

### 3.5 `macro-strategist` — 宏觀策略師

> **一句話**：先給出當前 Regime（增長/通脹/流動性/政策），再談資產含義；從不做細顆粒度建模。

- **headcount id**：`macro-strategist`
- **display name**：宏觀策略師
- **title**：Head of Macro Strategy
- **department**：`macro`
- **role**：`MACRO`
- **seniority**：`lead`
- **avatar**：`/avatars/macro-strategist.svg`
- **persona**（`configs/macro-strategist.yaml:7-10`）：
  > 框架性、跨資產、因果鏈清晰。
  > 你先給出當前的 Regime（增長/通脹/流動性/政策），再談資產含義。
  > 從不做細顆粒度建模，只提供上層背景。
- **specialty**：global-macro-regime、cross-asset-linkage、monetary-policy、inflation-dynamics、growth-cycle、risk-on-risk-off
- **supported_assets**：`ALL`
- **working_style**：框架先行、因果鏈清晰、跨資產視角
- **tools**：`macro-dashboard`
- **data_sources**：`treasury-gov`, `gold-api`
- **reasoning_mode**：`structured`
- **memory_access**：short_term ✓ / department ✓ / **firm ✓** / **cross_dept_meeting_records ✓**
- **governance**：`approval_required=false`、`audit_level=medium`、`confidence_threshold=0.3`、`max_output_length=1800`
- **prompt_file**：`macro-strategist.md`
- **trigger_modes**：`scheduled`, `event_driven`, `manual`
- **llm_model**：未 override

**关键獨立性**：MACRO 只讀 `market_data`、**不讀 DA 的 fact_brief**，因此在 DAG 中與 DA 同層並行（Level 0），是省 latency 的主要來源。

### 3.6 `risk-cro` — 首席風險官

> **一句話**：不討論看多看空，只討論「這個建議是否值得承受」；預設立場是懷疑。

- **headcount id**：`risk-cro`
- **display name**：首席風險官
- **title**：Chief Risk Officer
- **department**：`risk`
- **role**：`RA`（Risk Analyst）
- **seniority**：`executive`
- **avatar**：`/avatars/risk-cro.svg`
- **persona**（`configs/risk-cro.yaml:7-11`）：
  > 保守、針對性強、條件敏感、偏反證邏輯。
  > 你不討論「看多看空」，只討論「這個建議是否值得承受」。
  > 預設立場是懷疑，需要足夠證據才會給綠燈。
  > 寧可降級也不願放行不明確的建議。
- **specialty**：tail-risk-assessment、correlation-risk、drawdown-analysis、liquidity-risk、event-risk、position-sizing-review
- **supported_assets**：`ALL`
- **working_style**：反證邏輯、條件敏感、信息缺失時預設降級
- **tools**：`risk-calculator`, `scenario-simulator`
- **data_sources**：`gold-api`, `treasury-gov`
- **reasoning_mode**：**`critical`**（**全公司唯一用 critical 模式的員工**，其他 5 位都是 structured）
- **memory_access**：short_term ✓ / department ✓ / **firm ✓** / **cross_dept_meeting_records ✓**
- **governance**：`approval_required=false`、`audit_level=high`、`confidence_threshold=0.4`、`max_output_length=1800`
- **prompt_file**：`risk-cro.md`
- **trigger_modes**：`scheduled`, `event_driven`, `manual`
- **llm_model**：未 override

**特殊權力**：唯一 `cannot_override=[]` 的員工（見 §2）；唯一可以對其他所有下屬 agent `can_challenge` 的員工（DA / QM / MACRO 都在清單裡）；紅燈一亮 CIO 不能直接覆蓋，必須召開 Risk Committee 會議（HR-04，見 §4）。

---

## 4. 治理硬性規則 HR-01 ~ HR-06

這六條是**程式化執行**的規則，**不是** prompt 裡的拜託。每一條都有對應的 Python 函式或 adapter 邏輯違反時會 raise 或降級。實作集中在 `engine/agent_engine/orchestrator/permissions.py` 與 `engine/agent_engine/llm_cli.py`。

### 4.1 規則清單

| Rule | 中文敘述 | 執行點 |
|---|---|---|
| **HR-01** | DA 不得產出 Decision Memo | `permissions.py:53-89` `validate_output_permissions` |
| **HR-02** | QM 不得跳過 RA 成為公司最終意見 | 結構性：QM 只能產 `signal_note` / `opinion`，`cio_memo` 只允許 CIO；DAG 強制 QM 在 Level 1、RA 在 Level 2、CIO 在 Level 3 |
| **HR-03** | CIO 不得刪除 dissenting view | `permissions.py:92-117` `validate_memo_hard_rules` — 若 `dissenting_views` 空則加 `dissenting_views_empty` issue → memo 降為 draft |
| **HR-04** | 風險紅燈不可被 CIO 覆蓋；**且 LLM/資料源不得靜默替換** | `permissions.py:120-130` `validate_risk_escalation` + `llm_cli.py` 的 fallback-marker 偵測 |
| **HR-05** | 失效條件為必要欄位（缺失降為 draft） | `permissions.py:105-107`；另含 conclusion <10 字、evidence <1 條的檢查 |
| **HR-06** | Archivist 不得改寫原始 narrative | `output/archivist.py:75-89` — memo 以 `json.dumps(memo.model_dump(mode="json"))` 直接寫入 SQLite，無任何字串轉換 |

### 4.2 角色權限表（`permissions.py:32-42` verbatim）

```python
ALLOWED_OUTPUT_TYPES: dict[RoleTemplate, set[OutputType]] = {
    RoleTemplate.DA:    {OutputType.fact_brief},
    RoleTemplate.QM:    {OutputType.signal_note, OutputType.opinion},
    RoleTemplate.TA:    {OutputType.opinion},
    RoleTemplate.RA:    {OutputType.risk_note},
    RoleTemplate.ET:    {OutputType.execution_plan},
    RoleTemplate.MACRO: {OutputType.macro_brief, OutputType.opinion},
    RoleTemplate.QUANT: {OutputType.signal_note, OutputType.opinion},
    RoleTemplate.PMO:   {OutputType.task_brief},
    RoleTemplate.CIO:   {OutputType.cio_memo, OutputType.meeting_minutes},
}
```

### 4.3 允許的 stance（`permissions.py:45-50` verbatim）

```python
ALLOWED_STANCES: dict[RoleTemplate, set[str]] = {
    RoleTemplate.DA:  {"neutral", "no_view"},
    RoleTemplate.RA:  {"risk_alert", "neutral"},
    RoleTemplate.PMO: {"no_view"},
    # Others may declare any directional stance
}
```

其他角色（QM / MACRO / CIO / TA / ET / QUANT）可使用完整的 `Stance` 集合：`bullish / bearish / neutral / mixed / risk_alert / no_view`。

### 4.4 HR-04 的兩個面向

**面向 A — 紅燈不可覆蓋**：`validate_risk_escalation(envelopes)` 掃過所有 envelope，只要 `agent_id == "risk-cro"` 且满足下列任一條件，就 return `True`：`payload.status == "red"`、`payload.veto_recommendation` truthy、`envelope.escalation_required` truthy。`workflow.py:256` 消費這個布林值決定要不要召開 Risk Committee meeting。

**面向 B — LLM 不得靜默替換**：`qoderclicn` 對未知模型 ID 會印 `"Model X is not available right now; using Auto instead"` 並**靜默 fallback 到 Auto** — 這違反「不得靜默替換模型/資料源」的原則。CLI adapter 在 `llm_cli.py:314-323` 掃描 stdout+stderr，命中此 marker 就拋 `LLMError`，讓 orchestrator 走 `_empty_envelope`（帶 `agent_failure:llm_error` flag）而不是把 Auto 的輸出當成 Max 的輸出。

### 4.5 違規時的處理（`workflow.py:430-440`）

當 `validate_output_permissions` 拋 `PermissionViolation`：
1. 在 envelope.risk_flags 加一條 `permission_violation:<rule>`
2. 把 narrative 覆蓋為 `[BLOCKED BY PERMISSIONS] <rule>. The agent attempted an output outside its authorized scope. Original narrative suppressed.`
3. 清空 payload
4. `confidence = 0.0`、`stance = no_view`

**Infra-failure 例外**：若 envelope.risk_flags 已有 `agent_failure:*` 開頭的條目（超時 / LLM error），**跳過** permission validation — 否則 `_empty_envelope` 用的 fallback stance 會被誤判成 `stance_not_allowed`，把真正的 infra 錯誤蓋掉（`workflow.py:424-429`）。

---

## 5. 工作流狀態機 / DAG

一次完整的盤後審查走 4 層 DAG + 一個 meeting gate。實作在 `orchestrator/workflow.py:183-294` 的 `Orchestrator.execute_task`。

### 5.1 DAG 全景

```
                       market_data (data pipeline)
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       ╔═══════════════╗               ╔═══════════════════╗
       ║  metals-da    ║               ║ macro-strategist  ║    Level 0
       ║  (DA)         ║  parallel     ║ (MACRO)           ║    (asyncio.gather)
       ║  Qwen3.8-Flash║               ║ Qwen3.8-Max       ║
       ╚═══════╤═══════╝               ╚═════════╤═════════╝
               │                                 │
               │       fact_brief ───────────────┤
               ▼                                 │
       ╔═══════════════╗                         │
       ║  metals-qm    ║   Level 1               │
       ║  (QM)         ║   (serial)              │
       ║  Qwen3.8-Max  ║                         │
       ╚═══════╤═══════╝                         │
               │                                 │
               └────────────┬────────────────────┘
                            ▼
                   ╔════════════════╗
                   ║   risk-cro     ║   Level 2
                   ║   (RA)         ║   (serial)
                   ║  Qwen3.8-Max   ║
                   ╚════════╤═══════╝
                            │
                    ┌───────▼───────┐
                    │ validate_risk │  Meeting gate
                    │ _escalation() │  (HR-04)
                    └───────┬───────┘
                            │
                    red/veto│        no issue
                            │            │
                            ▼            │
                   ╔════════════════╗    │
                   ║  meeting       ║    │
                   ║  (optional)    ║    │
                   ╚════════╤═══════╝    │
                            │            │
                            └──────┬─────┘
                                   ▼
                          ╔════════════════╗
                          ║     cio        ║   Level 3
                          ║    (CIO)       ║   (serial)
                          ║  Qwen3.8-Max   ║
                          ╚════════╤═══════╝
                                   ▼
                            DecisionMemo
                                   │
                                   ▼
                             archived (SQLite + frontend/public/memos/)
```

### 5.2 各層細節

| Level | Task 狀態轉換 | 跑哪些 agent | 執行模式 | 讀什麼 |
|---|---|---|---|---|
| (Pre) | — | 無（資料管線） | async | — |
| Level 0 | `assigned → collecting_facts` | role ∈ `(DA, MACRO)` | **parallel** via `asyncio.gather` in `_run_role_group` | `market_data` |
| Level 1 | `collecting_facts → generating_opinions` | role = `QM` | serial | Level 0 的 fact_brief（DA） |
| Level 2 | `generating_opinions → risk_review` | `risk-cro` | serial | 所有前序 envelopes |
| Meeting gate | `risk_review → meeting_optional`（條件性） | 所有相关 agent | serial | HR-04 觸發 |
| Level 3 | `→ cio_summary` | `cio` | serial | 所有前序 envelopes + `task.user_query` |
| Terminal | `cio_summary → archived` | — | — | — |

**為什麼 MACRO 在 Level 0 而不是 Level 1**：MACRO 只讀 `market_data`、不讀 DA 的 fact_brief。把它移到 Level 0 讓它與 DA 並行，實測省下 ~85s（run 3：wall clock 589s vs LLM sum 675s）。

**為什麼 QM 必須在 Level 1**：`agents/desks/metals_qm.py:36` 明確讀 DA 的 fact_brief，硬依賴。

### 5.3 TaskStatus 狀態機（`models/task.py:41-75`）

```python
TaskStatus enum:
  created → assigned → collecting_facts → generating_opinions
                                        → risk_review → meeting_optional → cio_summary → archived
                                                      ↘ cio_summary ↗
  任何狀態 → failed；failed → assigned（重試）
```

非法轉換會 raise `ValueError("Illegal task transition: …")`（`task.py:108-122`）。

### 5.4 其他 Task 相關 enum（`models/task.py:12-38`）

- **TaskCategory**：`asset_opinion`, `macro_review`, `risk_check`, `cross_asset`, `event_response`, `daily_review`, `historical_compare`
- **TaskPriority**：`low`, `normal`, `high`, `urgent`
- **WorkflowMode**：`fast`（DA → QM → Risk → CIO serial）、`meeting`（強制多 agent 開會辯論）
- **TriggerSource**：`user_query`, `daily_cron`, `event_alert`, `playbook`

### 5.5 每 agent 的 elapsed_ms

`TaskResult.envelope_timings: dict[agent_id, elapsed_ms]`（`workflow.py:65`, `workflow.py:408-409`）。`daily_run.py` 結尾會印 per-agent timing 排序表（慢→快），並計算 wall clock 與 LLM sum 的差距來展示並行化收益。

---

## 6. 輸出契約

公司內部所有 agent-to-agent 通訊都走 **AgentOutputEnvelope**；對外發布走 **DecisionMemo**。

### 6.1 AgentOutputEnvelope（`models/output.py:165-186`）

每個 agent 跑完一輪，產出一個 envelope：

| 欄位 | 型別 | 預設 | 意義 |
|---|---|---|---|
| `task_id` | str | required | FK 到 Task |
| `agent_id` | str | required | 生產者 |
| `department_id` | str | required | 生產者的部門 |
| `timestamp` | str | `_utc_now()` | ISO 秒級 UTC |
| `output_type` | OutputType | required | 見 §6.2 |
| `stance` | Stance | `neutral` | 見 §6.3 |
| `confidence` | float (0-1) | `0.5` | 置信度 |
| `evidence_refs` | list[DataRef] | `[]` | 資料引用，見 §6.4 |
| `key_points` | list[str] | `[]` | bullet takeaways |
| `risk_flags` | list[str] | `[]` | 例：`agent_failure:<reason>`, `permission_violation:<rule>` |
| `next_actions` | list[str] | `[]` | 建議後續動作 |
| `escalation_required` | bool | `False` | HR-04 會消費這個 |
| `low_confidence_flag` | bool | `False` | 低於 threshold 時自動設 True |
| `narrative` | str | `""` | 自然語言層（Markdown） |
| `payload` | dict | `{}` | 角色特化的結構化內容 |

### 6.2 OutputType enum（`output.py:25-35`）

`fact_brief`（DA）、`signal_note`（QM）、`opinion`（QM/MACRO/TA/QUANT）、`risk_note`（RA）、`execution_plan`（ET）、`macro_brief`（MACRO）、`task_brief`（PMO）、`cio_memo`（CIO）、`meeting_minutes`（CIO）、`qa_alert`。

### 6.3 Stance enum（`output.py:16-22`）

`bullish`, `bearish`, `neutral`, `mixed`, `risk_alert`, `no_view`。

### 6.4 DataRef — evidence_refs 的 item shape（`output.py:44-51`）

| 欄位 | 型別 | 意義 |
|---|---|---|
| `source` | str | e.g. `"gold-api"`, `"treasury-gov"`；也可以是 upstream agent_id（跨引用） |
| `indicator` | str | e.g. `"XAU_spot"`, `"10Y_yield"`, `"stance"`, `"confidence"` |
| `value` | float/str/bool/None | 觀測值 |
| `date` | str | 觀測時間 ISO |
| `unit` | str | 單位 label |

**跨引用慣例**：`source` 可以是其他 agent 的 id（如 `"metals-qm"`），此時 `indicator` 通常是 `stance` 或 `confidence`，`value` 是字串化的值，`unit` 為空。這讓下游 agent（尤其 risk-cro / cio）可以精確引用上游的判斷。當模型沒主動生成 cross-ref 時，`agents/base.py:_auto_cross_refs` 會兜底：從 `ctx.prior_outputs[:4]` 各抽 stance+confidence 兩條塞進 evidence_refs。

### 6.5 角色特化的 payload 模型（`output.py:75-148`）

- **FactBrief**（DA）：`data_time_range`, `data_sources`, `anomalies: list[Anomaly]`, `gaps`, `news_relevance: list[NewsItem]`, `key_facts`, `supported_questions`
- **SignalNote**（QM）：`indicators_used`, `sample_period`, `current_regime`, `signal_direction`（`long/short/neutral`）, `signal_strength: float`, `invalidation_condition`, `model_summary`, `position_suggestion`, `backtest_snapshot: Optional[dict]`
- **RiskNote**（RA）：`status: RiskStatus`（green/yellow/red）, `risk_budget_opinion`, `tail_risk_warnings: list[str]`, `scenario_stress`, `veto_recommendation: bool`, `veto_reason: Optional[str]`, `downgrade_suggestion: Optional[str]`
- **MacroBrief**（MACRO）：`regime_label`, `growth_view`, `inflation_view`, `liquidity_view`, `policy_view`, `cross_asset_commentary`
- **TaskBrief**（PMO）：`task_category`, `priority="normal"`, `required_departments`, `required_agents`, `recommended_mode="fast"`, `deliverables`, `reasoning`
- **CIOMemoPayload**（CIO）：`title`, `executive_summary`, `final_conclusion`, `main_line`（今日主線）, `supporting_evidence: list[dict]`, `dissenting_views: list[dict]`, `action_items: list[dict]`, `invalidation_conditions: list[str]`, `next_watchpoints: list[str]`, `confidence: float=0.5`

### 6.6 DecisionMemo（`models/decision.py:97-118`）

對外發布的最終文件：

| 欄位 | 型別 | 預設 | 意義 |
|---|---|---|---|
| `memo_id` | str | required | 格式 `memo-YYYYMMDD-<6 hex>` |
| `task_id` | str | required | 來源 task |
| `date` | str | required | 發布日 |
| `title` | str | required | 標題 |
| `owner_agent_id` | str | `"cio"` | 签发者 |
| `participating_depts` | list[str] | `[]` | 參與部門 |
| `participating_agents` | list[str] | `[]` | 參與 agent |
| `executive_summary` | str | `""` | 摘要 |
| `supporting_evidence` | list[dict] | `[]` | 支持證據（HR-05 檢查 ≥1） |
| `dissenting_views` | list[dict] | `[]` | 反對意見（HR-03 檢查非空） |
| `final_conclusion` | str | `""` | 最終結論（HR-05 檢查 ≥10 字） |
| `action_items` | list[ActionItem] | `[]` | 見 §6.7 |
| `invalidation_conditions` | list[str] | `[]` | 失效條件（HR-05 檢查非空） |
| `next_watchpoints` | list[str] | `[]` | 下一步觀察點 |
| `review_date` | str | `""` | 覆核日 |
| `confidence` | float (0-1) | `0.5` | 信心度 |
| `version` | int | `1` | 版本 |
| `status` | MemoStatus | `draft` | 見 §6.8 |
| `created_at` | str | `_utc_now()` | 建立時間 |

### 6.7 ActionItem（`decision.py:80-86`）

| 欄位 | 型別 | 允許值（慣例） |
|---|---|---|
| `asset` | str | e.g. XAU, XAG |
| `action` | str | `hold / add / reduce / hedge / watch / exit` |
| `urgency` | str | `immediate / this_week / monitoring`（預設 `monitoring`） |
| `size` | Optional[str] | e.g. "3% NAV" |
| `condition` | Optional[str] | 觸發條件 |
| `note` | str | 說明 |

### 6.8 MemoStatus（`decision.py:89-94`）

`draft / published / under_review / validated / invalidated`。

**Draft vs Published 的觸發條件**（`workflow.py:517-518`）：

```python
hard_rule_issues = validate_memo_hard_rules(payload)
status = MemoStatus.published if not hard_rule_issues else MemoStatus.draft
```

`validate_memo_hard_rules` 會加 issue 到 list 的條件（任一命中就 draft）：
- `dissenting_views_empty`（HR-03）
- `invalidation_conditions_empty`（HR-05）
- `final_conclusion_too_short`（<10 字）
- `no_supporting_evidence`（<1 條）

---

## 7. 數據來源

公司目前接兩個 connector，皆**無需 API key、無需 proxy**。註冊表在 `data/connectors/registry.py:12-15`：`{"gold-api": GoldApiConnector, "treasury-gov": TreasuryGovConnector}`。

### 7.1 `gold-api`（`data/connectors/gold_api.py`）

- **Base URL**：`https://api.gold-api.com/price`
- **支援 symbol**：`XAU`, `XAG`, `BTC`
- **回傳 shape**：`{"name": "Gold", "price": 2645.3, "symbol": "XAU", "updatedAt": "...", "updatedAtReadable": "..."}`
- **限制**：**只回當前現貨，沒有歷史**。connector 接受 `start`/`end` 參數只是為介面合規，實際永遠回最新 snapshot。
- **Unit**：XAU/XAG 為 `USD/oz`，其餘為 `USD`
- **normalize() 輸出**：`NormalizedData(source_id="gold-api", symbol, unit, observations=[{date, value, field="spot", timestamp}], meta={name, updated_at_readable, snapshot_only: True})`

### 7.2 `treasury-gov`（`data/connectors/treasury_gov.py`）

- **Base URL**：`https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml`
- **兩個 dataset**：
  - Nominal：`?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM`
  - Real（TIPS）：`?data=daily_treasury_real_yield_curve&field_tdr_date_value_month=YYYYMM`
- **格式**：Atom XML，namespaces `atom`, `m`（metadata）, `d`（dataservices）
- **Nominal tenor**：`1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y` → `d:BC_1MONTH … d:BC_30YEAR`
- **Real (TIPS) tenor**：`5Y_REAL, 7Y_REAL, 10Y_REAL, 20Y_REAL, 30Y_REAL` → `d:DFII5 … d:DFII30`
- **月度抓取**：`fetch()` 用 `_month_range()` 遍歷 `[start, end]` 的每個月份；feed 抓失敗會**靜默略過**（回 None），因為 Treasury 當月 feed 常在月初尚未發布。
- **normalize() 輸出**：observations `{date, symbol, value, field: "yield" | "real_yield"}`；unit `percent_nominal` 或 `percent_real`

### 7.3 指標（`data/indicators/`）

**metals.py**：
- `gold_silver_ratio(gold, silver) -> Optional[float]` — 用最後一個觀測值算 g/s
- `real_yield_from_tips(tips_10y) -> float` — identity helper
- `real_rate_regression(gold, real_rates, window=60)` — Pearson 相關 + polyfit 斜率 + R²；n<10 回 None
- `mining_cost_curve_placeholder()` — 靜態 AISC 曲線（25/50/75/90 分位 → 950/1180/1420/1620 USD/oz，2025 匯總）
- `seasonality_factor()` — 靜態月度平均漲跌率字典；最強 `[1,2,8,11]`，最弱 `[3,5,9]`

**rates.py**：
- `term_spread(long, short) -> float`
- `yield_changes(current, previous) -> dict[str, float]`（bps）
- `curve_shape(spreads)` 分類器：讀 `10Y_2Y`, `10Y_3M`，回 `inverted / partially_inverted / flat / normal / unknown`（spread <0.25 判為 flat）

**technical.py**：純 numpy，無 TA-Lib。函式：`sma`, `ema`, `rsi(window=14, Wilder smoothing)`, `macd(fast=12, slow=26, signal=9)`, `bollinger_bands(window=20, num_std=2.0)`, `zscore(window=20)`。

### 7.4 DataPipeline（`data/pipeline.py`）

`run(target_date)` 一次跑完：
1. Lookback window：`start = target_date - 90 days`
2. 抓 gold-api `["XAU", "XAG"]` → `results["spot"][symbol]`
3. 抓 treasury `["ALL"]` → 拆 nominal/real bucket
4. 計算 indicators：`spread_10y_2y`, `spread_10y_3m`, `curve_shape`, `gold_silver_ratio`
5. Gold-real-rate regression：**目前 emit note 但 correlation=None**（"Historical regression requires Yahoo Finance proxy (Phase 3+)"）
6. `macro_context` snapshot：`tips_10y`, `ust_10y`, `ust_2y`, `ust_30y`, `curve_shape`
7. 寫兩份 JSON：`<FRONTEND_PUBLIC>/data/<YYYY-MM-DD>_metals_rates.json` + `<FRONTEND_PUBLIC>/data/latest.json`

**注意**：`data/normalizers/` 目錄只有 `__init__.py`，**沒有 normalizer 模組**。正規化邏輯內嵌在每個 connector 的 `normalize()` 方法裡。

---

## 8. 記憶架構

### 8.1 三層訪問模型（`models/agent.py:43-49`）

每位員工的 YAML 有四個布林 flag 決定他能讀哪一層記憶：

| Flag | 預設 | 有權限的員工 |
|---|---|---|
| `short_term` | true | 全部 6 位 |
| `department_memory` | true | 除 pmo 外的 5 位 |
| `firm_memory` | false | cio, pmo, macro-strategist, risk-cro（4 位） |
| `cross_dept_meeting_records` | false | cio, macro-strategist, risk-cro（3 位） |

`firm_memory` 是「公司級長期記憶」（例如所有歷史 memo）；`cross_dept_meeting_records` 是「跨部門會議記錄」。DA / QM 只能看部門內 + 短期，這是刻意的權限收斂。

### 8.2 MemoryType enum（`models/memory.py:16-22`）

`decision_memo`, `meeting_minutes`, `research_note`, `failed_idea`, `opinion_snapshot`, `kpi_record`。

`failed_idea` 是 risk-cro prompt §7 特別要求引用的「Failed Ideas Archive」— 但目前 Archivist 尚未主動產生此類型的 entry（Phase 3+）。

### 8.3 MemoryEntry 模型（`models/memory.py:25-40`）

欄位：`id`, `type`, `department_id`, `agent_ids: list`, `task_id`, `date`, `title`, `content`（Markdown）, `tags: list`, `related_memo_ids: list`, `retrieval_count`, `created_at`, `embedding: Optional[list[float]]`（註解：`filled by ChromaDB layer`）。

### 8.4 Archivist — SQLite 持久化（`output/archivist.py`）

**重要設計決策**：Archivist 在 FinPanel v3.1 中**不是 LLM Agent**，而是**確定性 Python 模組**。它的工作（寫會議記錄、索引 memo、追加到 firm memory）全都是確定性的，沒必要花 LLM token。

- **DB 路徑**：`engine/storage/memory.sqlite`
- **Schema**：`memory_entries` 表，`INSERT OR REPLACE` 冪等寫入
- **索引**：`idx_memory_type`（type）、`idx_memory_date`（date DESC）
- **`archive_memo(memo)`**：id = `mem-<memo_id>`, type = `decision_memo`, content = **`json.dumps(memo.model_dump(mode="json"))`** — 逐字寫入，無任何字串轉換。這是 **HR-06 的實作**。tags 由 `_extract_memo_tags` 抽 participating_depts ∪ action-item assets（大寫化）。
- **`archive_meeting(meeting)`**：id = `mem-<meeting_id>`, type = `meeting_minutes`, tags = `[meeting_type.value]`, related_memo_ids = `list(meeting.decisions)`
- **`recent_memos(limit=10)`**：按 date DESC, created_at DESC 排序
- **`search(query, limit=10)`**：`LOWER(title) LIKE ? OR LOWER(content) LIKE ?` — 註解明說「Naive substring search — ChromaDB will replace this in Phase 3.」

### 8.5 ChromaDB 狀態

**尚未接上**。`Settings.ENABLE_CHROMADB = false`（env var 可開，但 codebase 沒有任何 `import chromadb`）。`MemoryEntry.embedding` 是保留欄位。

Orchestrator 的 memo 檢索（`workflow.py:557-589`）是 Phase-1 的替代方案：掃描 `<FRONTEND_PUBLIC>/memos/` 下最多 30 個 JSON 檔，用 `json.dumps(m).upper()` 做 asset 子字串比對。註解明說「This is a Phase-1 stand-in for ChromaDB RAG (Phase 3+)」。

**Per-task memo cache**（run 4 新增）：`Orchestrator._memo_cache` 是 dict，key = `(task_id, tuple(sorted(assets)), limit)`。同一個 task 內 5 個 agent 共用一次掃描，`execute_task` 開頭 `.clear()` 確保跨 task 不會拿到 stale 資料。log 層級為 DEBUG，可用 `LOG_LEVEL=DEBUG` 觀察 HIT/MISS。

---

## 9. 技術棧與儲存佈局

### 9.1 Python 後端

- **Python 版本**：`pyproject.toml:9` 要求 `>=3.11`；README 建議 `>=3.12`；實際 `__pycache__/*.cpython-312.pyc` 顯示執行環境是 **Python 3.12**
- **Package name**：`agent-engine`（version `0.1.0`）
- **Console script**：`finpanel-daily = agent_engine.scripts.daily_run:main`

**依賴（`requirements.txt`）**：

| Package | Constraint |
|---|---|
| fastapi | >=0.115.0 |
| uvicorn[standard] | >=0.32.0 |
| pydantic | >=2.9.0 |
| pydantic-settings | >=2.6.0 |
| python-dotenv | >=1.0.1 |
| httpx | >=0.27.0 |
| aiohttp | >=3.10.0 |
| openai | >=1.55.0 |
| apscheduler | >=3.10.4 |
| pandas | >=2.2.0 |
| numpy | >=2.1.0 |
| PyYAML | >=6.0.2 |
| rich | >=13.9.0 |
| typer | >=0.13.0 |

**HTTP API**（`main.py`，port `:8000`）：
- `POST /api/query` — 用戶自然語言提問入口
- `POST /api/daily-run` — 觸發一次盤後審查
- `GET /api/health` — 健康檢查（含 llm_model）
- `GET /api/registry` — 列出所有 agent config
- `GET /api/memos` — 最近 memo
- `GET /api/tasks/{id}` — task 詳情

### 9.2 前端

- **框架**：Next.js 15 + TypeScript + Tailwind + App Router（`README.md:30`）
- **Node 版本**：`>=18.18`
- **注意**：當前 snapshot 中 `frontend/package.json` **不存在**（可能未 commit 或被 gitignore）。實際版本只能從 README 宣稱得知。
- **實際存在目錄**：`frontend/public/{agents, avatars, data, meetings, memos}` 與 `frontend/src/{agents, app, components, hooks, lib, styles}`；`src/app/` 下有 `api, chat, cio, d, data, history, meetings, memos, org, risk, tasks`
- **前端 env**：`ENGINE_URL` 預設 `http://127.0.0.1:8000`（`/api/query` proxy target）

### 9.3 Engine ↔ Frontend 資料契約

**只透過 `frontend/public/` 下的 JSON 檔案交換資料**（Phase 1 設計：靜態檔案即介面）。`/api/query` 與 `/api/health` 是例外，它們 proxy 到 Engine 的 HTTP API。

### 9.4 儲存佈局（`config.py:19-26`）

| 路徑 | 常數 | 用途 |
|---|---|---|
| `engine/agent_engine/prompts/` | `PROMPTS_DIR` | 8 段式 Prompt Contract `.md` |
| `engine/agent_engine/agents/configs/` | `AGENT_CONFIGS_DIR` | 6 個 YAML |
| `engine/storage/` | `STORAGE_DIR` | `memory.sqlite`, `cli-scratch/`, `daily_run_*.log` |
| `frontend/public/` | `FRONTEND_PUBLIC` | Engine 寫入的所有 JSON artifacts |
| `frontend/public/data/` | — | `<date>_metals_rates.json`, `latest.json` |
| `frontend/public/memos/` | — | DecisionMemo JSON |
| `frontend/public/agents/` | — | Agent envelope JSON |

### 9.5 LLM Backend（三種模式）

| Mode | 觸發條件 | 行為 |
|---|---|---|
| **echo** | `LLM_BACKEND=openai` 且無 `OPENROUTER_API_KEY` | 確定性佔位；narrative 前綴 `[ECHO MODE …]` |
| **openai** | `LLM_BACKEND=openai` + key | OpenAI SDK 打 `OPENROUTER_BASE_URL`（可改 DashScope compatible-mode） |
| **cli** | `LLM_BACKEND=cli` | Shell out 到 `qoderclicn -p`；認證走 Qoder CN OAuth，扣平台共享積分，**不需第三方 API key** |

### 9.6 環境變數總表

| 變數 | 預設 | 位置 | 說明 |
|---|---|---|---|
| `LLM_BACKEND` | `openai` | Engine | `openai` / `cli` |
| `OPENROUTER_API_KEY` | `""` | Engine | openai backend 用；空則 echo mode |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Engine | 可改指 DashScope |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Engine | cli backend 例：`Qwen3.8-Max`（大小寫敏感） |
| `LLM_TEMPERATURE` | `0.3` | Engine | — |
| `LLM_MAX_TOKENS` | `2000` | Engine | — |
| `LLM_TIMEOUT` | `120` | Engine | cli + risk-cro 建議 `480` |
| `QODERCLI_BINARY` | `qoderclicn` | Engine | cli backend 二進位 |
| `CLI_SCRUB_ENV` | `["QODER_AGENT_SDK_ENTRYPOINT", "QODER_WORK_INTEGRATION_MODE", "QODER_CONFIG_DIR"]` | Engine | 精準 scrub 清單 |
| `CLI_SCRUB_PREFIXES` | `["QODER"]` | Engine | 前綴 scrub 清單 |
| `FRED_API_KEY` | `""` | Engine | 宣告但無 connector 使用 |
| `FINNHUB_API_KEY` | `""` | Engine | 宣告但無 connector 使用 |
| `LOG_LEVEL` | `INFO` | Engine | — |
| `FIRM_NAME` | `FinPanel Virtual Capital` | Engine | — |
| `TIMEZONE` | `America/New_York` | Engine | — |
| `ENABLE_CHROMADB` | `false` | Engine | Feature flag，未接 |
| `ENABLE_DEBATE` | `true` | Engine | Meeting 機制開關 |
| `ENGINE_URL` | `http://127.0.0.1:8000` | Frontend | `/api/query` proxy target |

---

## 10. 操作手冊

### 10.1 前置

- Python ≥ 3.12（3.11 也能跑，但 3.12 是實測環境）
- Node ≥ 18.18（前端 build 需要）
- 若要走 cli backend：`qoderclicn` ≥ 1.1.26（`npm i -g @qodercn-ai/qoderclicn`）

### 10.2 Engine 安裝與執行

```bash
cd finpanel/engine
pip install -r requirements.txt

# 跑一次盤後審查（產生 artifacts）
python -m agent_engine.scripts.daily_run --verbose

# 或（選用）啟動 HTTP API
python -m agent_engine.main          # :8000
```

`daily_run` 會：
1. 抓 gold-api.com（XAU/XAG/BTC）+ Treasury.gov 收益率曲線（含 TIPS）
2. 計算指標（金銀比、2s10s / 3m10y 利差、曲線形狀）
3. 依 §5 DAG 跑 5 個角色
4. 產生 DecisionMemo + 寫入 `frontend/public/`
5. 歸檔到 `engine/storage/memory.sqlite`

### 10.3 CLI Backend 設定（推薦）

**Step 1 — 在自己的終端登入一次**（不要在 QoderWork 內）：

```bash
qoderclicn --version        # ≥ 1.1.26
qoderclicn login            # 開瀏覽器走 OAuth
qoderclicn --list-models    # 驗證可用模型清單
```

模型 ID 是 **PascalCase-with-hyphen**：`Qwen3.8-Max`, `Qwen3.8-Flash`, `DeepSeek-V4-Pro`, `GLM-5.3`, `Kimi-K3` 等。**不是** `qwen3.8-max`。拼錯不會报錯，CLI 會靜默 fallback 到 Auto，然後被 HR-04 守則抓到並拋 `LLMError`。

**Step 2 — 設定 engine**（`engine/.env`）：

```
LLM_BACKEND=cli
LLM_MODEL=Qwen3.8-Max
LLM_TIMEOUT=480
# QODERCLI_BINARY=qoderclicn   # 選填
```

**Step 3 — Per-agent model override**（選填）：在 `agents/configs/<agent>.yaml` 加 `llm_model: Qwen3.8-Flash`。`get_llm(model_override=...)`（`llm.py`）會 cache 一個獨立 client 實例。目前只有 `metals-da.yaml` 這樣設（DA 是純資料抽取，Flash 就夠，實測省 41% latency）。

### 10.4 CLI Backend 底層細節

**調用形式**：
```
qoderclicn -p --tools "" --no-session-persistence --permission-mode dont_ask \
           -o text -m <model> --max-output-tokens <n> \
           --system-prompt <text> --attachment <prompt-file>
```

- `--system-prompt` 用 CLI 的真實 slot 取代預設 agent system prompt
- `--attachment` 讓 user prompt 走檔案，避免 cmd.exe argv mangling
- `--tools ""` 禁用所有工具（純 LLM、不動檔案）
- `--no-session-persistence` 不污染 session 庫
- `--permission-mode dont_ask` 免互動確認

**Windows node-direct 策略**（自動）：npm shim `qoderclicn.CMD` 是 batch 檔，spawn 時會走 `cmd.exe /c` 二次解析 argv，multi-line 值會被吃掉（實測 `--system-prompt` 含換行 → rc=42、stdout/stderr 全空）。Adapter 偵測到 `.CMD`/`.bat` 結尾時自動改走：

```
node <prefix>/node_modules/@qodercn-ai/qoderclicn/bundle/qoderclicn.js
```

log 會印 `strategy=node-direct` 或 `strategy=cmd-shim`（後者代表退化，通常伴隨 rc=42 錯誤）。

**環境變數隔離**（prefix-based）：spawn 子進程時 scrub 所有 `QODER*` 環境變數。QoderWork host 會注入 21 個 `QODER*`/`QODERCN*`/`QODERWORK*` 變數，其中 `QODERCN_CONFIG_DIR` 是 CN 版二進位讀的、最陰。前綴清單在 `config.py:CLI_SCRUB_PREFIXES`。

**schema_hint 注入 prompt**：CLI backend 沒有 server-side `response_format`，`complete_json()` 把 JSON schema 以 `## Required Output Schema` 段落追加到 user prompt 尾端，並強制「每個 array item 的所有 subfield 都必須填」。移除這段會導致 narrative / key_points / evidence_refs 大面積空白。

### 10.5 前端

```bash
cd frontend
npm install
npm run dev          # :3000 development
# 或
npm run build && npm run start    # production
```

### 10.6 頁面一覽

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

## 11. 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| 前端顯示「尚未產生公司狀態」 | 從未跑過 daily_run | `python -m agent_engine.scripts.daily_run` |
| `/api/query` 回 503 engine_unreachable | Engine HTTP 未啟動 | `python -m agent_engine.main` |
| 收益率為空 | 週末／假日 Treasury.gov 無當日資料 | 正常；現貨（gold-api）仍會有值 |
| Memo 狀態 draft | HR-05 檢查未過（conclusion 過短／缺證據／缺失效條件） | 接真實 LLM 或看 `hard_rule_issues` log |
| `LLMError: qoderclicn silently substituted the model (HR-04)` | `LLM_MODEL` 拼錯 | 跑 `qoderclicn --list-models` 對照實際 ID |
| `LLMError: qoderclicn is not logged in`（明明已 login） | QoderWork host 注入的 `QODERCN_CONFIG_DIR` 讓 CLI 讀錯 config | Adapter 已 prefix-scrub 所有 `QODER*`；若仍失敗改從普通終端跑 |
| CLI rc=42、stdout/stderr 全空 | Windows `.CMD` shim 把 multi-line 吃了 | log 應顯示 `strategy=node-direct`；若顯示 `cmd-shim` 檢查 `node` 是否在 PATH |
| Agent 超時被標 `stance_not_allowed` | 舊版 bug，已修 | 確認 `workflow.py:_run_agent_safe` 的 `is_infra_failure` 分支存在 |
| risk-cro 動輒 200s+ | Prompt 已精簡（每信號 ≤3 行） | 檢查 `LLM_TIMEOUT ≥ 480`、`governance.max_output_length` 是否過大 |
| Memo 有 narrative 但 key_points 空 | 模型偷懶 | `agents/base.py:_build_envelope` 有 safety net 會從 narrative 抽 bullet；若仍空檢查 prompt 是否被改動 |
| Evidence_refs 只有 metals-da 有，其他都空 | 舊版 bug，已修 | 確認 `EVIDENCE_REFS_SCHEMA` 在 base.py 存在，且所有 4 個 agent 的 schema_hint 都 import 了它 |

---

## 12. 術語表

| 術語 | 意義 |
|---|---|
| **Agent** | 一位「員工」；有 YAML config + Prompt Contract + Python 實作 |
| **Envelope** | Agent 每輪的輸出包裝（`AgentOutputEnvelope`） |
| **Memo** | 對外發布的最終決策文件（`DecisionMemo`） |
| **DAG** | Level 0/1/2/3 的執行順序，見 §5 |
| **HR-XX** | 治理硬性規則，程式化執行，見 §4 |
| **Stance** | Agent 的立場（bullish/bearish/neutral/mixed/risk_alert/no_view） |
| **OutputType** | Envelope 的 payload 類型（fact_brief/signal_note/…） |
| **DataRef** | Evidence_refs 的 item，五欄：source/indicator/value/date/unit |
| **Cross-ref** | DataRef 中 source 為其他 agent_id 的引用 |
| **Infra-failure** | Agent 超時 / LLM error 導致的 `_empty_envelope`，帶 `agent_failure:*` flag |
| **Node-direct** | Windows 上繞過 `.CMD` shim、直接 spawn node 的策略 |
| **HR-04 fallback marker** | `"is not available right now"` — CLI 靜默降級的信號 |
| **Meeting gate** | Level 2 之後的 HR-04 檢查點，決定是否召開 Risk Committee |
| **Playbook** | QM 使用的預定義策略腳本（`pb-gold-silver-ratio` 等） |
| **Echo mode** | 無 API key 時的確定性佔位輸出，用於驗證管線 |
| **ChromaDB** | 向量 DB，Phase 3+ 預定接上，目前 flag 為 false |

---

## 附錄 A — 檔案對照表

| 想改的東西 | 去哪個檔案 |
|---|---|
| 某 agent 的 persona / specialty / governance | `engine/agent_engine/agents/configs/<agent>.yaml` |
| 某 agent 的 8 段式 Prompt Contract | `engine/agent_engine/prompts/system/<agent>.md` |
| 某 agent 的 user prompt 建構邏輯 | `engine/agent_engine/agents/<agent>.py` 或 `agents/desks/<agent>.py` |
| DAG 順序 / 並行策略 | `engine/agent_engine/orchestrator/workflow.py:execute_task` |
| HR-01 ~ HR-06 的執行邏輯 | `engine/agent_engine/orchestrator/permissions.py` |
| Envelope schema | `engine/agent_engine/models/output.py` |
| Memo schema | `engine/agent_engine/models/decision.py` |
| Task 狀態機 | `engine/agent_engine/models/task.py` |
| LLM adapter（openai backend） | `engine/agent_engine/llm.py` |
| LLM adapter（cli backend） | `engine/agent_engine/llm_cli.py` |
| Connector | `engine/agent_engine/data/connectors/<name>.py` |
| Indicator | `engine/agent_engine/data/indicators/<name>.py` |
| Pipeline | `engine/agent_engine/data/pipeline.py` |
| SQLite 歸檔 | `engine/agent_engine/output/archivist.py` |
| 寫 frontend/public | `engine/agent_engine/output/writer.py` |
| HTTP API | `engine/agent_engine/main.py` |
| CLI 盤後審查 | `engine/agent_engine/scripts/daily_run.py` |
| 全域設定 | `engine/agent_engine/config.py` |
| 環境變數 | `engine/.env` |

## 附錄 B — 一次 daily_run 的時間軸（實測 run 4，2026-09-07）

```
t=0s      pipeline.run() — 抓 gold-api (XAU/XAG) + treasury (4 個月 nominal+real)
t=5s      Level 0 啟動：metals-da (Flash) ‖ macro-strategist (Max) 並行
t=59s     metals-da 完成（54.2s）stance=neutral conf=0.25
t=108s    macro-strategist 完成（102.7s）stance=neutral conf=0.15
t=108s    Level 1 啟動：metals-qm
t=264s    metals-qm 完成（155.8s）stance=neutral conf=0.20
t=264s    Level 2 啟動：risk-cro
t=411s    risk-cro 完成（146.7s）stance=risk_alert conf=0.78
t=411s    Meeting gate：validate_risk_escalation() → status=yellow，不召開
t=411s    Level 3 啟動：cio
t=624s    cio 完成（213.3s）stance=neutral conf=0.35
t=624s    Memo 生成 → validate_memo_hard_rules() → 通過 → status=published
t=625s    Archivist 寫 SQLite + Writer 寫 frontend/public/
t=625s    Summary print：wall clock 623.0s / LLM sum 672.6s
```

**Memo**：`memo-20260907-5e8c09`「貴金屬部：XAU/XAG 中性（僅限觀察）— 數據黑洞持續，凍結方向性曝險」

---

*End of handbook.*
