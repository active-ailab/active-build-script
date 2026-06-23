# active-build 命令参考

`active-build` 是唯一受支持的执行入口。agent 可以生成 BuildPlan，但不能绕过 CLI 自己重写编译逻辑。

## 帮助

查看帮助：

```sh
active-build --help
```

## agent 优先执行方式

agent 发起固件构建或 bstyle 编译时，优先使用 BuildPlan JSON + `active-build -i <plan-file>`。短参形式主要用于用户手动执行、调试或 dry-run 验证，不作为 agent 默认执行方式。

```sh
active-build -i /tmp/active-build-plan.json
active-build -i /tmp/active-build-plan.json -w /home/zepp/workspace/mod
```

执行前必须先展示并确认 BuildPlan。
执行前必须先确认当前是否具备完全访问权限；若权限不足，先停止执行并告知用户切换权限以及当前权限下的失败风险。

示例固件 BuildPlan：

```json
{
  "action": "build",
  "family": "mhs003s",
  "project": "<project>",
  "mode": "sensorhub-ota",
  "threads": "8",
  "reload_defconfig": false,
  "version": null,
  "version_explicit": false,
  "build_type": null,
  "main_build_type": null,
  "sensorhub_build_type": null,
  "use_current_config": true,
  "workspace": "/home/zepp/workspace/mod",
  "log": false
}
```

示例 bstyle BuildPlan：

```json
{
  "action": "bstylenc",
  "family": "mhs003",
  "project": "cologne",
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

## 交互式完整编译

仅在终端交互可接受时使用：

```sh
active-build
```

交互式流程会选择 family、project、构建入口、mode、可选 BUILD_TYPE、日志开关与线程数。
其中 `bstyle 编译` 分支会在 family 和 project 已选择后，只要求输入 `.style` 路径，再自动生成默认 `.bstyle` 输出路径并询问是否修改。

## 独立 bstyle 编译

`bstyle` 是独立指令，不会串入任何固件编译流程。agent 默认应生成上面的 `action: "bstylenc"` JSON 并执行 `active-build -i <plan-file>`；下面短参形式主要用于用户手动执行、调试或 dry-run 验证：

```sh
active-build bstyle -i ui/Sports/prototype/style/466x466-mdpi/Foo.style
active-build bstyle -f mhs003 -p cologne -i Foo.style -o Foo.bstyle
active-build bstyle -i Foo.style -w /home/zepp/workspace/mod --dry-run
```

`active-build bstylenc` 是兼容别名，文档和人工使用场景优先展示 `active-build bstyle`。

参数或 JSON 模式不进入交互；推导失败、文件不存在或候选不唯一时直接报错。

底层工具从 workspace 推导：

```text
build/cmd/linux64/bstylenc
build/cmd/linux32/bstylenc
```

宽高和 ppi ratio 从主 defconfig 推导：

```text
configs/<family>/<family>_<project>_defconfig
```

优先读取 `STORYBOARD_DISPLAY_WIDTH`、`STORYBOARD_DISPLAY_HEIGHT`、`HM_FONT_DENSTIY`；缺失时分别回退到 `AMOLED_PANEL_WIDTH`、`AMOLED_PANEL_HEIGHT`、`HM_DISPLAY_DENSTIY`。

JSON 字段：

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

执行方式：

```sh
active-build -i /tmp/active-build-bstyle-plan.json
active-build -i /tmp/active-build-bstyle-plan.json -w /home/zepp/workspace/mod
```

## 完整编译

当用户明确给出目标时，可使用短参：

```sh
active-build -f mhs003 -p cologne -m debug -j 8
active-build -f mhs003s -p atlas -m sensorhub-ota -j 8 -b release
active-build -F mhs003s -P atlas -M sensorhub -J 8 -D -V 6.1.23.4
```

支持的 mode：

```text
fw, firmware, app, ota, sensorhub, sensorhub-fw, sensorhub-firmware,
sensorhub-ota, debug, release, sim
```

支持的 build type：

```text
debug, inspect, release_log, release
```

说明：

- `debug`、`release` 是快捷完整编译入口，CLI 内部会规范化为实际构建流程。
- 对用户展示和最终确认 BuildPlan 时，不应把 `debug` 或 `release` 当作最终 `mode` 配置，应直接使用 `sensorhub-ota` 等实际模式。
- `-b/-a/-u` 都会保留在 BuildPlan 语义中，但实际拼接 `make` 命令时，只有 `release` 会写入 `BUILD_TYPE=release`；`debug`、`inspect`、`release_log` 不会写入运行命令。
- `-v/-V` 继续映射到 BuildPlan 的 `version` 字段。旧工程中它表示显式 `.config` 版本覆写；新工程中它表示 OTA 阶段的 `BUILD_FW_VER`。新工程只接受两段 `c.d` 或四段 `a.b.c.d` 数字版本。

完整编译过程中，CLI 可能会：

- 检查 `.repo/manifest.xml`
- 在需要时询问是否继续
- 在 `reload_defconfig=true` 时执行 `distclean` 与 `clean`
- 旧工程（未检测到 `build/build_rules/fw_version.mk`）按原逻辑覆写 `BOARD_FIRMWARE_VERSION` 并执行对应 `silentoldconfig`
- 新工程（检测到 `build/build_rules/fw_version.mk`）不 patch `.config`，只在 `make ota` 阶段下发版本变量
- 新工程默认 `999.999` 或显式两段版本生成 `FW_VER_STRATEGY=os_global BUILD_FW_VER=<version>`，显式四段版本只生成 `BUILD_FW_VER=<version>`
- 新工程的 defconfig、普通 firmware、sensorhub 和 `silentoldconfig` 命令都不追加版本变量
- 在 `build/out_hub/` 下构建 sensorhub
- 回拷 sensorhub 产物到产品目录
- 执行 firmware 或 ota 阶段

## 基于当前配置继续编译

仅在 `build/.config` 已存在时使用：

```sh
active-build -c app -j 8
active-build -c fw -j 8
active-build -c fw -v 10.0.0 -j 8
active-build -c ota -v 23.4 -j 8
active-build -c ota -v 6.1.23.4 -j 8
active-build -c sensorhub -j 8
active-build -c sensorhub-ota -u release -j 8
```

说明：

- `app` 会被规范化为 firmware / fw。
- 当前配置模式下，CLI 会从 `build/.config` 中推断 family 与 project。
- 若工作区已有上次编译信息且用户确认延用，BuildPlan 中应使用 `reload_defconfig=false`。
- 当前配置模式下，旧工程默认跳过版本号覆写；显式传入 `-v/-V` 或 BuildPlan 中设置 `version_explicit=true` 时仍会按旧规则覆写一次。新工程的默认或显式版本只影响 `make ota` 阶段参数。
- 未显式传入 `-j` 时，CLI 优先复用 `build/.active-build-state.json` 中的 `threads`；旧 `.hmbuild_last_threads` 只作为迁移兼容读取，成功写入新状态后会被清理。

## BuildPlan 队列

CLI 一次只执行一个 BuildPlan。若需要串行多个构建计划或 bstyle 计划，先整体展示所有 BuildPlan 并确认，再顺序执行。队列中可以混合 `action=build` 和 `action=bstylenc`：

```sh
active-build -i /tmp/active-build-plan-1.json
active-build -i /tmp/active-build-bstyle-plan-2.json
active-build -i /tmp/active-build-plan-3.json
```

若前一个失败可能影响后续结果，应停止并向用户汇报，不要盲目继续。

## agent 输出要求

执行前展示：

```text
Running:
cd /path/to/workspace
active-build -i /tmp/active-build-plan.json
```

若当前不是完全访问权限，不要继续执行上面的编译命令；先向用户说明需要切换权限，以及当前权限下可能出现编译失败、沙箱拦截或结果不完整的风险。

执行时禁止实时监控、流式读取和中途判读。等待命令结束或中断后，再统一回溯并汇总结果。

执行后：

- 成功时，简洁说明 BuildPlan 关键信息与结果
- 失败时，返回命令、cwd、exit code 和关键错误输出
- 通过 Lark MCP 发送消息时，只发送构建信息摘要和结果，不发送文件路径、日志路径或长输出
