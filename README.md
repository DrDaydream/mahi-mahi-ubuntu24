# Mahi-Mahi：Ubuntu 24.04 本地与 AWS 实验部署

本仓库保存 Mahi-Mahi 研究原型，以及 Ubuntu 24.04 本地运行工具和 AWS Fabric 部署代码。A0–A5 敌手实验的控制机命令和配置见 [AWS 敌手实验说明](aws/README.md)，策略与边界见 [adversary.md](docs/adversary.md)。

- 上游：[PasinduTennage/mahi-mahi-consensus](https://github.com/PasinduTennage/mahi-mahi-consensus)。
- 上游分支：`consensus-rework`。
- 原始上游基线：`7c928c06bd436c96b94065119e0e8c2d4ac1bcb6`；敌手实验构建本仓库的指定提交。
- Rust：`1.78.0`，保留 `Cargo.lock`，编译使用 `--locked`。
- 节点程序仍叫 `mysticeti`，这里运行的是 Mahi-Mahi 原型。
- AWS 默认：五区域、10 个验证节点、Ubuntu 24.04 amd64、`m5.8xlarge`、200 GiB gp3。

协议源码在 `crates/`，原说明在 [UPSTREAM_README.md](UPSTREAM_README.md)，来源记录在 [UPSTREAM.json](UPSTREAM.json)。本仓库不是生产 Sui 部署。

## 源码目录修复

部署工具和实际编译的源码目录分开：

```text
/home/ubuntu/experiments/mahi-mahi-ubuntu24/       本仓库
├── aws/                                       在这里执行 fab
│   ├── settings.example.json
│   ├── settings.json                           自己创建，不提交到 Git
│   └── results/                                结果和日志
├── local/                                     本地运行工具
└── crates/                                    协议与敌手实验源码

/home/ubuntu/experiments/mahi-mahi-source/        自动检出并编译的本仓库指定提交
/home/ubuntu/experiments/mahi-mahi-aws-runs/      各工作节点的逐次实验目录
```

`remote.source_dir` 默认是 `/home/ubuntu/experiments/mahi-mahi-source`，不要设成部署仓库或 `aws/` 目录。若该路径已有非 Git 文件，改成另一个独立目录。同步代码会保留已有内容并报错，支持 `.git` 为目录或 worktree 文件的情况。

`fab install` 从控制机干净的仓库解析 `repo.ref`（默认 `HEAD`），下载本仓库的相同提交并显示编译输出；`fab remote` 校验该提交的已有二进制，不更新远端 Git、不编译。构建记录还校验敌手配置能力和二进制 SHA-256，旧上游二进制不能作为新敌手实验运行。部署代码完整包含在本仓库，无须之前的 Mysticeti 部署目录。

## AWS：从下载到运行

命令在 Ubuntu 24.04 控制机上以 `ubuntu` 用户执行。需要网络访问 GitHub、已有 AWS 凭据或控制机 IAM 角色、SSH 私钥。各区域需要可用的默认 VPC 和默认子网。

### 1. 安装控制机依赖

```bash
sudo apt-get update
```

```bash
sudo apt-get install -y build-essential pkg-config libssl-dev libssh2-1-dev zlib1g-dev git curl ca-certificates tmux chrony python3 python3-pip openssh-client
```

### 2. 下载仓库

```bash
mkdir -p "$HOME/experiments"
```

```bash
cd "$HOME/experiments"
```

```bash
git clone https://github.com/DrDaydream/mahi-mahi-ubuntu24.git
```

私有仓库需要控制机和参与构建的验证节点都有本仓库的 Git 读取权限。使用各机器已有的凭据配置；不要把 token 写进仓库地址或配置，部署脚本不会自动分发凭据。

```bash
cd "$HOME/experiments/mahi-mahi-ubuntu24/aws"
```

### 3. Python 依赖

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
```

```bash
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
```

```bash
fab --version
```

### 4. 密钥和 settings

```bash
chmod 600 /home/ubuntu/.ssh/orca-amazon.pem
```

```bash
cp -n settings.example.json settings.json
```

```bash
python3 -m json.tool settings.json
```

示例使用 `orca-amazon` KeyPair、`/home/ubuntu/.ssh/orca-amazon.pem` 私钥和 `mahi-ubuntu24` 实验标签。按实际环境修改 `settings.json`。`ssh_cidrs: []` 检测控制机公网 IPv4 并以 `/32` 配置 SSH。

区域为 `us-east-1`、`eu-north-1`、`ap-southeast-2`、`us-west-1`、`ap-northeast-1`。`build_jobs: 2` 是每台机器的编译任务数，`parallelism: 5` 是并行操作机器数。

### 5. AWS 身份和配置检查

```bash
python3 - <<'PY'
import boto3
identity = boto3.client("sts", region_name="us-east-1").get_caller_identity()
print("Account:", identity["Account"])
print("ARN:", identity["Arn"])
PY
```

```bash
fab check
```

```bash
fab info
```

`fab check` 检查配置和私钥路径；`fab info` 列出 `ManagedBy=mahi-fabric`、`Testbed=mahi-ubuntu24` 标签的实例。IAM 角色需具备 EC2 查询、实例创建与生命周期、安全组管理权限；`fab info` 成功不代表所有创建权限都已验证。

出现 `ec2:DescribeVpcs` 拒绝时，给实际使用的角色补充该查询权限。创建前还会查询 AMI、子网、KeyPair、安全组和实例。代码不修改 IAM 策略。

### 6. 创建节点——仅首次需要

```bash
fab create --nodes=2
```

每区域新增两台，共新增十台收费实例；它不是设置目标总数。中途失败先运行 `fab info` 查看已有实例，不要直接重复创建。缺少的区域 KeyPair 从现有私钥导出公钥后导入，同名 KeyPair 不匹配时停止。

```bash
fab info
```

### 7. 安装和编译

```bash
fab install
```

控制机和各节点安装依赖、Rust 1.78.0、chrony，编译固定提交，成功后写入 `.mahi-build.json`。等整个命令成功再运行实验。

```bash
python3 - <<'PY'
import json
from mahi_aws.build import verify_build
from mahi_aws.settings import load_settings
source = load_settings()["remote"]["source_dir"]
print("Source:", source)
print(json.dumps(verify_build(source), indent=2))
PY
```

### 8. 低负载试运行

```bash
fab remote --nodes=10 --rate=5000 --tx-size=512 --warmup=20 --duration=60 --runs=1
```

A0 基线等待所有活动节点出现共享交易提交后预热，再测量指定时长。A1～A5 在节点进程和指标就绪后预热，允许记录从开始就零提交的攻击结果。工作目录、存储、配置和日志按实验隔离。

### 9. 50000 TPS 实验

```bash
fab remote --nodes=10 --rate=50000 --tx-size=512 --warmup=30 --duration=120 --runs=1 --leaders=4 --wave-length=5 --leader-timeout-ms=250
```

`rate` 是委员会目标总输入。10 节点、`faults=0` 时，每节点 5000 TPS。原型每 100 ms 批量生成，要求 `rate` 为 `10 × nodes` 的整数倍，避免静默取整。

`faults` 是不启动的委员会节点数，其负载生成器也不启动。例如 `rate=50000, nodes=10, faults=3` 时，活动节点合计目标输入为 35000 TPS，结果会记录该值。

### 10. 结果

结果路径打印在终端，位于 `aws/results/run-时间-编号/`。

- `run.json`：状态、参数、节点分布、负载和错误。
- `summary.json` / `summary.csv`：汇总和各节点统计。
- `summary.txt`：终端 SUMMARY 的最终保存版本，含零提交节点和分叉区块输出比例。
- `logs/node-*.log`：节点日志，失败时也尝试下载。
- `metrics/`、`samples.jsonl`：原始指标和采样时刻。
- `genesis/`：委员会和节点配置。

若提示 `No shared commit progress`，查看最新实验节点日志。10 节点通信端口为 1500–1509，指标端口为 1510–1519；源端口是通信端口乘以 10，不要使用 17000 作为起始端口。安全组对本实验节点放行通信范围，指标通过 SSH 从节点本机采集。

### 11. 停止和恢复

只停止实验进程，实例继续运行：

```bash
fab kill
```

停止实例，保留 EBS 磁盘和编译产物：

```bash
fab stop
```

EBS 存储仍然计费。下次启动已有实例，每区域最多两台：

```bash
fab start --max=2
```

随后运行 `fab remote`。需要彻底移除本实验实例及随实例删除的磁盘时才执行 `fab destroy`；控制机已收集的结果保留。

## 迁移现有部署

如果已在 `/home/ubuntu/experiments/mahi-mahi-aws/aws` 配置过实验，克隆后复制现有配置、复用已有实例：

```bash
cp -n /home/ubuntu/experiments/mahi-mahi-aws/aws/settings.json "$HOME/experiments/mahi-mahi-ubuntu24/aws/settings.json"
```

保留原 `testbed_id` 和密钥配置。已修复为 `mahi-mahi-source-随机字符` 的 `source_dir` 可继续使用；若仍是原部署目录，改成未占用的独立源码目录。之后执行 `fab check`、`fab info`、`fab install`，无需再次 `fab create`。

## 本地运行与指标

完整命令见 [本地 Ubuntu 24.04 步骤](docs/local-ubuntu24.md)。本地默认端口 2000，检查监听端口、源端口冲突及乘以 10 的溢出。

AWS 和本地均使用 `latency_s_count{workload="shared"}`、`latency_s_sum{workload="shared"}` 的窗口差值：副本 TPS 为提交数除以实际采样间隔，平均延迟为延迟总和除以提交数；汇总取副本均值，不相加重复计数。

AWS 敌手实验另统计分叉区块输出比例：同一作者、同一轮次的额外已提交摘要数 / 全部非创世提交输出区块数。报告逐节点分子、分母、百分比及诚实副本均值；零输出或旧指标缺失时为 `N/A`。AWS 零提交副本保留 0 TPS，平均延迟只统计有提交的副本。A2～A5 可能重复提交相同交易内容，当前 TPS 是原始条目速率，完整口径见 [敌手实验说明](docs/adversary.md)。

测量不包含生产 Sui 应用执行，也不等于 Bullshark 的最早副本提交或客户端抽样延迟口径。输入速率是生成目标，不是实际到达速率的测量值。

## 测试与限制

在仓库根目录：

```bash
python3 -m pip install --user --break-system-packages -r requirements-test.txt
```

```bash
python3 -m pytest -q
```

测试覆盖 EC2 API 契约、标签、配置、编译记录、目录冲突、Git worktree、生命周期、端口和指标。GitHub Actions 在 Ubuntu 24.04 / Python 3.12 执行测试和 Bash 语法检查，不创建 EC2。

测试使用 EC2 stub、模拟 SSH 和本地验证节点夹具，不代表实际 Rust 共识二进制已通过 AWS 性能验收。以实际 `fab install`、试运行和日志确认环境。

## 许可

保留上游 Apache-2.0 [LICENSE](LICENSE) 和版权声明。新增部署及测试代码同样按 Apache-2.0 提供。来源和版本见 [UPSTREAM.json](UPSTREAM.json)。
