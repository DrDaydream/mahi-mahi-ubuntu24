# 上游协议行为与实验口径

基线固定为 `PasinduTennage/mahi-mahi-consensus` 的 `consensus-rework` 提交
`7c928c06bd436c96b94065119e0e8c2d4ac1bcb6`。本版所有模式恢复这份上游的正常协议行为，
保留现有 A3/A5 敌手模块和必要的接入、分叉存储、观测代码。它不是逐字节未修改的上游二进制。

## 源码范围

- 恢复 `synchronizer.rs::sync_strategy` 的原判断 `if self.enable { return; }`。
  上游这个开关的含义与名称相反：默认 `enable_synchronizer=true` 时跳过这条主动请求缺失父块的策略。
  正常区块流、转发和处理网络请求的代码继续存在。本版不保留分叉中反转该判断的修复。
- 提案、投票、leader 判定、提交和交易时间戳端点使用原上游算法。A0 的敌手包装直接传递消息，
  不产生调度任务、不改变提案、不产生额外分叉。native crash A1 也运行 A0。
- A3/A5 的 `adversary.rs` 与 `db58361` 相同，包括固定身份、定向编排、网络调度、队列和字节上限。
  所有模式恢复上游补块行为，可能改变 A3/A5 的实际性能；“保持不变”指敌手策略及参数不变。
- 保留分叉 WAL/索引支持、fork output 指标和启动时的真实零值 shared 计数器。
  这些是敌手/观测接入；A0/A1 不创建额外分叉。签名验证的变更仅用于测试开关。

## 工具包

在干净的本仓库提交中执行 `python3 experiments/prepare_mahi_upstream.py --root /home/ubuntu/experiments`，
生成独立 `figure-tools-mahi-upstream` 和 `source-lock.json`。生成器可重复执行，不覆盖不同版本的工具包。
源码、构建和结果都锁定到该提交。使用新输出目录 `paper-results-v3.5-60s-mahi-upstream`；
旧实验保持原样，不会被当作新版实验完成结果。

| 外层模式 | native adversary | faults | 统计 |
| --- | --- | --- | --- |
| A0 | A0，空敌手集合 | 0 | 上游终端累计 |
| A1 | A0，空敌手集合 | floor((n-1)/3)，尾部进程不启动 | 上游终端累计 |
| A3 | 原 A3 | 0 | 原窗口差分 |
| A5 | 原 A5 | 0 | 原窗口差分 |

所有 Mahi-Mahi 模式：额外 warmup=0，measurement duration=60 秒；保留就绪检查及抓取开销。
A3/A5 窗口仍为每个节点初始化后 20–50 秒，seed=7，其余参数与上一版相同。
10/20/35 节点的 A1 分别不启动 3/6/11 个进程，完整委员会仍为 10/20/35。
每个启动节点的负载仍为请求总 rate / 完整委员会大小，故障节点负载不重新分摊。
原有 lifecycle 的创建、安装、逐组运行、finally destroy 和中断清理保留。

## E2E 与上游终端 summary

E2E 从交易生成器写入交易前 8 字节的毫秒时间戳，到 shared transaction 的 commit callback 取时。
包含打时间戳之后的内部输入排队、提案等待、传播和共识；不含打时间戳之前的调度滞后，
也不含给外部客户端返回响应的时间。这两个端点未修改。

A0/A1 的 `measurement_basis=upstream-terminal-cumulative-v1`：

1. 取每个实际启动节点最后一次 shared `latency_s_sum / latency_s_count`，包含启动阶段提交。
2. 对各节点的均值等权平均，按上游 `Duration` 纳秒运算截断；不是按全局交易数加权，也不把重复副本相加。
3. 上游 `count=0` 的均值算作 0 并参与算术平均。报告另外显示无观测节点和正观测节点数。
   全部零提交时允许实验 complete，显示的 0 不代表实测 E2E 为零。
4. 上游 TPS = `max(最后累计 count) // max(最后 benchmark_duration 整数秒)`。
   分子、分母可以来自不同节点；不是 `max(count/duration)`。`replica_tps_mean` 另作诊断。

60 秒是工具包完成就绪检查后的测量时长；累计样本覆盖自节点启动至最后抓取，
因此其累计 uptime 可以大于 60 秒，分母使用节点真实的 `benchmark_duration`。
这是保留原运行框架并采用上游终端统计公式，不宣称运行调度器也与上游完全相同。

A3/A5 使用 `window-delta-v1`：继续对 before/after 求差，TPS 为副本均值，E2E 只平均有新增提交的副本。
旧 `run.json` 没有 measurement_basis 的离线报告仍用窗口差分，不重新解释历史数据。
缺失/非法计数器、计数器回退、进程退出、日志缺失或清理失败仍是错误，不能伪装成零输出。

普通 `fab remote --adversary A0`（包括 faults>0）同样采用累计口径；原生 adversary A1 仍是旧静默敌手。
矩阵里的外层 A1 明确映射为 native A0 + faults，请勿将两者混淆。
