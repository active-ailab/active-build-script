#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
"""Build UI resources using the current workspace configuration."""

import argparse
import curses
import hashlib
import json
import os
import shutil
import sys
import time
import zlib
import zipfile
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
    is_back,
    list_projects,
    locate_project_root,
    normalize_bool,
    prompt_choice,
    prompt_family_project,
    prompt_yes_no,
    quote_cmd,
    read_config_value,
    run_cmd,
)
from .active_tui import FieldType, MenuItem, MenuSection, TuiPage

UI_MODES = ("gapp", "csv", "all")


@dataclass
class UiPlan:
    family: str = None
    project: str = None
    mode: str = "all"
    workspace: str = None
    dry_run: bool = False
    log: bool = False


class UiLogger(ActiveLogger):
    def __init__(self, build_dir, family, project, enabled):
        super().__init__(build_dir, "active-ui", family or "config", project or "config", enabled)


HELP_TEXT = """\
用法:
  active-ui
  active-ui [--gapp|--csv|--all] [-f family] [-p project] [-w workspace]
  active-ui -i <plan.json>

参数:
  --gapp             生成 Application.bin 和 Application.crc
  --csv              生成翻译资源 locale.zip
  --all              生成全部 UI 资源；未指定模式时默认 all
  -f <family>        指定 family；必须和 -p 一起使用
  -p <project>       指定 project；必须和 -f 一起使用
  -w <path>          指定 workspace 根目录或 build 目录
  -i <file>          从 UI BuildPlan JSON 执行
  -l                写入构建日志
  --dry-run          只打印命令，不修改配置或生成产物
  -h                显示帮助

无参数时进入交互模式，依次选择 family、project 和 gapp/csv/all。
"""


def build_arg_parser():
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--gapp", action="store_true")
    group.add_argument("--csv", action="store_true")
    group.add_argument("--all", action="store_true")
    parser.add_argument("-f", "-F", dest="family")
    parser.add_argument("-p", "-P", dest="project")
    parser.add_argument("-w", "-W", dest="workspace")
    parser.add_argument("-i", "-I", dest="plan_file")
    parser.add_argument("-l", "-L", dest="log", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("-h", "-H", "--help", dest="help", action="store_true")
    return parser


def normalize_ui_plan(plan):
    if plan.mode not in UI_MODES:
        fail(f"无效 UI 模式: {plan.mode}，支持: gapp, csv, all")
    if bool(plan.family) != bool(plan.project):
        fail("-f <family> 和 -p <project> 必须同时传入")
    plan.dry_run = normalize_bool(plan.dry_run)
    plan.log = normalize_bool(plan.log)
    return plan


def plan_from_json(path, cwd):
    path = path if os.path.isabs(path) else os.path.abspath(os.path.join(cwd, path))
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"读取 UI BuildPlan 失败: {error}")
    if not isinstance(data, dict):
        fail("UI BuildPlan 顶层必须是对象")
    unknown = sorted(set(data) - set(UiPlan.__dataclass_fields__))
    if unknown:
        fail(f"UI BuildPlan 包含未知字段: {', '.join(unknown)}")
    return normalize_ui_plan(UiPlan(**data))


def parse_args(argv, cwd):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.help:
        print(HELP_TEXT)
        sys.exit(0)
    if args.plan_file:
        if any((args.gapp, args.csv, args.all, args.family, args.project, args.workspace, args.log, args.dry_run)):
            fail("-i <plan.json> 不能和其它参数混用")
        return plan_from_json(args.plan_file, cwd)
    mode = "gapp" if args.gapp else "csv" if args.csv else "all"
    return normalize_ui_plan(UiPlan(
        family=args.family,
        project=args.project,
        mode=mode,
        workspace=args.workspace,
        dry_run=args.dry_run,
        log=args.log,
    ))


def collect_interactive_plan(project_root, configs_dir):
    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")
    family, project = prompt_family_project(project_root, configs_dir, "未找到可用于 UI 编译的 defconfig")
    mode = prompt_choice("请选择 UI 编译类型", ["gapp", "csv", "all"], allow_back=True)
    if is_back(mode):
        return BACK
    return normalize_ui_plan(UiPlan(family=family, project=project, mode=mode, workspace=project_root))


def collect_tui_plan(project_root, configs_dir):
    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")
    initial_family = families[0]
    projects = list_projects(project_root, configs_dir, initial_family)
    if not projects:
        fail(f"{initial_family} 下未找到可用于 UI 编译的 defconfig")
    page = TuiPage(
        title="active-ui",
        sections=[
            MenuSection("Target", items=[
                MenuItem("Chip Family", "family", FieldType.CHOICE, value=initial_family, options=families),
                MenuItem("Project", "project", FieldType.CHOICE, value=projects[0],
                         options_provider=lambda s: list_projects(project_root, configs_dir, s.get("family", "")),
                         refreshes=["family"], enabled_when=lambda s: bool(s.get("family"))),
            ]),
            MenuSection("UI Build", items=[
                MenuItem("Build Type", "mode", FieldType.CHOICE, value="all", options=list(UI_MODES),
                         enabled_when=lambda s: bool(s.get("project"))),
            ]),
        ],
        actions=[MenuItem("Start", "_start", FieldType.ACTION), MenuItem("Exit", "_exit", FieldType.ACTION)],
    )
    try:
        result = curses.wrapper(page.run)
    except curses.error:
        print(f"{YELLOW}TUI 初始化失败，回退到文本交互模式{RESET}")
        return collect_interactive_plan(project_root, configs_dir)
    if result is None or result.get("_action") != "_start":
        sys.exit(0)
    return normalize_ui_plan(UiPlan(
        family=result.get("family", initial_family),
        project=result.get("project", projects[0]),
        mode=result.get("mode", "all"),
        workspace=project_root,
    ))


def config_value(config_path, key, default=""):
    return read_config_value(config_path, key) or default


def artifact_snapshot(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as file:
        digest = hashlib.sha256(file.read()).hexdigest()
    stat = os.stat(path)
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, digest


def artifact_changed(path, before):
    return os.path.isfile(path) and os.path.getsize(path) > 0 and artifact_snapshot(path) != before


def force_config_value(path, key, value):
    with open(path, "r", encoding="utf-8") as file:
        lines = file.readlines()
    found = False
    with open(path, "w", encoding="utf-8") as file:
        for line in lines:
            if line.startswith(key + "=") and not line.lstrip().startswith("#"):
                file.write(f'{key}="{value}"\n')
                found = True
            else:
                file.write(line)
        if not found:
            file.write(f'{key}="{value}"\n')


def makeui_command(build_dir):
    return f"cd {quote_cmd([build_dir])} && python3 scripts/makeUI.py ../ui ."


def gapp_paths(project_root, config_path, require_exists=True):
    project = config_value(config_path, "BUILD_PROJECT_PATH_NAME", "madrid")
    base = config_value(config_path, "BUILD_STORYBOARD_RESOURCE_PATH", "ui/prototype/Application")
    directory = os.path.join(project_root, base, project)
    candidates = [os.path.join(directory, f"Application_{project}.gapp"), os.path.join(directory, "Application.gapp")]
    if not require_exists:
        return candidates[0]
    for path in candidates:
        if os.path.isfile(path):
            return path
    fail(f"未找到 Application.gapp: {directory}")


def generate_gapp(project_root, build_dir, config_path, logger, dry_run=False):
    gapp = gapp_paths(project_root, config_path, require_exists=not dry_run)
    output = os.path.join(build_dir, "Application.bin")
    tool = os.path.join(build_dir, "cmd/linux64/gapp2bin")
    command = quote_cmd([tool, "-i", gapp, "-o", output])
    if config_value(config_path, "STORYBOARD_DYNAMIC_LOADER") == "y":
        command += " -v 2"
    if dry_run:
        print(f"{YELLOW}dry-run 命令: cd {quote_cmd([build_dir])} && {command}{RESET}")
        print(f"{YELLOW}dry-run: 生成 Application.crc{RESET}")
        return
    if not os.path.isfile(tool):
        fail(f"未找到 gapp2bin: {tool}")
    run_cmd(f"cd {quote_cmd([build_dir])} && {command}", logger)
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        fail(f"Application.bin 未生成或为空: {output}")
    with open(output, "rb") as file:
        crc = zlib.crc32(file.read()) & 0xFFFFFFFF
    with open(os.path.join(build_dir, "Application.crc"), "w", encoding="utf-8") as file:
        file.write(hex(crc)[2:])


def generate_locale(project_root, build_dir, config_path, dry_run=False):
    project = config_value(config_path, "BUILD_PROJECT_PATH_NAME", "madrid")
    base = config_value(config_path, "BUILD_STORYBOARD_RESOURCE_PATH", "ui/prototype/Application")
    locale = os.path.join(project_root, base, project, "locale")
    output = os.path.join(build_dir, "locale.zip")
    if dry_run:
        print(f"{YELLOW}dry-run: 打包 {locale} -> {output}{RESET}")
        return
    if not os.path.isdir(locale):
        fail(f"未找到 locale 目录: {locale}")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(locale):
            for name in files:
                path = os.path.join(root, name)
                archive.write(path, os.path.relpath(path, locale))


def run_ui_plan(plan, start_dir, project_root, build_dir):
    config_path = os.path.join(build_dir, ".config")
    if not os.path.isfile(config_path):
        fail(f"未找到 .config: {config_path}")
    logger = UiLogger(build_dir, plan.family, plan.project, plan.log)
    outputs = {
        "gapp": ["Application.bin", "Application.crc"],
        "csv": ["locale.zip"],
        "all": ["Application.bin", "Application.crc", "locale.zip"],
    }[plan.mode]
    output_paths = [os.path.join(build_dir, name) for name in outputs]
    before = {path: artifact_snapshot(path) for path in output_paths}
    backup = os.path.join(build_dir, ".config.active-ui-backup")
    success = False
    backup_cleanup_warning = None
    try:
        print(f"{YELLOW}脚本启动目录: {start_dir}{RESET}")
        print(f"{YELLOW}项目根目录: {project_root}{RESET}")
        print(f"{YELLOW}实际编译目录: {build_dir}{RESET}")
        print(f"{YELLOW}UIPlan: {json.dumps(asdict(plan), ensure_ascii=False)}{RESET}")
        if plan.family and plan.project:
            run_cmd(f"cd {quote_cmd([build_dir])} && make distclean", logger) if not plan.dry_run else print(f"{YELLOW}dry-run 命令: cd {quote_cmd([build_dir])} && make distclean{RESET}")
            run_cmd(f"cd {quote_cmd([build_dir])} && make clean", logger) if not plan.dry_run else print(f"{YELLOW}dry-run 命令: cd {quote_cmd([build_dir])} && make clean{RESET}")
            defconfig = f"{plan.family}_{plan.project}_defconfig"
            command = f"cd {quote_cmd([build_dir])} && make {quote_cmd([defconfig])}"
            if plan.dry_run:
                print(f"{YELLOW}dry-run 命令: {command}{RESET}")
            else:
                run_cmd(command, logger)
        if plan.dry_run:
            print(f"{YELLOW}dry-run: 备份并临时修改 {config_path}{RESET}")
            run_cmd(makeui_command(build_dir), logger) if False else print(f"{YELLOW}dry-run 命令: {makeui_command(build_dir)}{RESET}")
            if plan.mode in {"gapp", "all"}:
                generate_gapp(project_root, build_dir, config_path, logger, dry_run=True)
            if plan.mode in {"csv", "all"}:
                generate_locale(project_root, build_dir, config_path, dry_run=True)
            print(f"{YELLOW}dry-run: 恢复并删除 {backup}{RESET}")
            success = True
            return
        shutil.copy2(config_path, backup)
        force_config_value(config_path, "HMI_UPGRADE_CHECK_GAPP", "n")
        run_cmd(makeui_command(build_dir), logger)
        if plan.mode in {"gapp", "all"}:
            generate_gapp(project_root, build_dir, config_path, logger)
        if plan.mode in {"csv", "all"}:
            generate_locale(project_root, build_dir, config_path)
        unchanged = [path for path in output_paths if not artifact_changed(path, before[path])]
        if unchanged:
            fail("UI 产物未确认由本次生成或更新: " + ", ".join(unchanged))
        success = True
        print(f"{GREEN}UI 产物生成成功: {', '.join(output_paths)}{RESET}")
        maybe_prompt_ui_push(plan.mode, build_dir, logger)
    finally:
        if not plan.dry_run and os.path.exists(backup):
            try:
                shutil.copy2(backup, config_path)
            except OSError as error:
                print(f"{YELLOW}.config 恢复失败: {error}{RESET}")
            try:
                os.remove(backup)
            except OSError as error:
                backup_cleanup_warning = f"配置备份清理失败: {backup}: {error}"
        if backup_cleanup_warning:
            print(f"{YELLOW}{backup_cleanup_warning}{RESET}")
        if logger.enabled:
            logger.line(f"{'finish' if success else 'failed'}: {datetime.now().isoformat(timespec='seconds')}")
            print(f"{GREEN}日志文件: {logger.latest_path}{RESET}")
            print(f"{GREEN}历史日志: {logger.history_path}{RESET}")
        logger.close()


def resolve_wlctl_path():
    module_dir = os.path.dirname(os.path.realpath(__file__))
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(module_dir))))
    path = os.path.join(repo_root, "device-skills", "watchlink-v3", "scripts", "linux", "wlctl.sh")
    return path if os.path.isfile(path) else None


def maybe_prompt_ui_push(mode, build_dir, logger=None):
    if not sys.stdin.isatty():
        print(f"{YELLOW}非交互终端，跳过 UI 资源推送确认。{RESET}")
        return
    wlctl = resolve_wlctl_path()
    if not wlctl:
        print(f"{YELLOW}未找到 wlctl.sh，跳过 UI 资源推送确认。{RESET}")
        return
    actions = ["gapp"] if mode == "gapp" else ["csv"] if mode == "csv" else ["gapp", "csv"]
    print(f"\n{YELLOW}本次 UI 产物将按定向入口推送：{', '.join(actions)}{RESET}")
    if not prompt_yes_no("是否推送 UI 产物到设备？", default=False):
        print(f"{YELLOW}已跳过 UI 资源推送。{RESET}")
        return
    for action in actions:
        command = quote_cmd([wlctl, "fs", f"push-{action}", "--from-path", build_dir])
        run_cmd(command, logger)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 0:
        start_dir, project_root, configs_dir, build_dir = locate_project_root(os.getcwd())
        if sys.stdout.isatty():
            plan = collect_tui_plan(project_root, configs_dir)
        else:
            plan = collect_interactive_plan(project_root, configs_dir)
    else:
        plan = parse_args(argv, os.getcwd())
        start_dir, project_root, _, build_dir = locate_project_root(os.getcwd(), workspace=plan.workspace)
    run_ui_plan(plan, start_dir, project_root, build_dir)


if __name__ == "__main__":
    main()
