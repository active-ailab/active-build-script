#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
import argparse
import curses
import json
import os
import platform
import sys
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
  active-bstyle [-i input.style] [-o output.bstyle] [-f family] [-p project] [-w workspace]

核心参数:
  无参数运行时进入交互模式，依次选择 family、project、style 输入文件和输出文件。
  -i <file>         style 输入文件；不传时仅在当前目录存在唯一 .style 文件时自动推导
  -o <file>         bstyle 输出文件；不传时自动生成同目录同名 .bstyle
  -f <family>       family / 芯片目录，例如 mhs003、mhs003s
  -p <project>      项目名称，例如 cologne、geneva、atlas
  -w <path>         指定 workspace 根目录或 build 目录
  --width <value>   直接指定底层 bstylenc -w
  --height <value>  直接指定底层 bstylenc -h
  --pixel-ratio <value>
                    直接指定底层 bstylenc -p
  -l                写入日志
  --dry-run         只打印最终 bstylenc 命令，不执行
  -h                显示帮助

推导规则:
  底层工具从 workspace 下 build/cmd/linux64/bstylenc 或 build/cmd/linux32/bstylenc 查找。
  -w/-h/-p 参数默认从 configs/<family>/<family>_<project>_defconfig 推导。
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
    if not plan.input:
        plan.input = infer_single_style_input(start_dir)
    else:
        plan.input = resolve_workspace_path(plan.input, project_root)
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


def resolve_bstylenc_tool(project_root):
    arch = platform.architecture()[0]
    preferred = "linux64" if arch == "64bit" else "linux32"
    fallback = "linux32" if preferred == "linux64" else "linux64"
    checked = [
        os.path.join(project_root, "build", "cmd", preferred, "bstylenc"),
        os.path.join(project_root, "build", "cmd", fallback, "bstylenc"),
    ]
    for path in checked:
        if os.path.isfile(path):
            return path
    fail("未找到 bstylenc，已检查: " + ", ".join(checked))


def make_bstyle_cmd(tool_path, plan):
    return quote_cmd(
        [
            tool_path,
            "-i",
            plan.input,
            "-o",
            plan.output,
            "-w",
            plan.width,
            "-h",
            plan.height,
            "-p",
            plan.pixel_ratio,
        ]
    )


def run_bstyle_plan(plan, start_dir, project_root, configs_dir, build_dir):
    plan = normalize_bstyle_plan(plan)
    resolve_bstyle_input_output(plan, start_dir, project_root)
    needs_defconfig = plan.width is None or plan.height is None or plan.pixel_ratio is None
    defconfig = None
    if needs_defconfig:
        defconfig = infer_bstyle_target(plan, project_root, configs_dir, build_dir)
        parse_bstyle_params_from_defconfig(defconfig, plan)
    tool_path = resolve_bstylenc_tool(project_root)
    command = make_bstyle_cmd(tool_path, plan)
    logger = ActiveLogger(build_dir, "active-bstyle", plan.family, plan.project, plan.log)
    success = False

    try:
        print(f"\n{YELLOW}脚本启动目录: {start_dir}{RESET}")
        print(f"{YELLOW}项目根目录: {project_root}{RESET}")
        if defconfig:
            print(f"{YELLOW}主 defconfig: {defconfig}{RESET}")
        print(f"{YELLOW}BstylePlan: {json.dumps(asdict(plan), ensure_ascii=False)}{RESET}")
        if logger.enabled:
            logger.line(f"start: {datetime.now().isoformat(timespec='seconds')}")
            logger.line(f"BstylePlan: {json.dumps(asdict(plan), ensure_ascii=False)}")
        if plan.dry_run:
            print(f"{YELLOW}dry-run 命令: {command}{RESET}")
            if logger.enabled:
                logger.line(f"dry-run 命令: {command}")
        else:
            run_cmd(command, logger)
        success = True
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
