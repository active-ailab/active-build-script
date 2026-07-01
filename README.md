# active-build

Active/Zepp 工作区统一编译 CLI。定位工作区根目录、校验目标项目，并在 `build/` 下执行 `make` 构建流程。

## 安装

```sh
# 安装 CLI（active-build + active-bstyle）
sh install.sh cli

# 安装 CLI 和 Agent Skill
sh install.sh

# 非交互安装 Skill 到指定 Agent 插件
SKILL_PROJECT_ROOT="/path/to/project" SKILL_PLUGIN=codex sh install.sh
```

安装到 `~/.local/bin`，若不在 `PATH` 中请添加：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## 用法

### 交互模式

无参数启动，逐步选择 family、project、构建模式和参数：

```sh
active-build
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `-f <family>` | 芯片目录：`mhs003`、`mhs003s` |
| `-p <project>` | 项目名称 |
| `-m <mode>` | 构建模式（见下方） |
| `-j <threads>` | 线程数，默认 `CPU 核数 × 2`，其次复用上次构建记录 |
| `-c <mode>` | 复用当前 `build/.config` 继续编译 |
| `-d` | 强制重新加载 defconfig（仅 `-c` 模式有效） |
| `-v <version>` | 版本号 |
| `-i <file>` | 从 BuildPlan JSON 执行编译 |
| `-w <path>` | workspace 根目录或其 `build/` 目录 |
| `-b <type>` | 全局 `BUILD_TYPE` |
| `-a <type>` | main/fw/ota 阶段 `BUILD_TYPE` |
| `-u <type>` | sensorhub 阶段 `BUILD_TYPE` |
| `-l` | 写入构建日志到 `build/logs/active-build/` |

所有短参同时接受大写别名（如 `-F`、`-M`、`-J`）。

### 构建模式

| 输入 | 归一化 | 实际行为 |
|------|--------|----------|
| `fw` / `firmware` / `app` | `firmware` | 编译主固件 |
| `ota` | `ota` | 编译 OTA 包 |
| `sensorhub` | `sensorhub` | 仅编译 sensorhub |
| `sensorhub-fw` / `sensorhub-firmware` | `sensorhub-firmware` | sensorhub + 固件 |
| `sensorhub-ota` | `sensorhub-ota` | sensorhub + OTA（默认） |
| `debug` / `release` | `sensorhub-ota` | 快捷完整编译入口 |
| `sim` | `sim` | 模拟器（distclean → clean → defconfig + BUILD_SIM → make ota） |

### BUILD_TYPE

`debug`、`inspect`、`release_log`、`release`。实际写入 `make` 命令的只有 `release`（`BUILD_TYPE=release`），其余类型仅保留在 BuildPlan 语义层。

### 使用示例

```sh
# 完整编译
active-build -f mhs003 -p cologne -m debug -j 8
active-build -f mhs003s -p atlas -m sensorhub-ota -j 8 -b release

# 基于当前配置继续编译
active-build -c app -j 8
active-build -c ota -j 8
active-build -c sensorhub-ota -a release -u release -j 8

# 编译并指定版本（旧工程：覆写 BOARD_FIRMWARE_VERSION；新工程：传入 BUILD_FW_VER）
active-build -f mhs003 -p cologne -m ota -v 23.4 -j 8
active-build -f mhs003 -p cologne -m ota -v 6.1.23.4 -j 8

# BuildPlan 模式
active-build -i /tmp/active-build-plan.json
active-build -i /tmp/active-build-plan.json -w /home/zepp/workspace/mod
```

> 已移除的旧语法：`active-build <family> <project> <mode> [threads]`

## BuildPlan JSON

`-i` 读取的 JSON 结构：

```json
{
  "action": "build",
  "family": "mhs003s",
  "project": "atlas",
  "mode": "sensorhub-ota",
  "threads": "8",
  "reload_defconfig": true,
  "version": "23.4",
  "version_explicit": true,
  "build_type": null,
  "main_build_type": null,
  "sensorhub_build_type": "release",
  "use_current_config": false,
  "workspace": "/home/zepp/workspace/mod",
  "log": false
}
```

关键规则：

- `mode` 支持 `fw`、`firmware`、`app`、`ota`、`sensorhub`、`sensorhub-fw`、`sensorhub-firmware`、`sensorhub-ota`、`debug`、`release`、`sim`。`app` → `firmware`，`debug`/`release` → `sensorhub-ota`。
- `reload_defconfig` 切换 family/project 时通常为 `true`，延用当前配置时设为 `false`。
- JSON 含 `version` 但缺 `version_explicit` 时，CLI 按 `version_explicit: true` 处理。
- `use_current_config: true` 时从 `build/.config` 推断 family/project。
- `workspace` 可为项目根目录或 `build/` 子目录。

## 版本号规则

新旧工程以 `build/build_rules/fw_version.mk` 是否存在区分。

### 旧工程（无 `fw_version.mk`）

- 默认版本：`10.0.0`
- 显式 `-v` 或 `version_explicit: true` 时覆写 `build/.config` 的 `BOARD_FIRMWARE_VERSION`
- 纯 `sensorhub` 模式覆写 `build/out_hub/.config`
- 覆写后执行对应 `silentoldconfig`

### 新工程（有 `fw_version.mk`）

- 默认版本：`999.999`
- 不 patch 任何 `.config` 文件
- 版本仅在 `make ota` 阶段通过环境变量传入：
  - 默认 `999.999` 或两段版本 `c.d` → `FW_VER_STRATEGY=os_global BUILD_FW_VER=<version>`
  - 四段版本 `a.b.c.d` → `BUILD_FW_VER=<version>`（不指定 `FW_VER_STRATEGY`）
- 只接受两段或四段数字版本，三段 `10.0.0` 会被拒绝
- `sim` 模式使用 `BOARD_FIRMWARE_VERSION`

## active-bstyle

独立的 `.style` → `.bstyle` 编译命令，调用工作区内 `build/cmd/linux64/bstylenc`（或 `linux32`）。

```sh
# 交互模式
active-bstyle

# 命令行（单文件）
active-bstyle -i Foo.style -o Foo.bstyle -f mhs003 -p cologne
active-bstyle -i Foo.style --width 466 --height 466 --pixel-ratio 1.0 --dry-run

# 命令行（批量目录）
active-bstyle -i ./styles/ -f mhs003 -p cologne
active-bstyle -i ./styles/ -o ./output/ -f mhs003 -p cologne --dry-run
```

参数推导规则：

- `-i` 未指定时，仅当前目录有唯一 `.style` 文件时自动推导
- `-o` 未指定时，自动生成同目录同名 `.bstyle`；`-i` 为目录时 `-o` 也为目录，默认与 `-i` 同目录
- 当 `-i` 传入目录时，批量处理目录下所有 `.style` 文件，共享同一套 defconfig 参数
- `--width`/`--height`/`--pixel-ratio` 未指定时，从 `configs/<family>/<family>_<project>_defconfig` 推导：
  - width: `STORYBOARD_DISPLAY_WIDTH` → `AMOLED_PANEL_WIDTH`
  - height: `STORYBOARD_DISPLAY_HEIGHT` → `AMOLED_PANEL_HEIGHT`
  - pixel_ratio: `HM_FONT_DENSTIY` → `HM_DISPLAY_DENSTIY`
- `family`/`project` 优先从 `build/.active-build-state.json` 和 `build/.config` 推导

## 运行状态

成功构建后写入 `build/.active-build-state.json`，记录 `family`、`project`、`threads`、`updated_at`。下次 `-j` 未指定时优先复用其中的线程数。旧的 `.hmbuild_last_threads` 文件在写入新状态后自动清理。

## 编译后烧录确认

交互终端中，固件构建成功后会默认进入烧录确认流程，不需要额外参数。

规则：

- `firmware` / `sensorhub-firmware`：用户确认后在 `build/` 目录执行 `v3dl app`
- `ota` / `sensorhub-ota`：用户确认后在 `build/` 目录执行 `v3dl ota`
- `sensorhub` / `sim`：直接跳过烧录确认
- 非交互终端：跳过烧录确认，避免脚本化构建卡住

确认默认值为 `N`，直接回车不会烧录。

## Agent 使用注意

本节不是 CLI 自身功能说明，而是给使用本仓库 Skill 的 Agent 的操作约定。

当 agent 使用本工程发起编译时，推荐按以下原则执行：

1. 优先生成 BuildPlan，并先向用户展示后再执行。
2. 如果当前工作区已有上次编译信息，优先读取并向用户确认是否延用。
3. 如果用户确认延用，则 BuildPlan 中应设置 `reload_defconfig=false`。
4. Skill 执行编译命令时默认进入静默模式；等待命令完成或中断后，再统一读取结果。
5. 如果编译失败或中断，需要返回命令、工作目录、退出码和关键错误内容。
6. 通过 Lark MCP 发送结果时，只发送简要的构建计划和结果，不发送文件路径、日志路径或大段命令输出。

这里的"静默模式"并不等于完全不消耗 token，也不等于底层执行过程完全零监控。更准确地说，它表示 agent 不基于中间日志做过程分析、转述或阶段性总结，只在命令结束后再统一判读结果。

需要特别说明的是，即便启用静默模式，实际执行时仍可能发生以下 token 消耗：

- 执行工具为等待命令结束而保留 session 或轮询进程状态。
- 上层交互规则要求 agent 发送简短等待状态。
- 命令遇到交互提示、超时、权限问题或其他必须立即处理的异常。

因此，静默模式的目标是减少无意义的过程分析和上下文膨胀，而不是承诺"零 token"或"绝对不读取任何运行态信息"。

关于工作区已有编译信息，优先读取以下内容：

- `build/.active-build-state.json`：记录上次的 `family`、`project`、`threads` 和更新时间
- `build/.config`

可复用时，应至少向用户确认以下内容：

- 是否延用上次的 family / project
- 是否延用上次线程数
- 是否按当前工作区已有配置继续编译

关于 `mode` 配置，Skill 还有额外规则：

- BuildPlan 的 `mode` 默认快速构建应直接使用 `mode: "sensorhub-ota"`。
- 不要把 `debug` 或 `release` 当作最终确认给用户的 `mode` 配置值；它们只表示快捷完整编译入口，最终应落实为实际构建模式。
- 如果当前工作区已有上次编译信息，除确认 family / project 外，也需要确认这次是否仍然延用上次 `mode` 配置，还是改用新的 `mode`。

## 工程结构

```text
active-build-script/
  cli/
    bin/active-build
    bin/active-bstyle
    src/active_cli/
      active_common.py
      active_build_cli.py
      active_bstyle_cli.py
    tests/
    pyproject.toml
  skill/
    active-build/
      SKILL.md
      agents/openai.yaml
      references/commands.md
  install.sh
  README.md
  VERSION
```

## 验证

```sh
cd cli
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
PYTHONPATH=src python3 -m active_cli.active_build_cli --help
PYTHONPATH=src python3 -m active_cli.active_bstyle_cli --help
```
