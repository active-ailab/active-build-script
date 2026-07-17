#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
import argparse
import curses
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from .active_common import (
    ActiveLogger,
    BACK,
    GREEN,
    RED,
    RESET,
    YELLOW,
    fail,
    find_available_families,
    find_project_families,
    find_main_defconfig,
    is_back,
    list_projects,
    load_build_state,
    locate_project_root,
    normalize_bool,
    normalize_optional_positive_float,
    normalize_optional_positive_int,
    prompt_family_project,
    prompt_choice,
    prompt_yes_no,
    quote_cmd,
    read_config_value,
    run_cmd,
)
from .active_tui import FieldType, MenuItem, MenuSection, TuiPage


HELP_TEXT = """\
用法:
  active-bstyle
  active-bstyle [-i input.style | -i <dir>] [-o output.bstyle | -o <dir>] [-f family] [-p project] [-w workspace]

核心参数:
  无参数运行时进入交互模式，依次选择 family、project、style 输入文件和输出文件。
  -i <file|dir>     style 输入文件或目录；为目录时批量处理目录下所有 .style 文件
  -o <file|dir>     bstyle 输出文件或目录；-i 为目录时 -o 也必须为目录，默认与 -i 同目录
  -f <family>       family / 芯片目录，例如 mhs003、mhs003s
  -p <project>      项目名称，例如 cologne、geneva、atlas
  -w <path>         指定 workspace 根目录或 build 目录
  --width <value>   直接指定 gen_styles.py 单文件模式 width
  --height <value>  直接指定 gen_styles.py 单文件模式 height
  --pixel-ratio <value>
                    直接指定 gen_styles.py 单文件模式 ppi-ratio
  -l                写入日志
  --dry-run         只打印最终 gen_styles.py 命令，不执行
  -h                显示帮助

推送:
  编译成功后会在交互终端中询问是否推送 .bstyle 文件到设备。
  通过 wlctl.sh fs push-bstyle 写入设备 SYSTEM/resources/styles/。
  非交互终端跳过推送确认；--dry-run 模式不触发推送询问。

推导规则:
  底层通过 workspace 下 build/scripts/gen_styles.py 生成，并在 build 目录下执行。
  -w/-h/-p 参数默认从 configs/<family>/<family>_<project>_defconfig 推导。
  当 -i 传入目录时，批量处理目录下所有 .style 文件，共享同一套 defconfig 参数。
"""


@dataclass
class BstylePlan:
    family: str = None
    project: str = None
    input: str = None
    output: str = None
    workspace: str = None
    width: str = None
    height: str = None
    pixel_ratio: str = None
    dry_run: bool = False
    log: bool = False


def print_help():
    print(HELP_TEXT)


def normalize_bstyle_plan(plan):
    plan.width = normalize_optional_positive_int(plan.width, "width")
    plan.height = normalize_optional_positive_int(plan.height, "height")
    plan.pixel_ratio = normalize_optional_positive_float(plan.pixel_ratio, "pixel_ratio")
    plan.dry_run = normalize_bool(plan.dry_run)
    plan.log = normalize_bool(plan.log)
    return plan


def build_arg_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-f", "-F", dest="family")
    parser.add_argument("-p", "-P", dest="project")
    parser.add_argument("-i", "-I", dest="input")
    parser.add_argument("-o", "-O", dest="output")
    parser.add_argument("-w", "-W", dest="workspace")
    parser.add_argument("--width", dest="width")
    parser.add_argument("--height", dest="height")
    parser.add_argument("--pixel-ratio", dest="pixel_ratio")
    parser.add_argument("-l", "-L", dest="log", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("-h", "-H", "--help", dest="help", action="store_true")
    return parser


def parse_args(argv):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.help:
        print_help()
        sys.exit(0)
    return normalize_bstyle_plan(
        BstylePlan(
            family=args.family,
            project=args.project,
            input=args.input,
            output=args.output,
            workspace=args.workspace,
            width=args.width,
            height=args.height,
            pixel_ratio=args.pixel_ratio,
            dry_run=args.dry_run,
            log=args.log,
        )
    )


def default_bstyle_output(input_path):
    return os.path.splitext(input_path)[0] + ".bstyle"


def prompt_style_input(start_dir, allow_back=False):
    while True:
        try:
            suffix = " [输入 0 返回上一级]" if allow_back else ""
            raw = input(f"{YELLOW}请输入 style 输入文件路径{suffix}: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if allow_back and raw in {"0", "b", "back"}:
            return BACK
        if not raw:
            print(f"{RED}style 输入文件路径不能为空{RESET}")
            continue
        input_path = raw if os.path.isabs(raw) else os.path.abspath(os.path.join(start_dir, raw))
        if not input_path.endswith(".style"):
            print(f"{RED}输入文件必须是 .style: {input_path}{RESET}")
            continue
        if not os.path.isfile(input_path):
            print(f"{RED}style 输入文件不存在: {input_path}{RESET}")
            continue
        return input_path


def prompt_output_path(start_dir, allow_back=False):
    while True:
        try:
            suffix = " [输入 0 返回上一级]" if allow_back else ""
            raw = input(f"{YELLOW}请输入 bstyle 输出文件路径{suffix}: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if allow_back and raw in {"0", "b", "back"}:
            return BACK
        if raw:
            return raw if os.path.isabs(raw) else os.path.abspath(os.path.join(start_dir, raw))
        print(f"{RED}bstyle 输出文件路径不能为空{RESET}")


def collect_tui_bstyle_plan(start_dir, project_root, configs_dir):
    """menuconfig-style TUI that faithfully mirrors collect_interactive_bstyle_plan.

    Dependency relationships (1:1 with existing linear flow):
      - family  → refreshes project (cascading options)
      - input   → on_change auto-derives default output via default_bstyle_output()
      - modify_output  → custom_output visibility toggle
    """
    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")

    initial_family = families[0]
    projects = list_projects(project_root, configs_dir, initial_family)
    if not projects:
        fail(f"{initial_family} 下未找到可用于 bstyle 的 defconfig")
    initial_project = projects[0]

    page = TuiPage(
        title="active-bstyle",
        sections=[
            MenuSection("Target", items=[
                MenuItem("Chip Family", "family", FieldType.CHOICE,
                         value=None, options=families),
                MenuItem("Project", "project", FieldType.CHOICE,
                         value=None,
                         options_provider=lambda s: list_projects(project_root, configs_dir, s.get("family", "")),
                         refreshes=["family"],
                         enabled_when=lambda s: bool(s.get("family"))),
            ]),
            MenuSection("Input / Output", items=[
                MenuItem("Style Input", "input", FieldType.TEXT,
                         value="",
                         on_change=_on_input_change,
                         enabled_when=lambda s: bool(s.get("project"))),
                MenuItem("Modify output path", "modify_output", FieldType.TOGGLE,
                         value=True),
                MenuItem("Custom Output", "custom_output", FieldType.TEXT,
                         value="",
                         enabled_when=lambda s: s.get("modify_output", True),
                         visible_when=lambda s: s.get("modify_output", True)),
            ]),
        ],
        actions=[
            MenuItem("Start", "_start", FieldType.ACTION),
            MenuItem("Exit", "_exit", FieldType.ACTION),
        ],
    )

    try:
        result = curses.wrapper(page.run)
    except curses.error:
        print(f"{YELLOW}TUI 初始化失败，回退到文本交互模式{RESET}")
        return collect_interactive_bstyle_plan(start_dir, project_root, configs_dir)

    if result is None or result.get("_action") != "_start":
        sys.exit(0)

    family = result.get("family", initial_family)
    project_name = result.get("project", initial_project)

    input_raw = (result.get("input") or "").strip()
    if not input_raw:
        fail("style 输入文件路径不能为空")

    input_path = input_raw if os.path.isabs(input_raw) else os.path.abspath(os.path.join(start_dir, input_raw))
    if not input_path.endswith(".style"):
        fail(f"输入文件必须是 .style: {input_path}")
    if not os.path.isfile(input_path):
        fail(f"style 输入文件不存在: {input_path}")

    if result.get("modify_output", True):
        output_raw = (result.get("custom_output") or "").strip()
        if not output_raw:
            fail("bstyle 输出文件路径不能为空")
        output_path = output_raw if os.path.isabs(output_raw) else os.path.abspath(os.path.join(start_dir, output_raw))
    else:
        output_path = default_bstyle_output(input_path)

    return normalize_bstyle_plan(BstylePlan(
        family=family,
        project=project_name,
        input=input_path,
        output=output_path,
        workspace=project_root,
    ))


def _on_input_change(state, page):
    """Auto-derive default output path when style input changes."""
    input_val = (state.get("input") or "").strip()
    if input_val and input_val.endswith(".style"):
        derived = os.path.splitext(input_val)[0] + ".bstyle"
        if not state.get("modify_output", True):
            # Find the custom_output item and update its value
            for section in page.sections:
                for item in section.items:
                    if item.key == "custom_output":
                        item.value = derived
                        return


def collect_interactive_bstyle_plan(start_dir, project_root, configs_dir):
    while True:
        family, project = prompt_family_project(
            project_root,
            configs_dir,
            "未找到可用于 bstyle 的 defconfig",
        )

        step = 0
        input_path = None
        output_path = None
        while True:
            if step == 0:
                input_path = prompt_style_input(start_dir, allow_back=True)
                if is_back(input_path):
                    break
                output_path = default_bstyle_output(input_path)
                print(f"{YELLOW}输出文件默认: {output_path}{RESET}")
                step = 1
                continue
            if step == 1:
                modify_output = prompt_yes_no("是否修改输出路径", True, allow_back=True)
                if is_back(modify_output):
                    step = 0
                    continue
                if modify_output:
                    step = 2
                    continue
                return normalize_bstyle_plan(
                    BstylePlan(
                        family=family,
                        project=project,
                        input=input_path,
                        output=output_path,
                        workspace=project_root,
                    )
                )
            if step == 2:
                output_path = prompt_output_path(start_dir, allow_back=True)
                if is_back(output_path):
                    step = 1
                    continue
                return normalize_bstyle_plan(
                    BstylePlan(
                        family=family,
                        project=project,
                        input=input_path,
                        output=output_path,
                        workspace=project_root,
                    )
                )


def infer_bstyle_target(plan, project_root, configs_dir, build_dir):
    state = load_build_state(build_dir)
    config_path = os.path.join(build_dir, ".config")
    config_family = read_config_value(config_path, "HMI_BUILD_BOARD")
    config_project = read_config_value(config_path, "HMI_PRODUCT_CUSTOMIZE_DIR")

    if not plan.project:
        plan.project = state.get("project") or config_project
    if not plan.project:
        fail("无法推导 project，请使用 -p <project>")

    if not plan.family:
        state_family = state.get("family") if state.get("project") == plan.project else None
        current_family = config_family if config_project == plan.project else None
        plan.family = state_family or current_family

    if not plan.family:
        families = find_project_families(project_root, configs_dir, plan.project)
        if len(families) == 1:
            plan.family = families[0]
        elif len(families) > 1:
            fail(f"project {plan.project} 匹配多个 family: {', '.join(families)}，请使用 -f <family>")
        else:
            fail(f"无法根据 project 推导 family: {plan.project}")

    defconfig = find_main_defconfig(configs_dir, plan.family, plan.project)
    if defconfig is None:
        expected = os.path.join(configs_dir, plan.family, f"{plan.family}_{plan.project}_defconfig")
        fail(f"主 defconfig 不存在: {expected}")
    return defconfig


def infer_single_style_input(start_dir):
    styles = [
        name
        for name in sorted(os.listdir(start_dir))
        if os.path.isfile(os.path.join(start_dir, name)) and name.endswith(".style")
    ]
    if len(styles) == 1:
        return os.path.join(start_dir, styles[0])
    if len(styles) > 1:
        fail(f"当前目录存在多个 .style 文件，请使用 -i 指定: {', '.join(styles)}")
    fail("未指定 -i，且当前目录没有可自动推导的 .style 文件")


def resolve_workspace_path(path, project_root):
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(project_root, path))


def ensure_path_under_workspace(path, project_root, label):
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(project_root)
    try:
        in_workspace = os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:
        in_workspace = False
    if not in_workspace:
        fail(f"{label}必须位于当前 workspace 内: {path}")


def resolve_bstyle_input_output(plan, start_dir, project_root):
    """Resolve style input(s) and bstyle output(s).

    When plan.input is a directory, collects all .style files inside it
    and maps each to a same-named .bstyle in plan.output (defaults to the
    same directory).  When plan.input is a single file, behaviour is
    unchanged.

    Returns (inputs, outputs) where both are lists of absolute paths.
    """
    # -- resolve input path --------------------------------------------------
    if not plan.input:
        plan.input = infer_single_style_input(start_dir)
    else:
        plan.input = resolve_workspace_path(plan.input, project_root)

    # -- batch mode: input is a directory ------------------------------------
    if os.path.isdir(plan.input):
        style_files = sorted(
            os.path.join(plan.input, f)
            for f in os.listdir(plan.input)
            if f.endswith(".style") and os.path.isfile(os.path.join(plan.input, f))
        )
        if not style_files:
            fail(f"目录中未找到 .style 文件: {plan.input}")

        # output directory: default to input directory
        if plan.output:
            plan.output = resolve_workspace_path(plan.output, project_root)
        else:
            plan.output = plan.input

        if not os.path.isdir(plan.output):
            fail(f"当 -i 为目录时，-o 也必须为目录: {plan.output}")
        ensure_path_under_workspace(plan.output, project_root, "bstyle 输出目录")
        os.makedirs(plan.output, exist_ok=True)

        outputs = [
            os.path.join(plan.output, os.path.splitext(os.path.basename(f))[0] + ".bstyle")
            for f in style_files
        ]
        return style_files, outputs

    # -- single file mode ----------------------------------------------------
    if not plan.input.endswith(".style"):
        fail(f"输入文件必须是 .style: {plan.input}")
    ensure_path_under_workspace(plan.input, project_root, "style 输入文件")
    if not os.path.isfile(plan.input):
        fail(f"style 输入文件不存在: {plan.input}")

    if not plan.output:
        plan.output = default_bstyle_output(plan.input)
    else:
        plan.output = resolve_workspace_path(plan.output, project_root)
    ensure_path_under_workspace(plan.output, project_root, "bstyle 输出文件")

    return [plan.input], [plan.output]


def parse_bstyle_params_from_defconfig(defconfig_path, plan):
    if plan.width is None:
        plan.width = read_config_value(defconfig_path, "STORYBOARD_DISPLAY_WIDTH") or read_config_value(
            defconfig_path, "AMOLED_PANEL_WIDTH"
        )
    if plan.height is None:
        plan.height = read_config_value(defconfig_path, "STORYBOARD_DISPLAY_HEIGHT") or read_config_value(
            defconfig_path, "AMOLED_PANEL_HEIGHT"
        )
    if plan.pixel_ratio is None:
        plan.pixel_ratio = read_config_value(defconfig_path, "HM_FONT_DENSTIY") or read_config_value(
            defconfig_path, "HM_DISPLAY_DENSTIY"
        )
    if plan.width is None:
        fail(f"无法从 defconfig 推导 STORYBOARD_DISPLAY_WIDTH/AMOLED_PANEL_WIDTH: {defconfig_path}")
    if plan.height is None:
        fail(f"无法从 defconfig 推导 STORYBOARD_DISPLAY_HEIGHT/AMOLED_PANEL_HEIGHT: {defconfig_path}")
    if plan.pixel_ratio is None:
        fail(f"无法从 defconfig 推导 HM_FONT_DENSTIY/HM_DISPLAY_DENSTIY: {defconfig_path}")
    normalize_bstyle_plan(plan)


def resolve_gen_styles_script(project_root):
    script_path = os.path.join(project_root, "build", "scripts", "gen_styles.py")
    if os.path.isfile(script_path):
        return script_path
    fail(f"未找到 gen_styles.py: {script_path}")


def make_gen_styles_cmd(build_dir, script_path, input_path, output_path, width, height, pixel_ratio):
    relative_script = os.path.relpath(script_path, build_dir)
    return "cd {} && {}".format(
        quote_cmd([build_dir]),
        quote_cmd(
            [
                sys.executable,
                relative_script,
                os.path.abspath(input_path),
                os.path.abspath(output_path),
                width,
                height,
                pixel_ratio,
            ]
        ),
    )


def run_gen_styles_cmd(cmd, output_path, logger=None):
    print(f"\n{YELLOW}>>> 执行: {cmd}{RESET}")
    if logger:
        logger.line(f">>> 执行: {cmd}")
    start_step = time.time()
    before_output = None
    if os.path.isfile(output_path):
        stat = os.stat(output_path)
        before_output = (stat.st_mtime_ns, stat.st_size)
    process = subprocess.Popen(
        cmd,
        shell=True,
        cwd="/",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        if process.stdout:
            for line in process.stdout:
                print(line, end="")
                if logger and logger.enabled:
                    logger.write(line)
        returncode = process.wait()
    finally:
        if process.stdout:
            process.stdout.close()
    end_step = time.time()

    if returncode != 0:
        output_changed = False
        if os.path.isfile(output_path):
            stat = os.stat(output_path)
            after_output = (stat.st_mtime_ns, stat.st_size)
            output_changed = before_output is None or after_output != before_output
        if output_changed:
            message = (
                f"gen_styles.py 返回码 {returncode}，但输出文件已生成，"
                f"按兼容逻辑继续: {output_path}"
            )
            print(f"{YELLOW}{message}{RESET}")
            if logger:
                logger.line(message)
            return
        print(f"\n{RED}命令执行失败: {cmd}{RESET}")
        print(f"{YELLOW}耗时: {end_step - start_step:.2f} 秒{RESET}")
        if logger:
            logger.line(f"命令执行失败: {cmd}")
            logger.line(f"耗时: {end_step - start_step:.2f} 秒")
        sys.exit(returncode)

    print(f"{GREEN}完成: {cmd} (耗时 {end_step - start_step:.2f} 秒){RESET}")
    if logger:
        logger.line(f"完成: {cmd} (耗时 {end_step - start_step:.2f} 秒)")


def resolve_wlctl_path():
    """Find wlctl.sh by walking up from this module's location.

    Directory layout::

        active-lab/
          active-build-script/cli/src/active_cli/active_bstyle_cli.py  ← __file__
          device-skills/watchlink-v3/scripts/linux/wlctl.sh
    """
    module_dir = os.path.dirname(os.path.realpath(__file__))
    # active_cli/ → src/ → cli/ → active-build-script/ → active-lab/
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(module_dir))))
    wlctl = os.path.join(repo_root, "device-skills", "watchlink-v3", "scripts", "linux", "wlctl.sh")
    return wlctl if os.path.isfile(wlctl) else None


def maybe_prompt_post_bstyle_push(outputs, logger=None):
    """After successful bstyle compilation, ask whether to push .bstyle files to device.

    Uses wlctl.sh fs push-bstyle to write to SYSTEM/resources/styles/.
    Skips when:
      - wlctl.sh is not found
      - stdin is not a TTY (scripted / CI usage)
      - user declines the prompt (default: No)
    """
    wlctl = resolve_wlctl_path()
    if not wlctl:
        print(f"{YELLOW}未找到 wlctl.sh，跳过推送确认。{RESET}")
        if logger:
            logger.line("skip post-bstyle push: wlctl.sh not found")
        return

    if not sys.stdin.isatty():
        print(f"{YELLOW}非交互终端，跳过推送确认。{RESET}")
        if logger:
            logger.line("skip post-bstyle push: non-interactive stdin")
        return

    if len(outputs) == 1:
        from_path = outputs[0]
        display = f"bstyle 文件: {from_path}"
    else:
        from_path = os.path.dirname(outputs[0])
        display = f"bstyle 目录: {from_path} ({len(outputs)} 个文件)"

    print(f"\n{YELLOW}本次编译产物:{RESET}")
    print(display)
    print(f"{YELLOW}即将推送至设备 SYSTEM/resources/styles/{RESET}")

    if not prompt_yes_no("是否推送编译产物到设备？", default=False):
        print(f"{YELLOW}已跳过推送。{RESET}")
        if logger:
            logger.line("skip post-bstyle push: user declined")
        return

    cmd = quote_cmd([wlctl, "fs", "push-bstyle", "--from-path", from_path])
    run_cmd(cmd, logger)


def run_bstyle_plan(plan, start_dir, project_root, configs_dir, build_dir):
    plan = normalize_bstyle_plan(plan)
    inputs, outputs = resolve_bstyle_input_output(plan, start_dir, project_root)
    needs_defconfig = plan.width is None or plan.height is None or plan.pixel_ratio is None
    defconfig = None
    if needs_defconfig:
        defconfig = infer_bstyle_target(plan, project_root, configs_dir, build_dir)
        parse_bstyle_params_from_defconfig(defconfig, plan)
    script_path = resolve_gen_styles_script(project_root)
    logger = ActiveLogger(build_dir, "active-bstyle", plan.family, plan.project, plan.log)
    success = False
    is_batch = len(inputs) > 1

    try:
        print(f"\n{YELLOW}脚本启动目录: {start_dir}{RESET}")
        print(f"{YELLOW}项目根目录: {project_root}{RESET}")
        if defconfig:
            print(f"{YELLOW}主 defconfig: {defconfig}{RESET}")
        if is_batch:
            print(f"{YELLOW}批量模式: 共 {len(inputs)} 个 .style 文件{RESET}")
        print(f"{YELLOW}BstylePlan: {json.dumps(asdict(plan), ensure_ascii=False)}{RESET}")
        if logger.enabled:
            logger.line(f"start: {datetime.now().isoformat(timespec='seconds')}")
            logger.line(f"BstylePlan: {json.dumps(asdict(plan), ensure_ascii=False)}")

        for idx, (input_path, output_path) in enumerate(zip(inputs, outputs), 1):
            if is_batch:
                print(f"\n{YELLOW}[{idx}/{len(inputs)}] {os.path.basename(input_path)} → {os.path.basename(output_path)}{RESET}")

            command = make_gen_styles_cmd(
                build_dir,
                script_path,
                input_path,
                output_path,
                plan.width,
                plan.height,
                plan.pixel_ratio,
            )

            if plan.dry_run:
                print(f"{YELLOW}dry-run 命令: {command}{RESET}")
                if logger.enabled:
                    logger.line(f"dry-run 命令 [{idx}]: {command}")
            else:
                run_gen_styles_cmd(command, output_path, logger)

        success = True
        if not plan.dry_run:
            maybe_prompt_post_bstyle_push(outputs, logger)
    finally:
        if logger.enabled:
            status = "finish" if success else "failed"
            logger.line(f"{status}: {datetime.now().isoformat(timespec='seconds')}")
            print(f"{GREEN}日志文件: {logger.latest_path}{RESET}")
            print(f"{GREEN}历史日志: {logger.history_path}{RESET}")
        logger.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(arg in {"-h", "-H", "--help"} for arg in argv):
        print_help()
        sys.exit(0)
    if len(argv) == 0:
        start_dir, project_root, configs_dir, build_dir = locate_project_root(os.getcwd())
        if sys.stdout.isatty():
            plan = collect_tui_bstyle_plan(start_dir, project_root, configs_dir)
        else:
            plan = collect_interactive_bstyle_plan(start_dir, project_root, configs_dir)
    else:
        plan = parse_args(argv)
        start_dir, project_root, configs_dir, build_dir = locate_project_root(
            os.getcwd(), workspace=plan.workspace
        )
    run_bstyle_plan(plan, start_dir, project_root, configs_dir, build_dir)


if __name__ == "__main__":
    main()
