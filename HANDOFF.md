# HANDOFF

更新：2026-11-15 · by Codex · HEAD `a8ef069`
<!-- 規則見 AGENTS.md「Handoff」。上限 150 行；過期內容移到 docs/archive/HANDOFF-YYYY-MM.md。 -->

## ▶ 下一步（只放一件，必須帶 #Issue）
#21 審查並落地 PR #23（executor observation 的 baseline identity 必須等於 checkpoint `target_identity`），再跑 `nexus-certify issue-check --issue 21` 才關單。依據：唯一 open Issue（bug，BYPASS_PATH_EXISTS / MEDIUM）；PR #23 為 clean、CI 3.11–3.13 全綠，今日更新。

## 進行中
- #21 / PR #23 修正已提交（Refs #21，無 closing keyword）；PR 稱稽核 reproducer 已轉為 fail closed，待人工審查與合併授權。

## 待確認（沒有 Issue 號）
- G4 durable resume（PR #14）與後續 executor / benchmark 修正（#18、#19、#20）已在 main；#21 收尾後 G4 是否還有剩餘工作、或何時開下一個 gate，目前沒有對應 Issue。
- fam4 audit、rev6、rev8 與 rev10 review findings 已在 draft 修正；rev10 新增 5 項信任 Git 回歸測試，在原 draft 全數失敗、修正後通過；完整 103 項測試與 G3 驗證通過。尚未 commit；是否建立 Issue 追蹤尚未決定。
- trusted Git structural fix 已在此 worktree 完成，uncommitted；紅燈回歸涵蓋 metadata symlink/hardlink、execution-capable config、Python launcher 與執行後 inode 驗證。120 項測試、compileall、G3 驗證/report 比對及 diff check 通過；尚無對應 Issue。

## 卡住 / 注意
- 目前 gate 只到 G4：不得做 source mutation、任意 shell、live provider orchestration、Dev MCP/DevSpace 依賴、verification/completion authority 或 release。
- `OUTCOME_UNKNOWN` 永遠不是 retry 許可；resume 的 in-flight step 必須先以同一 durable identity reconcile。
- #21 來源為 Nexus-new 的跨 repo invariant 稽核（finding `WB-7`），Issue 本身註明「不是實作授權」；合併需另行授權。
