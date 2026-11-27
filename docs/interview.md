# 面试材料

## 5 分钟讲解稿

### 1. 问题（30 秒）

> 我想验证一个 agent 能不能自主修仓库里的 bug。不是调 LangChain 串流程，而是从零实现 loop、工具、沙箱和评测，并且用自建 benchmark 量化。

### 2. 架构（1 分钟）

> 分四层：agent loop 是消息状态机；工具层有 bash 和结构化工具两套 ACI；沙箱层是 Local / Docker 两个实现；评测层负责 base/fix commit、隐藏测试和 resolved 判定。loop 只依赖 Sandbox 和 Tool 两个 Protocol，换实现不改主体。

### 3. 关键取舍（1.5 分钟）

> - 动作协议：bash-only 更省 token，但要维护文本 parser；structured 协议稳定，但每轮多 1.3k schema。消融结果准确率无检测差异，bash 省 10-15%。
> - 评测：agent 只看 base 和 issue；评测时 fresh workspace、重置测试、注入 hidden test；sanitized git + 断网防作弊。
> - 沙箱：非 root、只读 rootfs、资源限制、断网；明确它不防内核逃逸。
> - 上下文：压缩实验是负结果——省 25-30% token，但稳定丢掉一个任务。

### 4. 结果（1 分钟）

> 10 个 eval 任务，最终配置两次 9/10、10/10；3 个 holdout 两轮 3/3。成本每次 5-6 元（高峰上界）。

### 5. 失败与边界（1 分钟）

> 唯一反复失败的是 requests-001：探索路径不稳定，30 步零编辑。还发现过两个 harness bug：DSML 协议变体导致 10% 动作丢失；截断响应被误判成重复动作。修复后空动作率降到 0.9%。
>
> 样本只有 10+3 个，跨 run 会翻转 1-2 个任务；结论是“可检测差异”而不是“绝对性能”。

## 高频问题与答法

### Q1：为什么不用 LangChain / LangGraph？

> 想理解 agent 的本质：消息状态、工具调用、观察回填、终止条件、上下文预算。框架会隐藏这些；我的 loop 核心约 200 行，所有行为都能在 trajectory 里看到。

### Q2：bash-only 和 structured 哪个好？

> 这批任务上准确率没有可检测差异（bash 9/10、10/10；structured 9,9,9,10）。bash 省 10-15% token，但空动作率 0.9% vs 0%；structured 的 `str_replace` 唯一匹配和路径守卫降低误操作风险。三分法：准确率无差异、bash 更省、structured 更稳。

### Q3：为什么不继续调 prompt 让模型多用 view？

> 试过在 prompt 里写 prefer view，模型仍有 82% 的动作是 bash。工具可用不等于工具会被选；继续调就是在 10 个 eval 任务上调参，会过拟合。

### Q4：怎么防止 agent 改测试通过？

> hidden test 不在 workspace；评测时从 base 重新生成 workspace，先应用 model patch，再把测试文件重置到 baseline，最后注入 hidden test。改测试不影响最终判定。

### Q5：怎么防止 agent 抄答案？

> workspace 用 `git archive` + 单 commit，没有 fix 对象；`git fsck` 找不到未来提交；`--network none` 挡住远程仓库。轨迹里确实出现过 `git fsck` 和 `git ls-remote`，都被机制挡住。

### Q6：沙箱安全吗？

> 防误操作、资源耗尽、外联和提权；不防内核漏洞，也不是多租户。非 root、只读 rootfs、1g/1cpu/128pids、cap-drop ALL、no-new-privileges、network none。LocalSandbox 只是开发便利，不是安全边界。

### Q7：10 个任务能说明什么？

> 不能说明泛化。n=10 时 1 个任务 = 10 个百分点；同一配置多次跑会翻转 1-2 个任务。所以报逐题矩阵和范围，不报单一成功率；holdout 3 个只作 sanity check。

### Q8：为什么成本是上界？

> 没统计缓存命中，全部按未命中价算。DeepSeek 缓存命中价 0.02 元/M，实际更低。成本还分高峰/平峰，同一配置高峰约翻倍。

### Q9：上下文压缩为什么失败？

> 12k 阈值 + 240 字符摘要触发反复压缩，早期 view/search 找到的关键信息被反复丢弃，agent 只能重新探索。省了 25-30% token，但 packaging-001 从 3/3 变成 0/2。默认关闭。

### Q10：self-repair 为什么没用？

> 17 次提交 0 次拒绝。模型提交前已经自己跑测试；可见测试也不覆盖新行为（hidden test 才覆盖）。它是对“改坏已有行为”的保险，不是提升通过率的杠杆，对“不提交”的失败完全无效。

### Q11：为什么 requests-001 总是失败？

> 它是探索路径问题：30 步都在读 urllib3 内部、git 历史、测试文件，没有进入编辑。成功样本里 step 15 就完成 str_replace。不是不会修，是不稳定。

### Q12：怎么保证评测可复现？

> base workspace 用 `git archive`；评测容器全新；patch 用 `newline=""` 保原始字节；同一配置多次跑并报翻转；配置全部进 metadata。

### Q13：为什么不用 SWE-bench？

> 重型仓库镜像和依赖会吃掉时间/磁盘；先用 4 个小仓库自建 10+3 个任务，验证方法。SWE-bench Lite 是下一步的外部 sanity check，不是主 benchmark。

### Q14：如果模型输出了不存在的工具怎么办？

> 参数校验和 registry 会返回 `ToolResult(ok=False)`，把错误作为 observation 喂回；loop 继续，不会崩。未知工具、缺参数、类型错误都有测试。

### Q15：怎么控制成本？

> 30 步预算、observation 截断、重复动作止损、单任务 token 统计；10 个任务一组约 3-6 元。上限由 max_steps 和 max_tokens 控制。

### Q16：生产环境敢用吗？

> 不敢直接用。benchmark 必须全自动，否则不可比；生产要加三层：危险动作审批（删除/push/部署）、成本硬上限、可回滚的 patch 流程。人的位置从“每步批准”移到“设计约束 + 预算 + 事后审计”。

### Q17：为什么用 DeepSeek？

> OpenAI 兼容、成本低、function calling 可用。代价是文本协议会混入 DSML 格式；我们为此写了解析兜底。换模型需要重测 parser 和工具采用率。

### Q18：你最大的收获是什么？

> 评测比功能难。功能一周能写完，但“数字是否可信”要处理行尾、隐藏测试、sanitized git、方差、协议失败率、截断误杀。一个 harness bug 就能把模型失败伪装成能力问题。

## 必记数字

```text
eval:      9/10、10/10（最终两次）
holdout:   3/3、3/3
tokens:    eval 2.19M - 2.55M
steps:     中位 23.5
空动作率:  0.9%（修复前 ~10%）
成本:      eval ¥5-6/次（高峰上界），平峰 ~¥3
任务集:    10 eval + 3 holdout + 1 smoke
单测:      64 passed
```

## 三个可讲的故事

1. **DSML 协议 bug**：以为 bash 比 structured 差，实际是 10% 动作没被解析；修复后 8/10 → 10/10。
2. **截断误杀**：`finish_reason=length` + 空内容被当成重复动作，触发 looping；修复后 holdout 恢复 3/3。
3. **压缩负结果**：省 25-30% token，但稳定丢掉 packaging-001；默认关闭，并留下“结构化进度摘要”的下一步。

## 不要说的话

- “bash 一定比 structured 好”——准确率无可检测差异；
- “Docker 很安全”——要讲威胁模型和边界；
- “成功率 90%”——要讲样本量、翻转和 holdout 只有 3 个；
- “能直接上生产”——它不是生产级系统。
