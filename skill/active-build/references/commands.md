# active-build 命令参考

`active-build` 是唯一受支持的执行入口。agent 可以生成 BuildPlan，但不能绕过 CLI 自己重写编译逻辑。

## 帮助

查看帮助：

```sh
active-build --help
```

## agent 优先执行方式

agent 发起编译时，优先使用 BuildPlan：

```sh
active-build -i /tmp/active-build-plan.json
active-build -i /tmp/active-build-plan.json -w /home/zepp/workspace/mod
```

执行前必须先展示并确认 BuildPlan。
执行前必须先确认当前是否具备完全访问权限；若权限不足，先停止执行并告知用户切换权限以及当前权限下的失败风险。

示例 BuildPlan：

```json
{
  "action": "build",
  "family": "mhs003s",
  "project": "<project>",
  "mode": "sensorhub-ota",
  "threads": "8",
  "reload_defconfig": false,
  "version": "10.0.0",
  "version_explicit": false,
  "build_type": null,
  "main_build_type": null,
  "sensorhub_build_type": null,
  "use_current_config": true,
  "workspace": "/home/zepp/workspace/mod",
  "log": false
}
```

## 交互式完整编译

仅在终端交互可接受时使用：

```sh
active-build
```

交互式流程会选择 family、project、构建入口、mode、可选 BUILD_TYPE、日志开关与线程数。

## 完整编译

当用户明确给出目标时，可使用短参：

```sh
active-build -f mhs003 -p cologne -m debug -j 8
active-build -f mhs003s -p atlas -m sensorhub-ota -j 8 -b release
active-build -F mhs003s -P atlas -M sensorhub -J 8 -D -V 10.0.0
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
- `-v/-V` 表示显式版本覆写；未传入时，默认版本值不会自动强制覆写当前配置。

完整编译过程中，CLI 可能会：

- 检查 `.repo/manifest.xml`
- 在需要时询问是否继续
- 在 `reload_defconfig=true` 时执行 `distclean` 与 `clean`
- main/fw/ota 阶段覆写 `build/.config` 中的 `BOARD_FIRMWARE_VERSION`，随后执行 `make silentoldconfig`
- 纯 `sensorhub` 模式显式带 `-v` 时覆写 `build/out_hub/.config`，随后执行 `make silentoldconfig APPDIR=out_hub`
- `sensorhub-fw`、`sensorhub-ota` 组合模式下跳过 sensorhub 的 `out_hub` 版本覆写，只在后续 main/fw/ota 阶段覆写一次 `build/.config`
- 在 `build/out_hub/` 下构建 sensorhub
- 回拷 sensorhub 产物到产品目录
- 执行 firmware 或 ota 阶段

## 基于当前配置继续编译

仅在 `build/.config` 已存在时使用：

```sh
active-build -c app -j 8
active-build -c fw -j 8
active-build -c fw -v 10.0.0 -j 8
active-build -c ota -j 8
active-build -c sensorhub -j 8
active-build -c sensorhub-ota -u release -j 8
```

说明：

- `app` 会被规范化为 firmware / fw。
- 当前配置模式下，CLI 会从 `build/.config` 中推断 family 与 project。
- 若工作区已有上次编译信息且用户确认延用，BuildPlan 中应使用 `reload_defconfig=false`。
- 当前配置模式默认跳过版本号覆写；显式传入 `-v/-V` 或 BuildPlan 中设置 `version_explicit=true` 时，仍会按对应阶段覆写一次。
- 未显式传入 `-j` 时，CLI 优先复用 `build/.active-build-state.json` 中的 `threads`；旧 `.hmbuild_last_threads` 只作为迁移兼容读取，成功写入新状态后会被清理。

## BuildPlan 队列

CLI 一次只执行一个 BuildPlan。若需要串行多个构建计划，先整体确认，再顺序执行：

```sh
active-build -i /tmp/active-build-plan-1.json
active-build -i /tmp/active-build-plan-2.json
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
