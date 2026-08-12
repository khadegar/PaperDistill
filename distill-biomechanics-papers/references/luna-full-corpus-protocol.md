# Luna Max 全文语义卡批次协议

本文件是 `distill-biomechanics-papers` 的规模化语义阅读操作协议。它约束真实全文阅读、overlay/card 写入、断点恢复、抽查和能力晋升；不产生任何论文卡，也不把脚本输出当作语义判断。

## 0. 不可放宽的规则

1. **语义内容只能由主读者在阅读全文后撰写。** `prepare_semantic_distillation.py` 的 article-kind/design 标签、标题、摘要、heading 和 regex 只能作为筛选提示；它们不是研究设计、结果、机制或质量结论。
2. **主读者必须亲自读完所选 packet 的每个非 reference chunk。** packet、索引、FTS 命中、摘要或正则命中均不等于已读。不得仅凭摘要、metadata 或 regex 完成 card。
3. **脚本仅做物料准备、hash/locator 枚举、机械 deep-merge、原子写入和结构校验。** 脚本不得自动生成 `study`、`argument_map`、`evidence_boundary`、`limitations`、`section_moves`、`writing_capability_candidates`、`quality` 或摘要等语义字段。
4. **parsed、selected、fully read、completed、validated、promoted 是六个不同计数。** 任何报告必须分开写；不能用“10k parsed”暗示“10k semantically read”。
5. **不改变证据状态。** `measured / predicted / inferred / recommended`、验证角色、端点层级、材料/几何状态、比较条件和不确定性在压缩、改写和晋升中都必须保留。

## 1. 术语和完成定义

| 状态 | 定义 | 可以做什么 |
|---|---|---|
| `parsed` | JATS/record 已获取并解析 | 统计和候选筛选；不能宣称读懂论文 |
| `selected` | 出现在 `semantic-distillation/selection.jsonl` | 排入阅读队列；尚未形成语义判断 |
| `fully_read` | 主读者按顺序读完 packet 的所有非 reference `Sddd:Cdd` | 可以开始写 overlay |
| `completed` | overlay 完成，assembler 写入 `reading.status=completed`，hash/locator/身份由 assembler 固定 | 可进入结构校验和语义抽查 |
| `batch_validated` | 本批目标卡的结构/locator 校验无 ERROR，且独立语义抽查通过；另附全局非 strict 进度报告 | 可计入本批次合格覆盖 |
| `global_strict_validated` | 整个当前 `selection.jsonl`（例如扩容后的 10k）均无 pending/invalid/orphan，`--strict` 无 ERROR/WARNING | 只能在全局阅读完成后使用；不是单批状态 |
| `promoted` | 候选能力通过 recurrence、journal diversity、credibility、locator、counterexample/boundary、scope、independence、utility gates | 可写入 `synthesis/promoted-rules.jsonl`，再更新最小相关 reference |

卡片的 machine-readable 位置固定为：

```text
<CORPUS_ROOT>/semantic-distillation/
  screening.jsonl
  selection.jsonl
  packets/<PMCID>.md
  cards/<PMCID>.json
  overlays/<PMCID>.json       # 主读者语义 overlay；只含可编辑语义字段
  synthesis/promoted-rules.jsonl
  reports/selection.json
  reports/validation.json
```

`selection.jsonl` 的 `reading_order`、`body_word_count`、`chunk_count` 是批次规划输入；packet 的 SHA-256 和原始 record SHA-256 是阅读版本的不可变锚点。

## 2. 批次大小和混合策略

批次是一个可恢复的工作包，不是把多篇全文一次性塞进同一上下文。批次内仍按**一篇论文一个连续阅读单元**执行；完成一篇并写入 checkpoint 后才进入下一篇。

### 2.1 以 packet 规模决定篇数

下面的阈值同时看 `body_word_count` 和 `chunk_count`，取较大风险等级。它们是保守的质量护栏，不是模型上下文的宣称容量。

| 论文等级 | 触发条件（任一） | 每批篇数 | 阅读方式 |
|---|---:|---:|---|
| 短 | ≤6,000 words 且 ≤35 chunks | 3（最多 4） | 每篇完整顺序阅读；批次总量不超过 24,000 words/120 chunks |
| 中 | 6,001–12,000 words 或 36–60 chunks | 2 | 每篇完整顺序阅读；同批至少跨两个 journal 或 article kind |
| 长 | >12,000 words 或 >60 chunks | 1 | 单篇批次；可分多个 session，但只生成一张 card |
| 超长/综述 | >18,000 words、>90 chunks，或 review/systematic review/scoping review | 1 | 先做章节地图，再按 chunk 顺序细读；每个 session 结束写 checkpoint |

若短篇混合后超过 24,000 words 或 120 chunks，立即拆批。一个含长综述的批次不再附加其他论文。当前语义选择中最长约 21,305 words/124 chunks，按“超长/综述”处理，而不是与短篇并读。

### 2.2 批次的覆盖约束

- 由 `selection.jsonl` 预先固定 PMCID 顺序和 batch ID，例如 `B001 = reading_order 001–003`；不得临时按标题相似度重排。
- 连续 3–5 个批次应覆盖配置的 discovery strata、至少两个 journal、original/review/weak-or-null/contradictory 执行和多个 study design；单批不必强行均衡，但不能只读“成功”论文。
- 把 lexical cue 与真实 article kind 分开：候选可标为 `review`，全文读后可以改为 original research、case series、protocol 等；反之亦然。
- weak、null、contradictory 或低可信度论文可作为失败模式/反例输入，但不得在未满足晋升 gates 前支持正向跨论文规则。

## 3. 每篇论文的全文阅读算法

### 3.1 开始前冻结版本

1. 从 `selection.jsonl` 取得 `PMCID`、`paper_id`、`record_path`、`source_record_sha256`、`packet_path`、`packet_sha256`、`section_count`、`chunk_count`。
2. 打开 packet 的 metadata 只作导航；把 `Article kind cue`、`Design feature cues` 标记为 **cue-only**。
3. 用 packet heading 形成预期序列：`### S001:C01` … 最后一个 chunk。不要手工删掉 Abstract、Background、Methods、Results、Discussion、Conclusion 或非 reference 的 supplementary/untitled chunk。
4. 若 packet 缺失、无法解码、hash 改变、source record hash 改变或 heading 序列异常，停在 `pending`，记录阻塞原因并修复/替换物料；不要用摘要补卡。

### 3.2 顺序读完所有 chunk

对每个 heading 按文件顺序执行以下循环，直到末尾：

```text
read Sddd:Cdd
→ 用前一 chunk 的末句恢复上下文（chunk 可能截断句子）
→ 私有阅读账本记录：section function、关键数据/条件、证据状态、疑问
→ 标记该 locator=read
→ 进入下一个 locator
```

阅读账本不是语义卡，也不能由脚本填充；它只是断点和防漏读工具。每篇至少形成以下五个相互链接的账本后再写 overlay：

1. **claim ledger**：主张、`measured/predicted/inferred/recommended`、允许动词、locator；
2. **validation ledger**：verification、calibration、validation、sensitivity、application prediction，注明独立性和覆盖端点；
3. **state ledger**：CAD/reconstructed/as-built/processed/aged/healing/explanted 等材料或几何状态；
4. **endpoint vector**：surface/contact、bone/tissue、mechanics、fixation、function、revision、durability，以及明确缺失端点；
5. **boundary ledger**：比较组差异、混杂方向、proxy-to-outcome bridge 和下一项区分性实验。

完成末尾 chunk 后，逐项核对：

- 预期 locator 集合与实际阅读集合一一相同、无重复、无遗漏；
- 每个核心判断至少有一个 chunk locator；每个数字保留单位、分母/样本量、比较组、时间和不确定性（若原文报告）；
- null、adverse、failed、contradictory、not assessed、not reported 不被正向摘要吞掉；
- 读者识别的限制与作者报告的限制分开，并给出可能偏倚方向；
- 不复制原句，只写与来源独立的能力候选。

### 3.3 综述和系统综述

先分类文章本身，再提取其内容：narrative/systematic/scoping review、meta-analysis、bibliometric study、case series 等不能混用。综述中：

- 把检索式、时间窗、纳排标准、风险偏倚、异质性和综合方法当作该综述的方法证据；
- 把纳入研究的结果保留为“review reports/synthesizes”，不能改写成综述作者亲自测得的结果；
- 先建立按 study type、species/population、anatomy、intervention/dose、comparator、time、endpoint、bias、certainty 的矩阵，再写跨研究能力；
- 长综述采用“章节地图 session → 逐 chunk 深读 session → 自审 session”，但最终必须覆盖所有 packet locator，不能只读结论和表格。

### 3.4 错误 article cue、修正和非实质记录

- 标题/metadata 出现 `review`、`clinical`、`case`、`finite element` 等词只触发复核；以全文实际设计重分类。
- `correction/corrigendum/erratum/retraction/expression of concern/editorial/news` 或仅有 abstract 的记录应在 screening 层排除或替换。若因筛选错误进入批次，保留审计记录，卡片留 `pending`，不要伪造完整语义卡。
- 若研究本身是故意纳入的弱执行/反例，完成卡时把 `quality`、`article_kind` 和边界如实写低/不确定；它可以支持 counterexample，不能单独支持 promoted rule。
- 非 reference 的 `Untitled section`、补充材料链接或短 chunk 仍是 packet 的 declared chunk：读完并计入 locator；内容无实质时在 reader notes 说明“读到但未提供可分析正文”，而不是把它放入 omissions。

## 4. Overlay 和 card 写入契约

### 4.1 主读者可写的内容

以 `assets/semantic-card-template.json` 为字段契约，至少完成：

- `study`：article kind、设计特征、problem、exact gap、objective/hypothesis、study design、sample/model、comparators、method spine、五类 validation roles、primary outcomes、highest evidence level；
- `argument_map`：central claim、data、warrant、backing、qualifier、rebuttals/boundaries、coherence；
- `evidence_boundary`：四类 evidence-state 列表、proxy-to-outcome bridges、overclaim risks；四类列表必须存在，至少一类非空；
- `section_moves`：每个 move 都有 section、transferable principle、failure mode 和真实 packet locator；
- `limitations`：author reported、reader identified、likely bias direction、非空 `scope_boundary`；
- `writing_capability_candidates`：能力而不是句子模板；每个候选有用途、适用 domain/design、supporting locators、counterexample/boundary、`confidence`；
- `quality`：六项维度、critical weaknesses、overall credibility；
- 非空 `summary_zh` 与必要的 `reader_notes`。

### 4.2 Overlay 的受保护字段

overlay 文件只放语义字段；不要放或修改以下根/键：

```text
card_id, paper_id, pmcid, source_record_sha256/source_hash,
packet_sha256, packet_path, record_path/source_record,
selection/identity/provenance/source/bibliography/reading/status
```

`assemble_semantic_cards.py` 会拒绝这些 protected paths，随后从 packet 重新计算 hash 和 locator，并原子写入 `reading.status=completed`、`access_level=full_text_read`、reader metadata、`omissions=[]`、`adjudication_status=self_reviewed`。因此：

1. 完整阅读和自审结束后才保存 `overlays/<PMCID>.json`；中断草稿用 `.draft`/批次 scratch 名称，不能让 assembler 误读。
2. 先 dry-run，再对**明确的 PMCID 列表**写入；日常批次不要用 `--all`，不要使用 `--overwrite-completed`。
3. assembler 的“assembled”只说明机械合并成功，不说明语义正确；仍需独立自审和抽查。

推荐的命令顺序（把占位符替换成当前批次值）：

```powershell
python scripts/assemble_semantic_cards.py `
  --root CORPUS_ROOT `
  --pmcid PMCID_1 --pmcid PMCID_2 `
  --read-at ISO8601 --reader-role luna_primary `
  --reader-model gpt-5.6-luna --reasoning-effort max

python scripts/assemble_semantic_cards.py `
  --root CORPUS_ROOT `
  --pmcid PMCID_1 --pmcid PMCID_2 `
  --read-at ISO8601 --reader-role luna_primary `
  --reader-model gpt-5.6-luna --reasoning-effort max --write
```

## 5. 自审、结构校验和语义校验

### 5.1 主读者自审（每张卡 100%）

在 dry-run 前逐项回答“是/否/不适用”，否项回到全文修订：

1. 我是否亲自读了 packet 每个 non-reference locator，并以 heading 集合核对？
2. `source_record_sha256`、`packet_sha256`、PMCID、paper ID 是否一致且未手工改写？
3. article kind 是否来自全文设计而非 cue？review 的方法和纳入研究结果是否分开？
4. problem → gap → objective → method → comparator → validation → outcomes → bounded implication 是否闭合？
5. verification、calibration、validation、sensitivity、application prediction 是否分开，且未把校准数据当独立验证？
6. measured/predicted/inferred/recommended 是否逐项标注；每个 inference 是否有替代解释和区分性试验？
7. architecture、cell、animal/tissue、interface mechanics、fixation、clinical function、durability 是否没有互相替代？
8. 比较组、状态、时间、样本量/分母、单位、不确定性、null/negative/contradictory 是否保留？
9. limitations 是否写出偏倚方向和 scope boundary，且没有把“未测量”写成“零事件”？
10. capability 是否是可迁移的推理能力，含 locator、边界和置信度，没有复制源句？

### 5.2 结构校验（100%）

标准 validator 只评估结构和 provenance，不评估语义正确性。对**本批目标卡**，结构/locator 校验至少要求：身份/hash 匹配，`reading.section_locators_read` 与 packet headings 完全相等，`omissions=[]`，所有核心对象和字段存在，section move/capability locator 可解析，promoted rule 的 recurrence/journal/locator/boundary/scope 可追溯。

当 `selection.jsonl` 扩展到 10k 时，validator 当前没有 `--pmcid`/scope 选项；它读取整个 selection。此时不能把全局 `--strict` 当作单批门：只要其余论文 pending，全局 strict 就会失败。批内应采用以下两层结果：

1. 对 batch PMCID 做目标卡结构/locator 校验（可在临时、只含该批 selection/cards/packets 的 fixture 上运行 strict，或调用 validator 的只读检查函数）；结果命名为 `batch_validated`，只覆盖目标卡。
2. 对真实全局 root 运行 `strict=False` 的进度检查，报告 selected/completed/pending/invalid/orphan 和 coverage；`PASS_WITH_WARNINGS`/pending 是进度状态，不能写成“全局 strict PASS”。
3. 只有当全局 selection 的所有选中论文都完成后，才运行全局 `--strict`，并将结果命名为 `global_strict_validated`。若 job manager/validator 没有 scope 支持，禁止误报全局 strict 为批次通过。

若必须保持严格只读，可在不写 `reports/validation.json` 的方式调用 `SemanticValidator(root, strict=True).report()`，或在临时 fixture 副本运行 CLI；CLI 本身会持久化 validation report。无论哪种方式，都要保留输出中的 `semantic_correctness_assessed=false` 事实，并在 10k 尚未全部完成时明确它是 batch fixture 结果，不是全局 strict 结果。

### 5.3 语义抽查（validator 之外）

- 每批 100% 做 locator 集合检查；每卡至少抽一个 Methods locator、一个 Results/Discussion locator 和一个 Conclusion/limitation locator 回到 packet 原文复核。
- 每批独立语义复读至少 1 张，或卡片数的 `ceil(20%)`，取较大者；优先长论文、综述、cue 被纠正者、低可信度/反例和跨层机制主张。
- 独立读者只能挑战主读者的分类、边界和证据状态，不能以摘要替代全文，也不能直接生成替代 card。主读者负责 adjudication，并把争议和决定写入 reader notes。
- 抽查发现 hash/locator/漏读/证据升级时，整张卡回到 pending/re-read；该批次不晋升，不能用脚本批量修补语义字段。

## 6. 语义保真的压缩规则

卡片需要短而可审计时，采用“去重复、不去证据”的压缩：

**优先保留**：central claim、exact gap、objective、design/comparator、带条件的 primary outcomes、验证角色、四类 evidence states、proxy bridge、overclaim risk、限制/偏倚/scope、capability locator/boundary。

**可以删除**：重复的背景形容词、同义句、未改变判断的修辞、重复 metadata、完整引文或原句。

**不得删除或合并**：数字/单位/分母/不确定性/时间、null 或失败结果、不同 comparator/arm、独立性、材料/几何状态、endpoint 层级、locator、modal verb、替代解释、未测量和未报告的区别。

压缩后逐句做“状态保持 diff”：

```text
原项 → 压缩项：evidence state 相同？endpoint 相同？条件/比较组相同？locator 保留？
```

任何 `predicted→measured`、`inferred→demonstrated`、proxy→clinical outcome、single component→synergy 的跃迁都视为失败，恢复被删信息或拆成多条。

## 7. 断点、恢复和版本漂移

1. 批次开始时记录 `batch_id`、selection 行号/reading_order、每篇 packet/source hash、预期 locator 列表和 session 序号。
2. 每读完一个 chunk，在 append-only scratch ledger 写入 `last_locator_read`、已发现疑问和下一个 locator。该账本不由脚本生成语义内容，也不替代 card。
3. 中断时保持 card `pending`，不要写 `reading.status=completed`。恢复时回读上一个已完成 chunk 的末段和下一个 chunk，再继续；不要从摘要重新开始。
4. overlay 先写临时 `.draft`，自审通过后原子改名为 `.json`；assembler 采用原子替换 card，避免半张卡可见。
5. packet/source hash 发生变化时，旧 overlay/card 不得沿用；将其标为 stale，重新读取受影响论文。已完成 card 默认冻结，重读必须显式批准并使用新的 `read_at`；常规批次禁止 `--overwrite-completed`。
6. 一张卡失败不阻塞同批其他**已完成且 hash 不变**的卡；失败卡保留 pending 和阻塞原因，下一批从同一 locator 恢复。批次报告同时列出 completed、pending、blocked 和原因。

## 8. 抽查、批次晋升和跨批规则晋升

### 8.1 批次晋升（card-level）

一批只有在以下条件全部满足时才计入 `validated`：

- 所有目标卡均有完整 overlay，assembler dry-run 和 `--write` 无错误；
- batch 目标卡的结构/locator 校验：`errors=0`、无目标卡 pending/invalid/orphan，packet locator 集合全覆盖；
- 全局 root 仅做非 strict 进度检查并记录 pending 数。selection 扩容到 10k 后，在其余论文未完成时不报告全局 `--strict` PASS；全局 strict 只留给最终全量完成门；
- 每卡自审通过；独立语义抽查达到第 5.3 节比例且无 critical finding；
- selection、completed、validated 计数可由 `selection.jsonl`、cards 和报告复算；
- 任何 cue 更正、异常 article、缺失 endpoint、冲突结果都有显式 reader note/quality boundary。

### 8.2 能力候选晋升（cross-paper）

候选先留在 card 的 `writing_capability_candidates`，不因单卡“写得好”直接晋升。只有同时满足以下 gates 才写 `synthesis/promoted-rules.jsonl`：

- 至少 3 张**完成并通过抽查**的 semantic cards；
- 至少 2 个 journal；且跨两个 study design，或明确属于窄 domain module；
- 每张支持卡和每个 supporting locator 可追溯；
- 至少 2 张支持卡的 credibility 为 `moderate` 或 `high`；
- 检查过一个 contrary/weak execution，并在 `counterexample_or_boundary` 说明停止条件；
- scope 明确写出适用章节/domain/design 和不适用范围；
- 用独立能力语言重写，不存 source sentence；
- 能改变 planning/drafting/revision/audit 决策（utility）。

晋升后再运行目标卡结构校验和 fresh-context forward test；forward test 只测规则是否保持证据边界，不把规则当文献证据。全局 `--strict` 仍只在全量 selection 完成时运行。失败、deferred、待补覆盖的候选保留在外部 synthesis/审计记录，不删除反例。

## 9. 可直接复制给 Luna reader 的任务模板

```text
角色：Luna Max primary semantic reader（reader_role=luna_primary；reader_model=gpt-5.6-luna；reasoning_effort=max）。
任务：完成批次 BATCH_ID 的真实全文语义阅读和 semantic-card overlay；只处理下列 PMCID：PMCID_LIST。

硬约束：
1) 逐篇处理，一篇完成并 checkpoint 后再进入下一篇；不要把多个 packet 同时当作一个摘要任务。
2) 必须亲自按顺序阅读 PACKET_PATH 的每一个非 reference `### Sddd:Cdd` chunk，直到最后一个；摘要、metadata、FTS、regex 和 article-kind/design cue 只能导航，不能替代全文。
3) 先建立 locator ledger；缺失、重复、hash 漂移、解码错误或非 reference packet 不完整时保持 pending，记录阻塞，不用摘要补卡。
4) 阅读过程中维护 claim/validation/state/endpoint/boundary 五个账本；保留 null、失败、冲突、不确定性、比较组、时间、分母和材料/几何状态。
5) 根据全文重分类 article kind 和 design；review 的检索/综合与纳入研究结果分开。把弱执行作为边界/反例，不当作正向规则证据。
6) 全文读完后才写 overlay。overlay 只写语义字段：study、argument_map、evidence_boundary、section_moves、limitations、writing_capability_candidates、quality、summary_zh、reader_notes。不要写 card_id/paper_id/pmcid/source hash/packet hash/reading/selection/provenance/source/bibliography/status 等受保护字段。
7) section_locators_read 的最终集合必须等于 packet headings；omissions 只有在尚未完成时使用，完成卡不得留下 omission。每个 section move/capability supporting locator 必须真实存在于 packet。
8) 写作能力要独立表述“如何推理/组织/审计”，不能保存源句或仿写句。每个候选有用途、scope、counterexample/boundary、confidence 和 locator。
9) 自审完成后先执行 assembler dry-run，再写入；命令必须使用 `--reader-role luna_primary --reader-model gpt-5.6-luna --reasoning-effort max`，不要使用 `--all` 或 `--overwrite-completed`。脚本只做 hash/locator/merge/validation，绝不自动填充语义字段。

当前纸面信息（由调度器填充，不可当作事实替代全文）：
- CORPUS_ROOT: ...
- BATCH_ID / reading_order: ...
- PMCID / paper_id: ...
- selection row: ...
- packet path/hash: ...
- source record path/hash: ...
- expected body_word_count / chunk_count: ...

每张卡的交付：
- overlay 文件：`semantic-distillation/overlays/<PMCID>.json`（仅语义字段）
- 私有 checkpoint：`BATCH_ID/<PMCID>.progress`（至少记录 last_locator_read、next_locator、hash、阻塞/疑问）
- 读者报告：实际读完 locator 数、article-kind 更正、关键证据边界、未测 endpoint、限制/偏倚、是否通过自审。

完成定义：所有非 reference chunk 已读；overlay 自审通过；assembler dry-run 无错误；随后由调度器写卡并做**本批目标卡结构/locator校验**，同时做全局非 strict 进度检查。全局 selection（例如 10k）全部完成后才运行全局 strict。只报告 `selected → fully_read → completed → batch_validated → global_strict_validated`（后者未到全量门时写 `not_yet`），不要把 parsed corpus count 写成 semantic-read count。
```

## 10. 批次验收清单（调度器）

### 物料和范围

- [ ] 批次 ID、PMCID 列表和 reading_order 已冻结；总量符合篇数/word/chunk cap。
- [ ] 每篇 packet/source hash 与 selection 行一致；没有 correction/retraction/editorial/仅摘要记录。
- [ ] 每篇预期 locator 集合已枚举；batch scratch ledger 可恢复。

### 真实全文阅读

- [ ] 主读者逐个读完全部非 reference chunk，包含短/untitled/supplementary chunk。
- [ ] cue 只作提示；article kind、design、validation role、quality 来自全文判断。
- [ ] 五个账本完成；null/负结果、比较条件、状态和 endpoint 边界未丢失。

### Card/overlay

- [ ] overlay 没有 protected root/key；无脚本生成的语义段落。
- [ ] study、argument、evidence boundary、section moves、limitations、capability、quality、summary 均非空且带 locator/边界。
- [ ] 每个 capability 是独立能力，不是源句；confidence/scope/counterexample 已写。

### 自审和验证

- [ ] locator 集合与 packet 完全相等、无重复/extra/missing；omissions=[]。
- [ ] assembler 先 dry-run，再显式 `--pmcid` 原子写入；未覆盖已完成卡。
- [ ] 本批目标卡结构/locator 校验 `errors=0`，目标卡无 pending/invalid/orphan；全局非 strict 进度中的 pending 数已记录。
- [ ] 若 selection 已扩展到 10k，未完成全量前不宣称 `global_strict_validated`；没有 scope 选项时不把全局 strict 误报为批次通过。
- [ ] 每批独立语义抽查至少 1 张或 20%（向上取整），重点复核长文、综述、cue 更正、低可信度和机制主张。

### 报告和晋升

- [ ] 报告分开列 `parsed/selected/fully_read/completed/validated/promoted`。
- [ ] 未满足三卡/两 journal/两张 moderate-high/counterexample/scope/locator gates 的候选保持 candidate/deferred。
- [ ] 批次失败卡和原因可在下一批恢复；未用脚本补写语义，未删除反例。

## 11. 覆盖报告固定句式

```text
本批使用 N 个 parsed full texts 作确定性物料/统计输入；其中选择 S 篇，主读者完整阅读 F 篇，完成 C 张 semantic cards，本批目标卡结构/locator 校验通过 V 张（`batch_validated`），晋升 P 条跨论文能力；全局 strict 状态为 `global_strict_validated=not_yet` 或已完成。这里不宣称对 N 篇都完成语义阅读。
```

当前仓库的只读结构审查结果可作为 45 篇已完成 profile 的基线：45 selected、45 completed cards、0 pending/invalid/orphan、strict PASS；该结果仍然只证明结构/provenance，不能替代本协议要求的每篇全文语义自审和抽查。若 selection 后续扩展到 10k，这个历史基线不等于新的全局 strict PASS；在全量完成前只能报告 batch_validated 与全局非 strict 进度。
