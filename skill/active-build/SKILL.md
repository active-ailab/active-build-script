---
name: active-build
description: 当用户要求通过 active-build CLI 构建 Active/Zepp 工作区、迁移 hmbuild.py 用法、生成或确认 BuildPlan、串行执行多个构建计划、或诊断 active-build 构建失败时使用。本 skill 只负责 CLI 使用工作流，不重写 CLI 的构建逻辑。
---

# active-build CLI Skill

使用本 skill 时，必须把已安装的 `active-build` CLI 作为唯一执行入口。skill 可以帮助推断、生成、确认 BuildPlan，但不能在 agent 侧重写编译流程。

## 必须遵守的行为

- 必须在目标 Active 工作区或其子目录内运行 `active-build`。
- 必须以 CLI 为项目校验、manifest 检查、defconfig 处理、sensorhub 产物处理、命令拼接顺序的唯一事实来源。
- agent 发起编译时，优先使用 BuildPlan JSON + `active-build -i <plan-file>`。
- 编译前必须先向用户展示 BuildPlan，并等待用户确认。
- 执行前必须给出将要运行的终端命令。
- 执行编译前必须先判断当前环境是否具备完全访问权限；若不是完全访问权限，不得直接发起编译。
- 若当前不是完全访问权限，必须提前告知用户切换权限后再执行，并明确说明在当前权限下继续编译可能失败、可能被沙箱拦截、也可能导致结果不完整或误判。
- skill 执行编译命令时，禁止在命令运行期间读取、分析、转述、总结或判断任何过程输出。
- 编译执行期间禁止任何形式的中途判读与思考，不应为了观察过程输出而持续轮询、流式消费、追加读取或展开分析。
- 编译执行期间应尽量避免额外 token 消耗；只在命令自然结束或被中断后，再一次性回溯获取结果并进行判读。
- 若构建失败或中断，必须返回命令、工作目录、退出码，以及关键错误内容。
- 若 CLI 在输出中打印了 `>>> 执行:` 的实际命令，需要保留这些命令，便于用户手动复现。
- 成功或失败后，可通过本地 Lark MCP 发送通知；通知内容只允许简述构建计划与结果，不要发送文件路径、日志路径或大段输出。
- 多个 BuildPlan 需要先整体确认，再按顺序串行执行。

## 工作区已有编译信息时的优先流程

如果当前工作区已经存在编译信息，不要直接重新推断一个全新的 BuildPlan。优先读取并整理以下内容：

- `build/.active-build-state.json`：记录上次的 `family`、`project`、`threads` 和更新时间
- `build/.config`

读取后，需要先向用户确认是否延用。确认时至少说明：

- 上次的 `family`
- 上次的 `project`
- 当前可推断的配置是否仍可复用
- 上次的线程数（如果能读到）

如果用户确认延用：

- BuildPlan 中应设置 `reload_defconfig: false`
- 可以继续沿用已有 `family` / `project`
- 若用户要求基于当前配置继续编译，可使用 `use_current_config: true`

如果用户不延用：

- 再按正常流程重新推断或收集编译参数

如果当前工作区已有上次编译信息，除 family / project 外，还需要额外确认是否继续沿用上次的 `mode` 配置。

## BuildPlan 优先工作流

用户要求 agent 发起编译时，按下面流程执行：

1. 解析目标工作区根目录。必须包含 `configs/` 与 `build/`；若用户当前在子目录中，需要向上定位。
2. 若当前工作区已有编译信息，先按“已有编译信息优先流程”处理。
3. 在需要重新推断时，收集或推断 `family`、`project`、`mode`、build type、版本号、线程数、日志开关、是否延用当前配置。
4. 为每个编译任务生成一个 BuildPlan JSON。
5. 将 BuildPlan 展示给用户确认。
6. 在执行前检查当前是否具备完全访问权限；若不具备，先停止执行并告知用户切换权限及风险。
7. 确认且权限满足后执行：

```sh
active-build -i <plan-file>
```

若 BuildPlan 中没有绝对路径 `workspace`，则应在目标工作区内执行；若从其他目录执行，则追加 `-w <workspace>`。
8. 命令启动后，不要在执行过程中基于过程输出做任何中途反馈、推理或状态判断；仅在命令结束或终止后统一回溯结果。

## BuildPlan 字段

CLI 接收的 BuildPlan 字段如下：

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

规则：

- `action` 必须是 `build`。
- `mode` 支持 `fw`、`firmware`、`app`、`ota`、`sensorhub`、`sensorhub-fw`、`sensorhub-firmware`、`sensorhub-ota`、`debug`、`release`、`sim`。
- `app` 会被 CLI 规范化为 `firmware`。
- `build_type` 支持 `debug`、`inspect`、`release_log`、`release`。
- `build_type` 可作为全局默认值，由 `main_build_type` 或 `sensorhub_build_type` 覆盖分阶段设置。
- 但在 CLI 实际拼接 `make` 指令时，只有 `release` 会被写入运行命令；其他 build type 只保留在 BuildPlan 语义层。
- `threads` 必须是正整数。agent 生成 BuildPlan 时，用户未指定则默认用 `"8"`。
- `reload_defconfig` 在完整重配时通常为 `true`；只有明确延用当前工作区已有编译配置时，才设为 `false`。
- `use_current_config` 为 `true` 时，可省略 `family` 和 `project`，由 CLI 从 `build/.config` 推断。
- `workspace` 可以是工作区根目录，也可以是其 `build/` 目录。
- `log` 控制是否写入 `build/logs/active-build/`。

## 用户只说“编译”时

如果用户只要求编译，没有给 family、project、mode、threads：

1. 定位当前工作区根目录，要求包含 `configs/`、`build/`，最好还包含 `.repo/`。
2. 优先检查当前工作区是否存在已有编译信息；若存在，先向用户确认是否延用。
3. 若不延用，则读取当前 manifest：

```sh
readlink -f .repo/manifest.xml
```

4. 以活动 XML 的 basename（去掉 `.xml`）作为第一候选 project；若为 `huamiOS.xml` 或无法唯一判断，则再结合 `build/.config` 中的 `HMI_BUILD_BOARD` 与 `HMI_PRODUCT_CUSTOMIZE_DIR` 判断。
5. 校验以下文件是否存在：

```text
configs/<family>/<family>_<project>_defconfig
configs/<family>/<family>_<project>_sensorhub_defconfig
```

6. 若只匹配到一个 family，则使用它；若匹配多个或无法匹配，不要猜测，直接向用户确认缺失信息。
7. 关于 `mode` 配置，不要把 `debug` 或 `release` 作为最终确认给用户的构建模式；默认快速构建应直接使用：

```json
{
  "action": "build",
  "family": "<matched-family>",
  "project": "<matched-project>",
  "mode": "sensorhub-ota",
  "threads": "8",
  "reload_defconfig": true,
  "version": "10.0.0",
  "build_type": null,
  "main_build_type": null,
  "sensorhub_build_type": null,
  "use_current_config": false,
  "workspace": "<workspace-root>",
  "log": false
}
```

8. `debug` / `release` 只作为 CLI 内部快捷完整编译入口使用；对用户展示和最终确认时，应明确成实际构建模式。
9. 展示最终 BuildPlan，等待用户确认后再运行。

## 用户给了部分信息时

- 若用户给了 project/module，先把它作为 `project` 候选，再校验其在 `configs/<family>/` 下是否存在。
- 若用户给了 family，则在该 family 下校验目标 project。
- 若用户要求使用当前配置继续编译，则设置 `use_current_config: true`，并使用用户指定的 `mode`，例如 `fw`、`ota`、`sensorhub-ota`。
- 若用户要求 `app`，可使用 `mode: "app"` 或 `mode: "fw"`；CLI 会将其视为 firmware。
- 若用户提到 debug/release/sim，需要区分“快捷入口”和“最终 mode 配置”。
- BuildPlan 对用户展示和最终确认时，不要把 `debug` 或 `release` 作为最终 `mode` 值，改用明确的实际模式，例如 `sensorhub-ota`。
- 用户未指定线程数时，agent 生成 BuildPlan 默认使用 `"8"`。

## Lark MCP 结果通知规则

通过 Lark MCP 发送结果时，只发送简洁摘要，不贴文件、日志、绝对路径或大段命令输出。推荐结构如下：

```text
构建结果：成功 / 失败 / 中断
family: mhs003s
project: mod
mode: sensorhub-ota
threads: 8
reload_defconfig: false
use_current_config: true
build_type: 默认
main_build_type: 默认
sensorhub_build_type: release
```

失败时可附一行关键错误摘要，但不要贴长日志。

## 直接 CLI 命令

需要精确命令示例时，读取 `references/commands.md`。

## 权限检查要求

发起编译前，必须先判断当前会话是否具备可覆盖目标工作区编译流程所需的完全访问权限。

- 若当前不是完全访问权限，停止在 agent 侧直接执行编译。
- 向用户明确说明需要切换到完全访问权限后再继续。
- 同时提示风险：在当前权限下继续执行，可能在拉起子命令、写入构建目录、访问工作区外依赖、生成中间产物或日志时失败，也可能让最终结果不完整，导致错误诊断失真。
- 只有在权限满足后，才进入实际 `active-build` 执行阶段。

常见命令：

```sh
active-build --help
active-build
active-build -f <family> -p <project> -m <mode> -j <threads>
active-build -c <mode> -j <threads>
active-build -i <plan-file>
active-build -i <plan-file> -w <workspace>
```

已移除的旧命令形式：

```text
active-build <family> <project> <mode> [threads]
```

不要再使用该顺序式写法。

## 失败汇报格式

失败时使用下面的结构：

```text
Command: active-build -i /tmp/active-build-plan-mod.json
Cwd: /home/zepp/workspace/mod
Exit code: 2
Key output:
...
```

如果是队列中的某个 BuildPlan 失败，需要立刻报告该失败。只有在失败不会影响后续任务，或者用户已明确要求继续整个队列时，才继续执行后续 BuildPlan。
