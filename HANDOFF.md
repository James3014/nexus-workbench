# HANDOFF

更新：2026-10-11 · by Claude · HEAD `79bae6f`
<!-- 規則見 AGENTS.md「Handoff」。上限 150 行；過期內容移到 docs/archive/HANDOFF-YYYY-MM.md。 -->

## ▶ 下一步（只放一件，必須帶 #Issue）
#21 審查並落地 PR #23（executor observation 的 baseline identity 必須等於 checkpoint `target_identity`），再跑 `nexus-certify issue-check --issue 21` 才關單。依據：唯一 open Issue（bug，BYPASS_PATH_EXISTS / MEDIUM）；PR #23 為 clean、CI 3.11–3.13 全綠，今日更新。

## 進行中
- #21 / PR #23 修正已提交（Refs #21，無 closing keyword）；PR 稱稽核 reproducer 已轉為 fail closed，待人工審查與合併授權。

## 待確認（沒有 Issue 號）
- G4 durable resume（PR #14）與後續 executor / benchmark 修正（#18、#19、#20）已在 main；#21 收尾後 G4 是否還有剩餘工作、或何時開下一個 gate，目前沒有對應 Issue。

## 卡住 / 注意
- 目前 gate 只到 G4：不得做 source mutation、任意 shell、live provider orchestration、Dev MCP/DevSpace 依賴、verification/completion authority 或 release。
- `OUTCOME_UNKNOWN` 永遠不是 retry 許可；resume 的 in-flight step 必須先以同一 durable identity reconcile。
- #21 來源為 Nexus-new 的跨 repo invariant 稽核（finding `WB-7`），Issue 本身註明「不是實作授權」；合併需另行授權。
