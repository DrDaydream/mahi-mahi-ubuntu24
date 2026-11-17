# Ubuntu 24.04 单机运行

假定部署仓库位于 `$HOME/experiments/mahi-mahi-ubuntu24`。工具在 `local/`，构建使用独立的固定版本上游源码目录。

## 1. 安装依赖

```bash
sudo apt-get update
```

```bash
sudo apt-get install -y build-essential pkg-config libssl-dev ca-certificates curl git python3 python3-yaml
```

## 2. 准备固定源码

```bash
cd "$HOME/experiments/mahi-mahi-ubuntu24"
```

```bash
bash aws/scripts/sync_source.sh "$HOME/experiments/mahi-mahi-source" 7c928c06bd436c96b94065119e0e8c2d4ac1bcb6 https://github.com/PasinduTennage/mahi-mahi-consensus.git
```

不存在时克隆，已有正确源码仓库可复用。非空非 Git 目录会保留并报错。不要把部署仓库作为源码目标。

## 3. 安装 Rust

已有 rustup 时跳过下面下载和安装 rustup 的两条命令。

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o /tmp/mahi-rustup-init
```

```bash
sh /tmp/mahi-rustup-init -y --profile minimal --default-toolchain none
```

```bash
source "$HOME/.cargo/env"
```

```bash
rustup toolchain install 1.78.0 --profile minimal
```

## 4. 编译

```bash
MAHI_BUILD_JOBS=2 bash local/build.sh "$HOME/experiments/mahi-mahi-source"
```

仅构建 `mysticeti` 节点，使用 `--locked --release`。日志在源码目录 `build-logs/`，成功后生成 `target/mahi-build.json`，记录源码提交、Rust 版本、二进制和 Cargo.lock 哈希。

## 5. 启动四个节点

```bash
python3 local/run_local.py --repo "$HOME/experiments/mahi-mahi-source" --nodes 4 --rate 1000 --tx-size 512 --warmup 10 --duration 60 --base-port 2000
```

目标总输入 1000 TPS，每节点 250 TPS。默认 wave length 为 5、leaders 为 4、leader timeout 为 250 ms，每节点两个 Tokio worker 线程。

网络端口为 2000–2003，指标端口为 2004–2007，源端口为 20000、20010、20020、20030。端口占用时可使用 `--base-port 2500`，不要使用 17000 或 18000。

必须满足 `(base_port + nodes - 1) × 10 <= 65535`，`rate` 必须是 `10 × nodes` 的整数倍。

工具等待全部节点提交、预热、测量，再停止本次进程。Ctrl+C 也会尝试清理并保留日志，不会全局终止同名进程。

## 6. 结果

路径打印在终端，默认位于 `$HOME/experiments/mahi-mahi-source/results/local/run-时间-编号/`，包括统计、日志、配置和原始指标。超时或进程退出先查看对应 `node-*.log`。

本地和 AWS 使用不同的构建记录文件。交替使用两套工具构建同一源码目录后，重新执行对应构建步骤，再使用相应验证与运行入口。
