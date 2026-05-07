# active-build

## 能力

| 能力 | 说明 |
| --- | --- |
| 交互式完整编译 | 无参数启动后选择芯片目录、项目、编译模式和线程数。 |
| 参数式完整编译 | 支持 `active-build <芯片目录> <项目名称> <release|debug|sim> [线程数]`。 |
| 当前配置编译 | 支持 `active-build -c app [线程数]` 和 `active-build -c ota [线程数]`。 |
| 编译前检查 | 检查 `.repo/manifest.xml` 与 `huamiOS.xml` 或目标项目 XML。 |
| defconfig 保护 | 完整编译时临时修改 `BOARD_FIRMWARE_VERSION="10.0.0"`，结束或异常后恢复。 |
| sensorhub 回退 | 优先使用签名产物，缺失时可确认回退到未签名产物。 |

## 工程结构

```text
active-build/
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

默认安装 CLI 和 Skill：

```sh
sh install.sh
```

只安装 CLI：

```sh
sh install.sh cli
```

只安装或刷新 Skill，并同步刷新 CLI：

```sh
sh install.sh skills
```

非交互安装到 Codex 项目级 skills：

```sh
SKILL_PROJECT_ROOT="/home/zepp/workspace/col_win" SKILL_PLUGIN=codex sh install.sh
```

非交互安装到 GitHub Copilot 项目级 skills：

```sh
SKILL_PROJECT_ROOT="/home/zepp/workspace/col_win" SKILL_PLUGIN=github-copilot sh install.sh
```

如果 `~/.local/bin` 不在 `PATH` 中，请加入 shell 配置：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## 用法

```sh
active-build
active-build mhs003 cologne debug
active-build mhs003 geneva release 16
active-build mhs003s atlas debug 8
active-build -c app
active-build -c ota
active-build -c ota 12
active-build --current app 8
active-build --help
```

注意：

- `-c app` / `-c ota` 依赖当前工程 `build/.config`。
- 完整编译会执行 `make distclean`、`make clean` 和后续 `make` 编译指令。
- 完整编译可能出现 XML、defconfig 修改、未签名 sensorhub 回退等交互确认。
- 实际编译命令在项目根目录的 `build/` 下执行。

## 验证

```sh
cd /home/zepp/workspace/active-lab/active-build/cli
python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m active_build --help
```
