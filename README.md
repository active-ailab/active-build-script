# active-build

`active-build` 是 Active/Zepp 工作区的统一编译入口 CLI。它负责定位工作区根目录、校验目标项目、检查当前 repo XML，并在工作区 `build/` 目录下执行对应的 `make` 构建流程。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 交互式编译 | 无参数启动后，可选择芯片目录、项目、构建入口、模式、BUILD_TYPE、日志和线程数。 |
| 短参完整编译 | 使用 `-f/-p/-m/-j` 指定 family、project、mode 和线程数，同时兼容大写短参别名。 |
| 当前配置继续编译 | `-c <mode>` 可直接复用已有 `build/.config`，其中 `app` 会按 `fw` 处理。 |
| BuildPlan 输入 | `-i <plan-file>` 可直接读取 JSON BuildPlan 执行编译。 |
| 工作区定位 | `-w <path>` 支持传入工作区根目录或其 `build/` 目录。 |
| XML 校验 | 完整编译前会检查 `.repo/manifest.xml` 与 `huamiOS.xml` 或目标项目 XML。 |
| 版本号覆写 | 非当前配置的 main 阶段会覆写 `build/.config` 中的 `BOARD_FIRMWARE_VERSION`，默认值为 `10.0.0`。 |
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
active-build -c sensorhub-ota -a release -u release -j 8
```

使用 BuildPlan：

```sh
active-build -i /tmp/active-build-plan.json
active-build -i /tmp/active-build-plan.json -w /home/zepp/workspace/mod
```

以下顺序式旧语法已经移除：

```text
active-build <family> <project> <mode> [threads]
```

请改用短参形式或 BuildPlan JSON。

## BuildPlan

`active-build -i` 接收一个 JSON 对象，字段如下：

```json
{
  "action": "build",
  "family": "mhs003s",
  "project": "atlas",
  "mode": "sensorhub-ota",
  "threads": "8",
  "reload_defconfig": true,
  "version": "10.0.0",
  "build_type": null,
  "main_build_type": null,
  "sensorhub_build_type": "release",
  "use_current_config": false,
  "workspace": "/home/zepp/workspace/mod",
  "log": false
}
```

字段说明：

- `action` 当前只支持 `build`。
- `mode` 支持 `fw`、`firmware`、`app`、`ota`、`sensorhub`、`sensorhub-fw`、`sensorhub-firmware`、`sensorhub-ota`、`debug`、`release`、`sim`。
- `app` 会被规范化为 `firmware`。
- `debug`、`release` 属于快捷完整编译入口，最终会规范化为 `sensorhub-ota` 流程。
- `build_type` 支持 `debug`、`inspect`、`release_log`、`release`，也可由 `main_build_type`、`sensorhub_build_type` 分别覆盖。
- 实际拼接 `make` 指令时，只有 `release` 会被写入 `BUILD_TYPE=release`；其他类型不会写入运行命令。
- `threads` 必须是正整数。
- `reload_defconfig` 在切换 family 或 project 时通常应为 `true`；若明确延用当前工作区上次编译配置，则应设为 `false`。
- `use_current_config` 为 `true` 时，CLI 会从 `build/.config` 中推断 family 和 project。
- `workspace` 可指向工作区根目录，也可直接指向 `build/` 目录。
- `log` 控制是否写入 `build/logs/active-build/`。

## 运行状态文件

CLI 自身生成的运行状态收敛在 `build/.active-build-state.json`，成功构建后会记录：

- `family`
- `project`
- `threads`
- `updated_at`

未显式传入 `-j` 时，CLI 会优先复用 `build/.active-build-state.json` 中的 `threads`。旧版本生成的 `.hmbuild_last_threads` 仅作为迁移兼容读取；成功写入新的状态文件后，CLI 会尝试清理这个旧文件。

版本号覆写发生在当前工作区的 `build/.config` 上：非当前配置、非 sensorhub `out_hub` 阶段会写入 `BOARD_FIRMWARE_VERSION="<version>"`，随后执行 `make silentoldconfig`。`use_current_config=true` 和 sensorhub `out_hub` 配置会跳过版本号覆写。

## Agent 使用注意

本节不是 CLI 自身功能说明，而是给使用本仓库 Skill 的 Agent 的操作约定。

当 agent 使用本工程发起编译时，推荐按以下原则执行：

1. 优先生成 BuildPlan，并先向用户展示后再执行。
2. 如果当前工作区已有上次编译信息，优先读取并向用户确认是否延用。
3. 如果用户确认延用，则 BuildPlan 中应设置 `reload_defconfig=false`。
4. Skill 执行编译命令时不做实时监控；等待命令完成或中断后，再统一读取结果。
5. 如果编译失败或中断，需要返回命令、工作目录、退出码和关键错误内容。
6. 通过 Lark MCP 发送结果时，只发送简要的构建计划和结果，不发送文件路径、日志路径或大段命令输出。

需要注意的是，Agent 即便在这些约束下，实际执行时仍可能进行流程监控、过程分析或中途总结，这会带来额外 token 消耗，也可能让长时间编译任务的上下文成本明显增加。使用者需要关注这一现象，并在需要时明确要求 Agent 等待命令自然结束后再分析。

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
