# active-build

`active-build` 是 Active/Zepp 工作区的统一编译入口 CLI。它负责定位工作区根目录、校验目标项目、检查当前 repo XML，并在工作区 `build/` 目录下执行对应的 `make` 构建流程。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 交互式编译 | 无参数启动后，可选择芯片目录、项目、构建入口、模式、BUILD_TYPE、日志和线程数。 |
| 短参完整编译 | 使用 `-f/-p/-m/-j` 指定 family、project、mode 和线程数，同时兼容大写短参别名。 |
| 当前配置继续编译 | `-c <mode>` 可直接复用已有 `build/.config`，其中 `app` 会按 `fw` 处理；旧工程未显式传入 `-v` 时不覆写版本，新工程版本只在 `make ota` 阶段生效。 |
| BuildPlan 输入 | `-i <plan-file>` 可直接读取 JSON BuildPlan 执行编译。 |
| bstyle 编译 | `active-build bstyle` 可独立调用工作区内 `build/cmd/linux32|linux64/bstylenc`，不会串入固件编译流程；`bstylenc` 是兼容别名。 |
| 工作区定位 | `-w <path>` 支持传入工作区根目录或其 `build/` 目录。 |
| XML 校验 | 完整编译前会检查 `.repo/manifest.xml` 与 `huamiOS.xml` 或目标项目 XML。 |
| 版本号兼容 | 旧工程沿用 `BOARD_FIRMWARE_VERSION` 覆写；检测到 `build/build_rules/fw_version.mk` 的新工程只在 `make ota` 阶段传版本。默认或两段版本会追加 `FW_VER_STRATEGY=os_global BUILD_FW_VER=<version>`，四段版本只追加 `BUILD_FW_VER=<version>`。 |
| 分阶段 BUILD_TYPE | `-b` 设置全局 BUILD_TYPE，`-a/-u` 可分别覆盖 main/fw/ota 和 sensorhub 阶段；实际 `make` 命令只会写入 `release`。 |
| Sensorhub 产物处理 | sensorhub 产物会生成到 `build/out_hub/`，再同步回产品目录下的 `sensorhub/`。 |
| 构建日志 | `-l` 会在 `build/logs/active-build/` 下写入最新日志和历史日志。 |

## 工程结构

```text
active-build-script/
  cli/
    bin/active-build
    src/active_build/
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

## 安装

安装 CLI 和 Skill：

```sh
sh install.sh
```

仅安装 CLI：

```sh
sh install.sh cli
```

安装或刷新 Skill，同时刷新 CLI：

```sh
sh install.sh skills
```

非交互安装到 Codex 项目级 skills：

```sh
SKILL_PROJECT_ROOT="/home/zepp/workspace/mod" SKILL_PLUGIN=codex sh install.sh
```

非交互安装到 GitHub Copilot 项目级 skills：

```sh
SKILL_PROJECT_ROOT="/home/zepp/workspace/mod" SKILL_PLUGIN=github-copilot sh install.sh
```

如果 `~/.local/bin` 不在 `PATH` 中，请加入 shell 配置：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## CLI 用法

查看帮助：

```sh
active-build --help
```

进入交互式编译：

```sh
active-build
```

使用短参执行完整编译：

```sh
active-build -f mhs003 -p cologne -m debug -j 8
active-build -f mhs003s -p atlas -m sensorhub-ota -j 8 -b release
active-build -f mhs003 -p cologne -m release -j 16
```

基于当前配置继续编译：

```sh
active-build -c app -j 8
active-build -c ota -j 8
active-build -c fw -v 10.0.0 -j 8
active-build -f mhs003 -p cologne -m ota -v 23.4 -j 8
active-build -f mhs003 -p cologne -m ota -v 6.1.23.4 -j 8
active-build -c sensorhub-ota -a release -u release -j 8
```

使用 BuildPlan：

```sh
active-build -i /tmp/active-build-plan.json
active-build -i /tmp/active-build-plan.json -w /home/zepp/workspace/mod
```

独立编译 `.style`：

```sh
active-build bstyle -i ui/Sports/prototype/style/466x466-mdpi/Foo.style
active-build bstyle -f mhs003 -p cologne -i Foo.style -o Foo.bstyle
active-build bstyle -i Foo.style -w /home/zepp/workspace/mod --dry-run
```

以下顺序式旧语法已经移除：

```text
active-build <family> <project> <mode> [threads]
```

请改用短参形式或 BuildPlan JSON。

## BuildPlan

`active-build -i` 接收一个 JSON 对象。固件构建字段如下：

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

字段说明：

- `action` 使用 `build`。
- `mode` 支持 `fw`、`firmware`、`app`、`ota`、`sensorhub`、`sensorhub-fw`、`sensorhub-firmware`、`sensorhub-ota`、`debug`、`release`、`sim`。
- `app` 会被规范化为 `firmware`。
- `debug`、`release` 属于快捷完整编译入口，最终会规范化为 `sensorhub-ota` 流程。
- `build_type` 支持 `debug`、`inspect`、`release_log`、`release`，也可由 `main_build_type`、`sensorhub_build_type` 分别覆盖。
- 实际拼接 `make` 指令时，只有 `release` 会被写入 `BUILD_TYPE=release`；其他类型不会写入运行命令。
- `threads` 必须是正整数。
- `reload_defconfig` 在切换 family 或 project 时通常应为 `true`；若明确延用当前工作区上次编译配置，则应设为 `false`。
- `version` 继续作为唯一版本输入字段，不新增 BuildPlan 字段。旧工程中它用于覆写 `BOARD_FIRMWARE_VERSION`；新工程中它用于生成 OTA 阶段的 `BUILD_FW_VER`。
- `version_explicit` 表示 BuildPlan 是否显式要求版本。BuildPlan JSON 中包含 `version` 但未写 `version_explicit` 时，CLI 会按 `true` 处理。新工程未显式传入版本时默认使用 `999.999`。
- 新工程版本格式只支持两段 `c.d` 或四段 `a.b.c.d` 数字版本。默认 `999.999` 或显式两段版本会在 `make ota` 上追加 `FW_VER_STRATEGY=os_global BUILD_FW_VER=<version>`；四段版本只追加 `BUILD_FW_VER=<version>`。
- `use_current_config` 为 `true` 时，CLI 会从 `build/.config` 中推断 family 和 project。
- `workspace` 可指向工作区根目录，也可直接指向 `build/` 目录。
- `log` 控制是否写入 `build/logs/active-build/`。

`bstylenc` JSON 字段如下：

```json
{
  "action": "bstylenc",
  "family": null,
  "project": null,
  "input": "ui/Sports/prototype/style/466x466-mdpi/Foo.style",
  "output": null,
  "workspace": "/home/zepp/workspace/mod",
  "width": null,
  "height": null,
  "pixel_ratio": null,
  "dry_run": false,
  "log": false
}
```

规则：

- `action` 使用 `bstylenc` 时只执行 bstyle 编译，不进入 `fw`、`ota`、`sensorhub` 等固件编译流程。
- `input` 传给底层 `bstylenc -i`；未配置时，参数模式只会在当前目录存在唯一 `.style` 文件时自动推导，否则报错。
- `output` 传给底层 `bstylenc -o`；为 `null` 或缺失时由 `input.style` 生成同目录同名 `.bstyle`。
- `width`、`height`、`pixel_ratio` 为 `null` 或缺失时，从 `configs/<family>/<family>_<project>_defconfig` 推导；非 `null` 时使用 JSON 输入值。
- 推导宽高优先读取 `STORYBOARD_DISPLAY_WIDTH` / `STORYBOARD_DISPLAY_HEIGHT`，缺失时回退到 `AMOLED_PANEL_WIDTH` / `AMOLED_PANEL_HEIGHT`。
- 推导 `pixel_ratio` 优先读取 `HM_FONT_DENSTIY`，缺失时回退到 `HM_DISPLAY_DENSTIY`。
- `family`、`project` 可省略；需要读取 defconfig 时，会优先从 `build/.active-build-state.json` 和 `build/.config` 推导，失败则报错。
- `dry_run` 为 `true` 时只打印最终 `bstylenc` 命令，不执行。

## 运行状态文件

CLI 自身生成的运行状态收敛在 `build/.active-build-state.json`，成功构建后会记录：

- `family`
- `project`
- `threads`
- `updated_at`

未显式传入 `-j` 时，CLI 会优先复用 `build/.active-build-state.json` 中的 `threads`。旧版本生成的 `.hmbuild_last_threads` 仅作为迁移兼容读取；成功写入新的状态文件后，CLI 会尝试清理这个旧文件。

版本号兼容规则如下：

- 旧工程（未检测到 `build/build_rules/fw_version.mk`）保持原逻辑：显式带 `-v` 或 `version_explicit=true` 时覆写 `BOARD_FIRMWARE_VERSION`，main/fw/ota 写 `build/.config`，纯 `sensorhub` 写 `build/out_hub/.config`，并执行对应 `silentoldconfig`。
- 新工程（检测到 `build/build_rules/fw_version.mk`）不再 patch `.config` 中的 `BOARD_FIRMWARE_VERSION`，也不在 defconfig、普通 firmware、sensorhub 或 `silentoldconfig` 命令上追加版本变量。
- 新工程只在 `make ota` 阶段追加版本变量；未显式传入版本时默认 `version=999.999`。
- 新工程默认值 `999.999` 或显式两段版本 `c.d` 会生成 `FW_VER_STRATEGY=os_global BUILD_FW_VER=<version>`，由项目拼接 OS 前两位。
- 新工程显式四段版本 `a.b.c.d` 会只生成 `BUILD_FW_VER=<version>`，不强行指定 `FW_VER_STRATEGY`。
- 新工程显式 `-v` 只接受两段或四段数字版本，例如 `23.4` 或 `6.1.23.4`；三段 `10.0.0` 会被拒绝。

## Agent 使用注意

本节不是 CLI 自身功能说明，而是给使用本仓库 Skill 的 Agent 的操作约定。

当 agent 使用本工程发起编译时，推荐按以下原则执行：

1. 优先生成 BuildPlan，并先向用户展示后再执行。
2. 如果当前工作区已有上次编译信息，优先读取并向用户确认是否延用。
3. 如果用户确认延用，则 BuildPlan 中应设置 `reload_defconfig=false`。
4. Skill 执行编译命令时默认进入静默模式；等待命令完成或中断后，再统一读取结果。
5. 如果编译失败或中断，需要返回命令、工作目录、退出码和关键错误内容。
6. 通过 Lark MCP 发送结果时，只发送简要的构建计划和结果，不发送文件路径、日志路径或大段命令输出。

这里的“静默模式”并不等于完全不消耗 token，也不等于底层执行过程完全零监控。更准确地说，它表示 agent 不基于中间日志做过程分析、转述或阶段性总结，只在命令结束后再统一判读结果。

需要特别说明的是，即便启用静默模式，实际执行时仍可能发生以下 token 消耗：

- 执行工具为等待命令结束而保留 session 或轮询进程状态。
- 上层交互规则要求 agent 发送简短等待状态。
- 命令遇到交互提示、超时、权限问题或其他必须立即处理的异常。

因此，静默模式的目标是减少无意义的过程分析和上下文膨胀，而不是承诺“零 token”或“绝对不读取任何运行态信息”。

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

## 验证

```sh
cd /home/zepp/workspace/active-lab/active-build-script/cli
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
PYTHONPATH=src python3 -m active_build --help
```
