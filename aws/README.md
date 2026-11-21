# Mahi-Mahi A0–A5：Fabric AWS 实验

此适配器部署包含攻击实现的 `DrDaydream/mahi-mahi-ubuntu24` 源码。策略及其适用范围见 [docs/adversary.md](../docs/adversary.md)。已在临时 Ubuntu 24.04 环境执行 Rust 测试及编译检查，并完成离线 Python 测试；通过项与基线已有失败详见 [验证记录](../docs/adversary-validation.md)。没有运行 AWS 实验。以下安装与运行命令供之后在 Ubuntu 24.04 amd64 控制机上执行。

## 控制机准备

控制机和每个工作节点都必须能通过 HTTPS Git 读取 `https://github.com/DrDaydream/mahi-mahi-ubuntu24.git`。私有仓库需要预先配置具备读取权限的 Git 凭据；原来的“节点只读取公开上游”条件已不适用。不要将 token 写入仓库 URL、settings、脚本或结果文件。适配器不会自动复制 Git 凭据到工作节点。

```bash
mkdir -p /home/ubuntu/experiments
cd /home/ubuntu/experiments
git clone --branch main https://github.com/DrDaydream/mahi-mahi-ubuntu24.git
cd mahi-mahi-ubuntu24/aws
python3 -m pip install --user --break-system-packages -r requirements.txt
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
cp -n settings.example.json settings.json
chmod 600 /home/ubuntu/.ssh/orca-amazon.pem
fab check
fab info
```

默认使用 AWS 凭据链、五个区域、10 个验证节点、`m5.8xlarge` 和 200 GiB gp3。按自己的控制机环境配置 `key`、`instances` 与 `ssh_cidrs`。settings 和结果不提交到 Git。若没有这套专属标签的实例，`fab create --nodes 2` 会在每个区域新建两台计费实例。

## 固定源码与安装

`repo.url` 指向上述用户仓库，`repo.ref` 默认 `HEAD`。每次安装或运行都从执行适配器的控制机 checkout 解析完整 40 位提交，要求 checkout 干净且 origin 与 `repo.url` 一致。也可以指定控制机已有的分支、标签或完整提交；该提交必须含有 `adversary.rs`，且能够从远端仓库取得。旧 `repo.commit` 设置已移除，迁移时改用 `repo.ref`。

源码改变后先提交并使该提交可从远端获取，再明确安装：

```bash
git status --short
git rev-parse HEAD
fab install
```

只有 `fab install` 安装依赖并编译。控制机和工作节点使用同一解析后提交、Rust `1.78.0`、`--locked --release` 与 `-C target-cpu=native`。`remote.source_dir` 是独立构建 checkout，默认 `/home/ubuntu/experiments/mahi-mahi-source`；不要设置成控制机的适配器 checkout。已有不同 origin 或未保存文件时会拒绝覆盖；原来的上游源码目录请保留，另选新的构建路径。

编译后运行 genesis 命令检查二进制是否导出完整 adversary schema，再写入 `.mahi-build.json`：仓库、完整提交、工具链、编译参数、二进制 SHA-256 与 `adversary_schema=1`。`results/installation.json` 汇总各机器记录。缺失 schema 的旧 manifest、错误提交或校验和均被拒绝。

`fab remote` 在本地解析源码身份，在各节点执行纯 Python manifest/校验和校验。该命令不编译、不安装依赖、不更新工作节点 Git。切换提交后必须先重新安装，不能用原上游二进制运行攻击。

## 选择 A0–A5

```bash
fab remote --adversary A0
fab remote --adversary A1 --byzantine 3
fab remote --adversary A2 --byzantine 3
fab remote --adversary A3 --byzantine 3
fab remote --adversary A4 --byzantine-ids 0,1,2
fab remote --adversary A5 --byzantine-ids 0,1,2
```

`--byzantine K` 在启动前固定选择 `[0, ..., K-1]`；`--byzantine-ids` 明确指定身份，二者不能同时给出。A1–A5 未指定身份或配置为空列表时，默认选择前 `f=floor((n-1)/3)` 个身份。A0 没有攻击身份；显式 `--adversary A0` 会清空 settings 中的身份及目标。

`faults` 仍表示不启动委员会末尾的 N 个身份，和 Byzantine 数量独立。两组必须互不相交且总数不超过 `f`。例如 10 个节点中身份 0、1 为 Byzantine，9 崩溃：

```bash
fab remote --nodes 10 --faults 1 --adversary A3 --byzantine-ids 0,1
```

A1 有限时间扣留 Byzantine 提议；A2 与 DAG 无关地分发两个版本；A3 根据本地 DAG 支持构造分裂；A4 跟踪未决目标并延迟相关证据；A5 再调度诚实节点之间的区块路径。最多两个版本，没有 `fork_count` 设置。实际策略及回退条件以策略说明和节点事件为准。

## 参数、时间窗口与复现

`settings.example.json` 列出全部参数。有效 `benchmark.adversary` 写入 `public-config.yaml` 的 `parameters.adversary`，并在所有运行副本上使用相同公共配置，包含诚实节点。默认对象为：

```json
{
  "mode": "A0",
  "byzantine_authorities": [],
  "seed": 1,
  "start_after_ms": null,
  "duration_ms": 60000,
  "delay_ms": 200,
  "late_delay_ms": 1000,
  "max_pending_messages": 4096,
  "max_pending_bytes": 67108864,
  "max_extra_bytes_per_round": 8388608,
  "target_authority": null,
  "target_round": null
}
```

省略 `start_after_ms` 或设置 null，表示 `warmup * 1000`，会考虑 CLI 的 `--warmup`；最终 YAML 使用整数。`duration_ms` 是攻击时间，默认 60 秒。`benchmark.duration` 是 warmup 后的测量时长，默认 120 秒。要求 `start_after_ms + duration_ms + late_delay_ms <= (warmup + duration) * 1000`，并且 `0 < delay_ms <= late_delay_ms`，留出恢复时间。短 A0 基线不要求容纳活动攻击窗口。

`target_authority` 必须在固定 Byzantine 集合里。`target_round` 如提供必须为正，并确实选出该身份（或未指定身份时的某个固定 Byzantine 身份）。开启 pipeline 后第 r 轮的 leaders 为 `(r + offset) % n`，其中 `offset=0..leaders-1`。只给身份时由策略选择符合条件的轮次；两者均为空时使用策略找到的最早合适目标。

此实现使用已有 `round_robin` leader schedule，并未实现随机 common coin。A1–A5 只允许四轮或五轮 wave，默认五轮、四个 leaders。schedule 写入 run metadata，不增加 Rust 协议字段。适配器开启 pipeline，并记录设置。公共配置维持 `parameters.enable_synchronizer=true`；恢复上游后，这个值会跳过主动请求缺失父块的策略。

攻击时间相对各节点 Core 初始化的本地单调时钟。Fabric 并行启动存在偏差，`run.json` 记录控制机观察的 `launch_spread_seconds`。测量 warmup 在所有运行节点就绪后开始，因此与节点攻击时间轴存在启动和就绪偏差；分析时对齐各节点日志，不能把相同毫秒设置当作全局同时启动。

## 结果与离线检查

`results/run-*` 保留 `run.json`、`summary.json`、`summary.csv`、生成的配置、原始 Prometheus 快照及节点日志。run manifest 包含全部有效攻击参数、固定 Byzantine/崩溃身份、wave/leader 设置、同步器状态、源码来源及各二进制记录；summary 同时保存攻击参数。CSV 继续记录逐副本吞吐与延迟。攻击效果、额外版本、commit/skip/undecided 和恢复需要结合节点事件分析。

测量结束后立即打印详细 `SUMMARY`，包括实验参数、敌手参数、全部活动副本与诚实副本的统计，以及逐节点 TPS、E2E 和分叉区块输出比例；清理和日志下载结束后更新 `summary.txt` 的最终状态。native A0（包括 faults>0）采用上游终端累计统计，记录为零提交的副本在 E2E 算术平均中贡献 0，同时明确标记无观测。其余敌手模式的窗口差分仍把零提交延迟记为 `N/A`，只平均正提交副本。全部零提交也会生成结果，不能将运行完成等同于协议保持进展。

所有模式以节点进程存活且指标可采集作为就绪条件，允许有效零输出；进程退出、指标缺失等基础故障仍报错。二进制启动时发布真正的零值 shared 直方图，缺失数据不会被报告器伪装成零。

分叉比例采用“额外版本 / 全部非创世提交输出区块”：相同作者与轮次第一次输出一个摘要计普通输出，之后输出的其他摘要计额外分叉输出。分子、分母按同一测量窗口计算，区块须已进入最终提交 DAG；只生成或接收的分叉不计入。终端显示 `Committed blocks`、`Extra fork outputs`、`Fork output ratio`，JSON/CSV 保存 `committed_blocks`、`extra_fork_blocks`、`fork_output_ratio`（0～1）。零区块输出或旧二进制缺少计数器时为 `N/A`，详情见 [统计口径](../docs/adversary.md#fork-block-output-proportion)。TPS 仍是未按交易内容去重的提交条目速率。

重新打印最近一次结果（不运行节点）：

```bash
cd /home/ubuntu/experiments/mahi-mahi-ubuntu24/aws
python3 -m mahi_aws.report --latest
```

本版恢复上游补块开关的原始判断：`enable_synchronizer=true` 会跳过主动请求缺失父区块的策略。A3/A5 的敌手策略保留。native A0（包括 faults>0）采用上游终端累计 E2E/TPS 口径，其余敌手模式保留窗口差分；零输出允许完成，但缺失指标仍报错。需重新 `fab install` 编译二进制。详细边界、上游版本与新版工具包见 [上游协议说明](../docs/upstream-protocol.md)。

离线 Python 检查不访问 AWS，也不编译 Rust。构建失败回归使用退出失败的假编译命令：

```bash
python3 -m pip install --user --break-system-packages -r ../requirements-test.txt
python3 -m pytest tests -q
```

停止实验进程用 `fab kill`；停止专属 EC2 实例用 `fab stop`；恢复已有实例用 `fab start --max 2`。`fab destroy` 会终止这套专属标签的实例并移除相应安全组。
