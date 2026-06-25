#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
import argparse
import curses
import filecmp
import json
import multiprocessing
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime

from .active_common import (
    ActiveLogger,
    BACK,
    GREEN,
    RED,
    RESET,
    STATE_FILE,
    YELLOW,
    fail,
    find_available_families,
    load_build_state,
    locate_project_root,
    list_projects,
    normalize_bool,
    prompt_choice,
    prompt_family_project,
    prompt_yes_no,
    quote_cmd,
    read_config_value,
    run_cmd,
    is_back,
)
from .active_tui import FieldType, MenuItem, MenuSection, StatusItem, TuiPage

DEFAULT_VERSION = "10.0.0"
DEFAULT_BUILD_FW_VER = "999.999"
LEGACY_LAST_THREADS_FILE = ".hmbuild_last_threads"
SENSORHUB_APPDIR = "out_hub"
VALID_BUILD_TYPES = {"debug", "inspect", "release_log", "release"}
HELP_TEXT = """\
用法:
  active-build
  active-build -c <mode> [-b build-type] [-a main-type] [-u sensorhub-type] [-j threads]
  active-build -f <family> -p <project> -m <mode> [-j threads] [options]
  active-build -i <plan-file>

核心参数:
  -f <family>       family / 芯片目录，例如 mhs003、mhs003s
  -p <project>      项目名称，例如 cologne、geneva、atlas
  -m <mode>         完整构建模式：fw、ota、sensorhub、sensorhub-fw、sensorhub-ota、debug、release、sim
  -j <threads>      构建线程数
  -c <mode>         使用当前 build/.config 继续编译；app 会按 fw 处理

高级参数:
  -d                重新加载 defconfig
  -v <version>      旧工程覆写 BOARD_FIRMWARE_VERSION；新工程传 BUILD_FW_VER，默认 999.999
  -w <path>         指定 workspace 根目录或 build 目录
  -i <file>         从 active-build BuildPlan JSON 文件读取构建计划
  -b <type>         全局 BUILD_TYPE，未单独指定时 main 和 sensorhub 共用
  -a <type>         main/fw/ota 阶段 BUILD_TYPE
  -u <type>         sensorhub 阶段 BUILD_TYPE
  -l                写入构建日志
  -h                显示帮助

支持的 mode:
  fw, firmware, app, ota, sensorhub, sensorhub-fw, sensorhub-firmware, sensorhub-ota, debug, release, sim

支持的 build type:
  debug, inspect, release_log, release

大小写:
  help 中统一展示小写短参；实现上接受对应大写短参作为同义别名，例如 -f/-F、-m/-M、-b/-B。

已移除:
  active-build <family> <project> <mode> [threads]
  请改用: active-build -f <family> -p <project> -m <mode> [-j threads]

bstyle:
  bstyle 编译已拆分为独立指令 active-bstyle。
"""


@dataclass
class BuildPlan:
    action: str = "build"
    family: str = None
    project: str = None
    mode: str = "sensorhub-ota"
    threads: str = None
    reload_defconfig: bool = False
    version: str = DEFAULT_VERSION
    version_explicit: bool = False
    build_type: str = None
    main_build_type: str = None
    sensorhub_build_type: str = None
    use_current_config: bool = False
    workspace: str = None
    log: bool = False


class BuildLogger(ActiveLogger):
    def __init__(self, build_dir, family, project, enabled):
        super().__init__(build_dir, "active-build", family, project, enabled)


@dataclass
class PythonStatus:
    ok: bool
    path: str = None
    version_text: str = None
    message: str = ""


def print_help():
    print(HELP_TEXT)


def get_default_python_version():
    python_path = shutil.which("python")
    if not python_path:
        return None, None

    result = subprocess.run(
        ["python", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    version_text = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not version_text:
        return python_path, None
    return python_path, version_text


def probe_python3_default():
    python_path, version_text = get_default_python_version()
    if version_text and version_text.startswith("Python 3"):
        return PythonStatus(
            ok=True,
            path=python_path,
            version_text=version_text,
            message=f"OK  {python_path} ({version_text})",
        )
    if python_path and version_text:
        return PythonStatus(
            ok=False,
            path=python_path,
            version_text=version_text,
            message=f"NOT OK  {python_path} ({version_text})",
        )
    if python_path:
        return PythonStatus(
            ok=False,
            path=python_path,
            version_text=None,
            message=f"NOT OK  {python_path} (版本未知)",
        )
    return PythonStatus(
        ok=False,
        path=None,
        version_text=None,
        message="NOT OK  python command not found",
    )


def ensure_python3_default():
    status = probe_python3_default()
    if status.ok:
        print(f"{GREEN}当前默认 python 正常: {status.path} ({status.version_text}){RESET}")
        return

    print(f"{YELLOW}检测到默认 python 不是 Python 3{RESET}")
    if status.path and status.version_text:
        print(f"{YELLOW}当前默认 python: {status.path} ({status.version_text}){RESET}")
    elif status.path:
        print(f"{YELLOW}当前默认 python: {status.path} (版本未知){RESET}")
    else:
        print(f"{YELLOW}当前环境未找到 python 命令{RESET}")
    fail("默认 python 必须是 Python 3，请用户自行调整后重新开始构建")


def block_start_when_python_not_ok(_state):
    status = probe_python3_default()
    if status.ok:
        return None
    return f"Python 状态异常，无法开始构建。\n{status.message}\n请自行调整默认 python 到 Python 3 后重试。"


def normalize_threads_value(value):
    if value is None:
        return None
    threads = str(value).strip()
    if threads.isdigit() and int(threads) > 0:
        return threads
    return None


def load_last_threads(project_root, build_dir=None):
    build_dir = build_dir or os.path.join(project_root, "build")
    threads = normalize_threads_value(load_build_state(build_dir).get("threads"))
    if threads:
        return threads

    legacy_cache_path = os.path.join(project_root, LEGACY_LAST_THREADS_FILE)
    if not os.path.exists(legacy_cache_path):
        return None

    try:
        with open(legacy_cache_path, "r", encoding="utf-8") as file:
            return normalize_threads_value(file.read())
    except OSError as error:
        print(f"{YELLOW}读取历史线程数失败: {error}{RESET}")
    return None


def remove_legacy_last_threads(project_root):
    legacy_cache_path = os.path.join(project_root, LEGACY_LAST_THREADS_FILE)
    if not os.path.exists(legacy_cache_path):
        return
    try:
        os.remove(legacy_cache_path)
    except OSError as error:
        print(f"{YELLOW}清理历史线程数文件失败: {error}{RESET}")


def normalize_mode(mode):
    if mode is None:
        fail("缺少构建模式")
    value = str(mode).lower()
    aliases = {
        "fw": "firmware",
        "firmware": "firmware",
        "app": "firmware",
        "ota": "ota",
        "sensorhub": "sensorhub",
        "senserhub": "sensorhub",
        "sensorhub-fw": "sensorhub-firmware",
        "sensorhub-firmware": "sensorhub-firmware",
        "senserhub-fw": "sensorhub-firmware",
        "senserhub-firmware": "sensorhub-firmware",
        "sensorhub-ota": "sensorhub-ota",
        "senserhub-ota": "sensorhub-ota",
        "debug": "sensorhub-ota",
        "release": "sensorhub-ota",
        "sim": "sim",
    }
    if value not in aliases:
        fail(f"无效构建模式: {mode}")
    return aliases[value]


def implied_build_type(mode):
    value = str(mode).lower()
    if value == "release":
        return "release"
    return None


def validate_build_type(build_type, label):
    if build_type in {None, ""}:
        return None
    value = str(build_type).lower()
    if value not in VALID_BUILD_TYPES:
        fail(f"无效 {label}: {build_type}，支持: debug, inspect, release_log, release")
    return value


def validate_version(version):
    if not version or re.search(r"[^0-9A-Za-z._-]", str(version)):
        fail(f"无效版本号: {version}，仅允许字母、数字、点、下划线和横线")
    return str(version)


def supports_build_fw_ver(project_root):
    if not project_root:
        return False
    return os.path.exists(
        os.path.join(project_root, "build", "build_rules", "fw_version.mk")
    )


def default_version_for_project(project_root):
    if supports_build_fw_ver(project_root):
        return DEFAULT_BUILD_FW_VER
    return DEFAULT_VERSION


def validate_build_fw_ver(version):
    value = validate_version(version)
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+\.[0-9]+)?", value):
        fail(f"新版本规则下版本号必须是两段 c.d 或四段 a.b.c.d 数字版本: {version}")
    return value


def configure_plan_version(project_root, plan):
    use_build_fw_ver = supports_build_fw_ver(project_root)
    setattr(plan, "_use_build_fw_ver", use_build_fw_ver)
    if use_build_fw_ver:
        if not plan.version_explicit:
            plan.version = DEFAULT_BUILD_FW_VER
        plan.version = validate_build_fw_ver(plan.version)
    else:
        plan.version = validate_version(plan.version or DEFAULT_VERSION)
    return plan


def plan_uses_build_fw_ver(plan):
    return bool(getattr(plan, "_use_build_fw_ver", False))


def normalize_plan(plan):
    if plan.action != "build":
        fail(f"不支持的 BuildPlan action: {plan.action}")

    original_mode = plan.mode
    plan.mode = normalize_mode(plan.mode)
    if plan.build_type is None:
        plan.build_type = implied_build_type(original_mode)

    plan.version = validate_version(plan.version or DEFAULT_VERSION)
    plan.build_type = validate_build_type(plan.build_type, "build type")
    plan.main_build_type = validate_build_type(plan.main_build_type, "main build type")
    plan.sensorhub_build_type = validate_build_type(
        plan.sensorhub_build_type, "sensorhub build type"
    )

    if plan.threads is None:
        plan.threads = str(multiprocessing.cpu_count() * 2)
    plan.threads = str(plan.threads)
    if not plan.threads.isdigit() or int(plan.threads) <= 0:
        fail(f"线程数必须是正整数: {plan.threads}")

    plan.reload_defconfig = normalize_bool(plan.reload_defconfig)
    plan.version_explicit = normalize_bool(plan.version_explicit)
    plan.use_current_config = normalize_bool(plan.use_current_config)
    plan.log = normalize_bool(plan.log)

    if not plan.use_current_config and (not plan.family or not plan.project):
        fail("完整构建必须指定 -f <family> 和 -p <project>")
    return plan


def build_arg_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-f", "-F", dest="family")
    parser.add_argument("-p", "-P", dest="project")
    parser.add_argument("-m", "-M", dest="mode")
    parser.add_argument("-j", "-J", dest="threads")
    parser.add_argument("-c", "-C", dest="current_mode")
    parser.add_argument("-d", "-D", dest="reload_defconfig", action="store_true")
    parser.add_argument("-v", "-V", dest="version")
    parser.add_argument("-w", "-W", dest="workspace")
    parser.add_argument("-i", "-I", dest="plan_file")
    parser.add_argument("-b", "-B", dest="build_type")
    parser.add_argument("-a", "-A", dest="main_build_type")
    parser.add_argument("-u", "-U", dest="sensorhub_build_type")
    parser.add_argument("-l", "-L", dest="log", action="store_true")
    parser.add_argument("-h", "-H", "--help", dest="help", action="store_true")
    parser.add_argument("legacy_args", nargs="*")
    return parser


def plan_from_json(path, cwd):
    plan_path = path
    if not os.path.isabs(plan_path):
        plan_path = os.path.abspath(os.path.join(cwd, plan_path))
    try:
        with open(plan_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except OSError as error:
        fail(f"读取 BuildPlan 文件失败: {error}")
    except json.JSONDecodeError as error:
        fail(f"BuildPlan JSON 格式错误: {error}")

    if not isinstance(data, dict):
        fail("BuildPlan JSON 顶层必须是对象")
    action = data.get("action", "build")
    if action != "build":
        fail(f"不支持的 BuildPlan action: {action}")
    allowed = set(BuildPlan.__dataclass_fields__.keys())

    unknown = sorted(set(data.keys()) - allowed)
    if unknown:
        fail(f"BuildPlan 包含未知字段: {', '.join(unknown)}")
    if action == "build" and "version" in data and "version_explicit" not in data:
        data["version_explicit"] = True

    plan = BuildPlan(**data)
    if plan.workspace and not os.path.isabs(plan.workspace):
        plan.workspace = os.path.abspath(os.path.join(cwd, plan.workspace))
    return normalize_plan(plan)


def parse_args_or_prompt(project_root=None, configs_dir=None, argv=None, cwd=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cwd = os.path.abspath(cwd or os.getcwd())
    project_root = project_root or cwd
    default_threads = load_last_threads(project_root) or str(multiprocessing.cpu_count() * 2)
    default_version = default_version_for_project(project_root)

    if len(argv) == 0:
        if configs_dir is None:
            _, project_root, configs_dir, _ = locate_project_root(cwd)
        if sys.stdout.isatty():
            return collect_tui_build_plan(project_root, configs_dir)
        return collect_interactive_plan(project_root, configs_dir)

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.help:
        print_help()
        sys.exit(0)

    if args.legacy_args:
        print(
            f"{RED}顺序式完整编译参数已移除，请改用：\n"
            f"active-build -f <family> -p <project> -m <mode> [-j threads]{RESET}"
        )
        sys.exit(1)

    if args.plan_file:
        mixed = [
            args.family,
            args.project,
            args.mode,
            args.current_mode,
            args.reload_defconfig,
            args.version,
            args.build_type,
            args.main_build_type,
            args.sensorhub_build_type,
            args.log,
        ]
        if any(mixed):
            fail("-i <plan-file> 不能和 -f/-p/-m/-c/-d/-v/-b/-a/-u/-l 混用")
        plan = plan_from_json(args.plan_file, cwd)
        if args.workspace:
            workspace = os.path.abspath(os.path.join(cwd, args.workspace))
            if plan.workspace and os.path.abspath(plan.workspace) != workspace:
                fail("-i <plan-file> 中的 workspace 不能和 -w <path> 指定不同路径")
            plan.workspace = workspace
        return plan

    if args.version is not None:
        if supports_build_fw_ver(project_root):
            validate_build_fw_ver(args.version)
        else:
            validate_version(args.version)

    if args.current_mode:
        plan = BuildPlan(
            mode=args.current_mode,
            threads=args.threads or default_threads,
            reload_defconfig=args.reload_defconfig,
            version=args.version or default_version,
            version_explicit=args.version is not None,
            build_type=args.build_type,
            main_build_type=args.main_build_type,
            sensorhub_build_type=args.sensorhub_build_type,
            use_current_config=True,
            workspace=args.workspace,
            log=args.log,
        )
        return normalize_plan(plan)

    if not args.family or not args.project or not args.mode:
        fail("完整构建必须使用: active-build -f <family> -p <project> -m <mode> [-j threads]")

    plan = BuildPlan(
        family=args.family,
        project=args.project,
        mode=args.mode,
        threads=args.threads or default_threads,
        reload_defconfig=True if args.mode and args.mode.lower() in {"debug", "release", "sim"} else args.reload_defconfig,
        version=args.version or default_version,
        version_explicit=args.version is not None,
        build_type=args.build_type,
        main_build_type=args.main_build_type,
        sensorhub_build_type=args.sensorhub_build_type,
        use_current_config=False,
        workspace=args.workspace,
        log=args.log,
    )
    return normalize_plan(plan)


def infer_plan_target_from_config(plan, build_dir):
    if plan.family and plan.project:
        return

    config_path = os.path.join(build_dir, ".config")
    if not os.path.exists(config_path):
        fail(f"当前 build 目录下未找到 .config，无法延用当前配置编译: {config_path}")

    family = read_config_value(config_path, "HMI_BUILD_BOARD")
    project = read_config_value(config_path, "HMI_PRODUCT_CUSTOMIZE_DIR")
    if not family:
        fail(f"当前配置缺少 HMI_BUILD_BOARD: {config_path}")
    if not project:
        fail(f"当前配置缺少 HMI_PRODUCT_CUSTOMIZE_DIR: {config_path}")
    plan.family = family
    plan.project = project


def prompt_threads(default_threads, allow_back=False):
    while True:
        try:
            suffix = f"默认 {default_threads}"
            if allow_back:
                suffix = f"{suffix}, 输入 0 返回上一级"
            raw = input(f"{YELLOW}请输入编译线程数 [{suffix}]: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")

        if allow_back and raw in {"0", "b", "back"}:
            return BACK
        if raw == "":
            return str(default_threads)
        if raw.isdigit() and int(raw) > 0:
            return raw
        back_hint = "，或输入 0 返回上一级" if allow_back else ""
        print(f"{RED}线程数必须是正整数{back_hint}{RESET}")


def prompt_optional_build_type(label, allow_back=False):
    options = ["默认", "debug", "inspect", "release_log", "release"]
    choice = prompt_choice(label, options, allow_back=allow_back)
    if is_back(choice):
        return BACK
    return None if choice == "默认" else choice


def prompt_version(allow_back=False):
    while True:
        try:
            suffix = " [输入 0 返回上一级]" if allow_back else ""
            version = input(f"{YELLOW}请输入版本号{suffix}: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if allow_back and version in {"0", "b", "back"}:
            return BACK
        if version:
            return version
        print(f"{RED}版本号不能为空{RESET}")


def collect_interactive_quick_plan(family, project, default_version, default_threads):
    step = 0
    mode = None

    while True:
        if step == 0:
            mode = prompt_choice("请选择编译模式", ["release", "debug", "sim"], allow_back=True)
            if is_back(mode):
                return BACK
            step = 1
            continue
        threads = prompt_threads(default_threads, allow_back=True)
        if is_back(threads):
            step = 0
            continue
        return normalize_plan(
            BuildPlan(
                family=family,
                project=project,
                mode=mode,
                threads=threads,
                reload_defconfig=True,
                version=default_version,
                version_explicit=False,
                build_type=None,
                main_build_type=None,
                sensorhub_build_type=None,
                log=False,
            )
        )


def collect_interactive_advanced_plan(family, project, default_version, default_threads):
    step = 0
    mode = None
    reload_defconfig = None
    use_default_version = None
    version = default_version
    version_explicit = False
    build_type = None
    main_build_type = None
    sensorhub_build_type = None
    log = None

    while True:
        if step == 0:
            mode = prompt_choice(
                "请选择高级构建模式",
                ["firmware", "ota", "sensorhub", "sensorhub-firmware", "sensorhub-ota"],
                allow_back=True,
            )
            if is_back(mode):
                return BACK
            step = 1
            continue
        if step == 1:
            reload_defconfig = prompt_yes_no("是否重新加载 defconfig", True, allow_back=True)
            if is_back(reload_defconfig):
                step = 0
                continue
            step = 2
            continue
        if step == 2:
            use_default_version = prompt_yes_no(
                f"是否使用默认版本 {default_version}",
                True,
                allow_back=True,
            )
            if is_back(use_default_version):
                step = 1
                continue
            if use_default_version:
                version = default_version
                version_explicit = False
                step = 4
            else:
                step = 3
            continue
        if step == 3:
            version = prompt_version(allow_back=True)
            if is_back(version):
                step = 2
                continue
            version_explicit = True
            step = 4
            continue
        if step == 4:
            build_type = prompt_optional_build_type("请选择全局 BUILD_TYPE", allow_back=True)
            if is_back(build_type):
                step = 2 if use_default_version else 3
                continue
            step = 5
            continue
        if step == 5:
            main_build_type = prompt_optional_build_type(
                "请选择 main/fw/ota 阶段 BUILD_TYPE",
                allow_back=True,
            )
            if is_back(main_build_type):
                step = 4
                continue
            step = 6
            continue
        if step == 6:
            sensorhub_build_type = prompt_optional_build_type(
                "请选择 sensorhub 阶段 BUILD_TYPE",
                allow_back=True,
            )
            if is_back(sensorhub_build_type):
                step = 5
                continue
            step = 7
            continue
        if step == 7:
            log = prompt_yes_no("是否写入构建日志", True, allow_back=True)
            if is_back(log):
                step = 6
                continue
            step = 8
            continue
        threads = prompt_threads(default_threads, allow_back=True)
        if is_back(threads):
            step = 7
            continue
        return normalize_plan(
            BuildPlan(
                family=family,
                project=project,
                mode=mode,
                threads=threads,
                reload_defconfig=reload_defconfig,
                version=version,
                version_explicit=version_explicit,
                build_type=build_type,
                main_build_type=main_build_type,
                sensorhub_build_type=sensorhub_build_type,
                log=log,
            )
        )


def collect_tui_build_plan(project_root, configs_dir):
    """menuconfig-style TUI that faithfully mirrors collect_interactive_plan.

    Dependency relationships (1:1 with existing linear flow):
      - family  → refreshes project (cascading options)
      - project ← must be re-calculated when family changes
      - entry   → quick/advanced visibility toggle (two mutually exclusive sections)
      - adv_use_default_version  → adv_version enabled/disabled
      - Back navigation  → Esc = cancel entire form (same as Ctrl-C in text mode)
    """
    last_threads = load_last_threads(project_root)
    default_threads = int(last_threads) if last_threads else multiprocessing.cpu_count() * 2
    default_version = default_version_for_project(project_root)

    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")

    initial_family = families[0]
    projects = list_projects(project_root, configs_dir, initial_family)
    if not projects:
        fail(f"{initial_family} 下未找到可编译的 defconfig")
    initial_project = projects[0]

    BUILD_TYPE_OPTS = ["默认", "debug", "inspect", "release_log", "release"]
    QUICK_MODES = ["release", "debug", "sim"]
    ADV_MODES = ["firmware", "ota", "sensorhub", "sensorhub-firmware", "sensorhub-ota"]
    python_status = probe_python3_default()
    manifest_status = {"ms": None, "checked": False}

    def _update_status_items(page):
        items = [StatusItem("Python", python_status.message, python_status.ok)]
        if manifest_status["checked"]:
            items.append(StatusItem("Manifest", manifest_status["ms"].message,
                                    manifest_status["ms"].ok))
        page.status_items = items

    def _on_project_changed(state, page):
        p = (state.get("project") or "").strip()
        if not p:
            manifest_status["checked"] = False
        else:
            manifest_status["ms"] = check_repo_manifest_status(project_root, p)
            manifest_status["checked"] = True
        _update_status_items(page)

    def _xml_action_confirm(page):
        # 1. Python check (keep existing blocking logic)
        msg = block_start_when_python_not_ok({})
        if msg:
            page._popup_message("Start Build", msg, ok=False)
            return True  # blocked — stay on form

        # 2. XML check — show confirm popup if not OK
        if not manifest_status["checked"]:
            return False  # no project selected yet, let action_guard handle it
        ms = manifest_status["ms"]
        if ms is None or ms.ok:
            return False  # XML OK → proceed

        if ms.missing_target:
            lines = [
                f"Target XML not found: {ms.target_xml}",
                "",
                "This may cause build errors.",
            ]
        else:
            lines = [
                f"Current manifest: {ms.current_xml}",
                f"Project requires:  {ms.target_xml}",
                "",
                "It is recommended to switch manifest first.",
            ]
        return page._popup_confirm("Start Build", lines)

    page = TuiPage(
        title="active-build",
        sections=[
            MenuSection("Target", items=[
                MenuItem("Chip Family", "family", FieldType.CHOICE,
                         value=None, options=families),
                MenuItem("Project", "project", FieldType.CHOICE,
                         value=None,
                         options_provider=lambda s: list_projects(project_root, configs_dir, s.get("family", "")),
                         refreshes=["family"],
                         enabled_when=lambda s: bool(s.get("family")),
                         on_change=_on_project_changed),
            ]),
            MenuSection("Build Entry", items=[
                MenuItem("构建入口", "entry", FieldType.CHOICE,
                         value=None,
                         options=["快速完整编译", "高级构建"],
                         enabled_when=lambda s: bool(s.get("project"))),
            ]),
            # ── 快速完整编译  (visible when entry == "快速完整编译") ──
            MenuSection("Quick Build", visible_when=lambda s: s.get("entry") == "快速完整编译", items=[
                MenuItem("编译模式", "quick_mode", FieldType.CHOICE,
                         value="release", options=QUICK_MODES),
                MenuItem("编译线程数", "quick_threads", FieldType.TEXT,
                         value=str(default_threads)),
            ]),
            # ── 高级构建  (visible when entry == "高级构建") ──
            MenuSection("Advanced Build", visible_when=lambda s: s.get("entry") == "高级构建", items=[
                MenuItem("构建模式", "adv_mode", FieldType.CHOICE,
                         value="sensorhub-ota", options=ADV_MODES),
                MenuItem("重新加载 defconfig", "adv_reload_defconfig", FieldType.TOGGLE,
                         value=True),
                MenuItem("使用默认版本", "adv_use_default_version", FieldType.TOGGLE,
                         value=True,
                         refreshes=["adv_version"]),
                MenuItem("自定义版本号", "adv_version", FieldType.TEXT,
                         value=None,
                         enabled_when=lambda s: not s.get("adv_use_default_version", True)),
                MenuItem("全局 BUILD_TYPE", "adv_build_type", FieldType.CHOICE,
                         value="默认", options=BUILD_TYPE_OPTS),
                MenuItem("main/fw/ota BUILD_TYPE", "adv_main_build_type", FieldType.CHOICE,
                         value="默认", options=BUILD_TYPE_OPTS),
                MenuItem("sensorhub BUILD_TYPE", "adv_sensorhub_build_type", FieldType.CHOICE,
                         value="默认", options=BUILD_TYPE_OPTS),
                MenuItem("写入构建日志", "adv_log", FieldType.TOGGLE,
                         value=True),
                MenuItem("编译线程数", "adv_threads", FieldType.TEXT,
                         value=str(default_threads)),
            ]),
        ],
        actions=[
            MenuItem("Start Build", "_start", FieldType.ACTION,
                     action_confirm=_xml_action_confirm,
                     action_guard=block_start_when_python_not_ok),
            MenuItem("Exit", "_exit", FieldType.ACTION),
        ],
        status_items=[
            StatusItem("Python", python_status.message, python_status.ok),
        ],
    )

    try:
        result = curses.wrapper(page.run)
    except curses.error:
        # Terminal too small or curses init failed — fall back to text mode
        print(f"{YELLOW}TUI 初始化失败，回退到文本交互模式{RESET}")
        return collect_interactive_plan(project_root, configs_dir)

    if result is None or result.get("_action") != "_start":
        sys.exit(0)

    family = result.get("family", initial_family)
    project = result.get("project", initial_project)

    if result.get("entry") == "快速完整编译":
        plan = BuildPlan(
            family=family,
            project=project,
            mode=result.get("quick_mode", "release"),
            threads=result.get("quick_threads", str(default_threads)),
            reload_defconfig=True,
            version=default_version,
            version_explicit=False,
            build_type=None,
            main_build_type=None,
            sensorhub_build_type=None,
            log=False,
        )
    else:
        build_type = _tui_opt_build_type(result.get("adv_build_type"))
        main_bt = _tui_opt_build_type(result.get("adv_main_build_type"))
        sensorhub_bt = _tui_opt_build_type(result.get("adv_sensorhub_build_type"))
        use_default = result.get("adv_use_default_version", True)
        plan = BuildPlan(
            family=family,
            project=project,
            mode=result.get("adv_mode", "sensorhub-ota"),
            threads=result.get("adv_threads", str(default_threads)),
            reload_defconfig=result.get("adv_reload_defconfig", True),
            version=result.get("adv_version", default_version) if not use_default else default_version,
            version_explicit=not use_default,
            build_type=build_type,
            main_build_type=main_bt,
            sensorhub_build_type=sensorhub_bt,
            log=result.get("adv_log", True),
        )
    setattr(plan, "_tui_xml_checked", manifest_status["checked"])
    return normalize_plan(plan)


def _tui_opt_build_type(value):
    """Map TUI build-type display name to internal value (None for 默认)."""
    if value in (None, "", "默认"):
        return None
    return value


def collect_interactive_plan(project_root, configs_dir):
    last_threads = load_last_threads(project_root)
    default_threads = int(last_threads) if last_threads else multiprocessing.cpu_count() * 2

    default_version = default_version_for_project(project_root)

    while True:
        family, project = prompt_family_project(
            project_root,
            configs_dir,
            "未找到可编译的 defconfig",
        )

        while True:
            entry = prompt_choice(
                "请选择构建入口",
                ["快速完整编译", "高级构建"],
                allow_back=True,
            )
            if is_back(entry):
                break
            if entry == "快速完整编译":
                plan = collect_interactive_quick_plan(
                    family,
                    project,
                    default_version,
                    default_threads,
                )
            elif entry == "高级构建":
                plan = collect_interactive_advanced_plan(
                    family,
                    project,
                    default_version,
                    default_threads,
                )
            if is_back(plan):
                continue
            return plan


def confirm_manifest_choice(should_switch):
    if should_switch:
        prompt = "检测到当前 XML 可能需要变更，是否仍继续后续流程? (Y/n): "
    else:
        prompt = "检测到当前 XML 可继续使用，是否继续后续流程? (Y/n): "

    while True:
        try:
            choice = input(f"\n{YELLOW}{prompt}{RESET}").strip().lower()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            return False

        if choice in {"", "y", "yes"}:
            print(f"{GREEN}继续后续流程{RESET}")
            return True
        if choice in {"n", "no"}:
            print(f"{RED}用户取消后续流程{RESET}")
            return False
        print(f"{RED}请输入 y/yes 或 n/no{RESET}")


def read_manifest_includes(manifest_path):
    try:
        root = ET.parse(manifest_path).getroot()
    except (ET.ParseError, OSError):
        return []

    includes = []
    for node in root.findall("include"):
        name = node.get("name")
        if name:
            includes.append(name)
    return includes


def manifest_matches_xml(manifest_path, current_xml_name, include_names, xml_name, xml_path, use_filecmp=False):
    if current_xml_name == xml_name:
        return True
    if xml_name in include_names:
        return True
    if use_filecmp:
        return filecmp.cmp(manifest_path, xml_path, shallow=False)
    return False


@dataclass
class ManifestStatus:
    ok: bool
    current_xml: str
    target_xml: str
    missing_target: bool = False
    message: str = ""
    content_identical: bool = False


def check_repo_manifest_status(project_root, project):
    """Pure read-only manifest check — no print, no input, no sys.exit."""
    repo_dir = os.path.join(project_root, ".repo")
    manifest_path = os.path.join(repo_dir, "manifest.xml")
    manifests_dir = os.path.join(repo_dir, "manifests")

    if not os.path.exists(manifest_path):
        return ManifestStatus(
            ok=False, current_xml="N/A", target_xml="",
            message=f"No manifest: {manifest_path}",
        )
    if not os.path.isdir(manifests_dir):
        return ManifestStatus(
            ok=False, current_xml="N/A", target_xml="",
            message=f"No manifests dir: {manifests_dir}",
        )

    current_manifest_realpath = os.path.realpath(manifest_path)
    current_xml_name = os.path.basename(current_manifest_realpath)
    current_include_names = read_manifest_includes(manifest_path)
    current_display = current_xml_name if current_xml_name else "manifest.xml"
    if current_include_names:
        current_display = ", ".join(current_include_names)

    # huamiOS.xml is the global default
    huamios_xml_path = os.path.join(manifests_dir, "huamiOS.xml")
    if os.path.exists(huamios_xml_path):
        if manifest_matches_xml(
            manifest_path, current_xml_name, current_include_names,
            "huamiOS.xml", huamios_xml_path,
            use_filecmp=True,
        ):
            return ManifestStatus(
                ok=True, current_xml="huamiOS.xml", target_xml="huamiOS.xml",
                message="huamiOS.xml  ✓",
            )

    project_xml_name = f"{project}.xml"
    project_xml_path = os.path.join(manifests_dir, project_xml_name)

    if not os.path.exists(project_xml_path):
        return ManifestStatus(
            ok=False, current_xml=current_display, target_xml=project_xml_name,
            missing_target=True,
            message=f"target {project_xml_name} missing",
        )

    if manifest_matches_xml(
        manifest_path, current_xml_name, current_include_names,
        project_xml_name, project_xml_path,
    ):
        # Name or include matched — also verify content
        content_same = filecmp.cmp(manifest_path, project_xml_path, shallow=False)
        if content_same:
            return ManifestStatus(
                ok=True, current_xml=project_xml_name, target_xml=project_xml_name,
                message=f"{project_xml_name}  ✓",
            )
        return ManifestStatus(
            ok=False, current_xml=current_display, target_xml=project_xml_name,
            message=f"{project_xml_name}  (name match, content differs)",
            content_identical=False,
        )

    content_same = filecmp.cmp(manifest_path, project_xml_path, shallow=False)
    tag = "same content" if content_same else "content differs"
    return ManifestStatus(
        ok=False, current_xml=current_display, target_xml=project_xml_name,
        message=f"{current_display}  →  {project_xml_name}  ({tag})",
        content_identical=content_same,
    )


def print_manifest_check_summary(current_xml, target_xml, should_switch, missing_target=None):
    print(f"\n{YELLOW}检查仓库 XML 配置...{RESET}")
    print(f"{YELLOW}当前 XML: {current_xml}{RESET}")
    print(f"{YELLOW}目标 XML: {target_xml}{RESET}")
    if missing_target:
        print(f"{RED}目标 XML 文件不存在: {missing_target}{RESET}")
    print(f"{YELLOW}是否需要变更 XML: {'是' if should_switch else '否'}{RESET}")


def compare_repo_manifest(project_root, project):
    repo_dir = os.path.join(project_root, ".repo")
    manifest_path = os.path.join(repo_dir, "manifest.xml")
    manifests_dir = os.path.join(repo_dir, "manifests")

    if not os.path.exists(manifest_path):
        print(f"{RED}未找到当前 manifest 文件: {manifest_path}{RESET}")
        sys.exit(1)

    if not os.path.isdir(manifests_dir):
        print(f"{RED}未找到 manifests 目录: {manifests_dir}{RESET}")
        sys.exit(1)

    current_manifest_realpath = os.path.realpath(manifest_path)
    current_xml_name = os.path.basename(current_manifest_realpath)
    current_include_names = read_manifest_includes(manifest_path)
    current_display_name = current_xml_name if current_xml_name else "manifest.xml"
    if current_include_names:
        current_display_name = ", ".join(current_include_names)

    huamios_xml_path = os.path.join(manifests_dir, "huamiOS.xml")
    if os.path.exists(huamios_xml_path):
        if manifest_matches_xml(
            manifest_path,
            current_xml_name,
            current_include_names,
            "huamiOS.xml",
            huamios_xml_path,
            use_filecmp=True,
        ):
            print_manifest_check_summary("huamiOS.xml", "huamiOS.xml", False)
            return confirm_manifest_choice(False)

    project_xml_name = f"{project}.xml"
    project_xml_path = os.path.join(manifests_dir, project_xml_name)
    if not os.path.exists(project_xml_path):
        print_manifest_check_summary(
            current_display_name,
            project_xml_name,
            True,
            missing_target=project_xml_path,
        )
        return confirm_manifest_choice(True)

    if manifest_matches_xml(
        manifest_path,
        current_xml_name,
        current_include_names,
        project_xml_name,
        project_xml_path,
    ):
        print_manifest_check_summary(project_xml_name, project_xml_name, False)
        return confirm_manifest_choice(False)

    print_manifest_check_summary(current_display_name, project_xml_name, True)
    return confirm_manifest_choice(True)


def resolve_defconfig_paths(project_root, configs_dir, family, project):
    defconfig_main = os.path.join(configs_dir, family, f"{family}_{project}_defconfig")
    sensorhub_defconfig = os.path.join(
        configs_dir, family, f"{family}_{project}_sensorhub_defconfig"
    )
    if not os.path.exists(defconfig_main):
        fail(f"主 defconfig 不存在: {defconfig_main}")
    if not os.path.exists(sensorhub_defconfig):
        fail(f"未找到 sensorhub defconfig: {sensorhub_defconfig}")

    return os.path.basename(defconfig_main), os.path.basename(sensorhub_defconfig)


def resolve_sim_defconfig(project_root, family, project):
    sim_candidates = [
        os.path.join(project_root, "configs", "simulator", f"simx86_{project}_defconfig"),
        os.path.join(project_root, "simulator", "configs", f"simx86_{project}_defconfig"),
    ]
    for sim_defconfig in sim_candidates:
        if os.path.exists(sim_defconfig):
            return os.path.basename(sim_defconfig)

    print(f"{RED}未找到模拟器 defconfig，已检查以下路径:{RESET}")
    for sim_defconfig in sim_candidates:
        print(f"{RED}  - {sim_defconfig}{RESET}")
    sys.exit(1)


def check_project_switch_requires_reload(build_dir, plan):
    state = load_build_state(build_dir)
    if not state:
        return

    old_family = state.get("family")
    old_project = state.get("project")
    if not old_family or not old_project:
        return
    if (old_family, old_project) == (plan.family, plan.project):
        return
    if plan.reload_defconfig:
        return
    fail(
        f"当前目标从 {old_family}/{old_project} 切换到 {plan.family}/{plan.project}，"
        f"需要使用 -d 重新加载 defconfig"
    )


def record_build_state(build_dir, plan):
    state_path = os.path.join(build_dir, STATE_FILE)
    data = {
        "family": plan.family,
        "project": plan.project,
        "threads": str(plan.threads),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        with open(state_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"{YELLOW}记录 active-build 状态失败: {error}{RESET}")


def stage_build_type(plan, stage):
    if stage == "sensorhub":
        return plan.sensorhub_build_type or plan.build_type
    if stage == "main":
        return plan.main_build_type or plan.build_type
    return plan.build_type


def command_build_type(plan, stage):
    build_type = stage_build_type(plan, stage)
    if build_type == "release":
        return build_type
    return None


def make_ota_version_args(plan):
    if not plan_uses_build_fw_ver(plan):
        return []
    part_count = len(str(plan.version).split("."))
    if part_count == 2:
        return [
            "FW_VER_STRATEGY=os_global",
            f"BUILD_FW_VER={shlex.quote(plan.version)}",
        ]
    return [f"BUILD_FW_VER={shlex.quote(plan.version)}"]


def make_cmd(plan, target=None, stage=None, appdir=None, build_dir_var=None, threads=None):
    parts = ["make"]
    if target:
        parts.append(target)
    build_type = command_build_type(plan, stage)
    if build_type and target not in {"clean", "distclean"}:
        parts.append(f"BUILD_TYPE={build_type}")
    if target == "ota":
        parts.extend(make_ota_version_args(plan))
    if build_dir_var:
        parts.append(f"BUILD_DIR={build_dir_var}")
    if appdir:
        parts.append(f"APPDIR={appdir}")
    if threads:
        parts.append(f"-j{threads}")
    return quote_cmd(parts)


def patch_config_version(config_path, version):
    if not os.path.exists(config_path):
        fail(f"Config 不存在，无法覆写版本: {config_path}")

    with open(config_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    updated = False
    output = []
    for line in lines:
        if line.startswith("BOARD_FIRMWARE_VERSION="):
            output.append(f'BOARD_FIRMWARE_VERSION="{version}"\n')
            updated = True
        else:
            output.append(line)
    if not updated:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append(f'BOARD_FIRMWARE_VERSION="{version}"\n')

    with open(config_path, "w", encoding="utf-8") as file:
        file.writelines(output)


def apply_version_override(build_dir, plan, logger=None, appdir=None):
    if plan_uses_build_fw_ver(plan):
        print(f"{YELLOW}新版本规则使用 BUILD_FW_VER={plan.version}，已跳过 .config 版本覆写{RESET}")
        if logger:
            logger.line(f"skip version override: BUILD_FW_VER={plan.version}")
        return

    if plan.use_current_config and not plan.version_explicit:
        print(f"{YELLOW}当前配置编译已跳过 .config 版本覆写{RESET}")
        if logger:
            logger.line("skip version override: current config")
        return

    if appdir:
        if plan.version_explicit and plan.mode == "sensorhub":
            config_path = os.path.join(build_dir, appdir, ".config")
            patch_config_version(config_path, plan.version)
            run_cmd(
                make_cmd(plan, "silentoldconfig", stage="sensorhub", appdir=appdir),
                logger,
            )
            print(
                f"{GREEN}已覆写 {config_path} 的 BOARD_FIRMWARE_VERSION={plan.version}{RESET}"
            )
            if logger:
                logger.line(f"version override: {config_path} -> {plan.version}")
            return
        print(f"{YELLOW}已跳过 {appdir} .config 版本覆写{RESET}")
        if logger:
            logger.line(f"skip version override: {appdir}")
        return

    config_path = os.path.join(build_dir, ".config")
    patch_config_version(config_path, plan.version)
    run_cmd(make_cmd(plan, "silentoldconfig", stage="main"), logger)
    print(f"{GREEN}已覆写 {config_path} 的 BOARD_FIRMWARE_VERSION={plan.version}{RESET}")
    if logger:
        logger.line(f"version override: {config_path} -> {plan.version}")


def remove_sensorhub_output(build_dir, logger=None):
    output_dir = os.path.join(build_dir, SENSORHUB_APPDIR)
    if output_dir in {build_dir, "/", ""}:
        fail(f"危险的 sensorhub 输出目录: {output_dir}")
    run_cmd(f"rm -rf {shlex.quote(SENSORHUB_APPDIR)}", logger)


def copy_sensorhub_outputs(build_dir, family, sensorhub_target_dir, logger=None):
    src_dir = os.path.join(build_dir, SENSORHUB_APPDIR, f"sensorhub@{family}", "binary")
    if not os.path.isdir(src_dir):
        fail(f"Sensorhub output directory not found: {src_dir}")
    os.makedirs(sensorhub_target_dir, exist_ok=True)

    artifacts = [
        f"sensorhub@{family}_sign.bin",
        f"sensorhub@{family}.bin",
        f"sensorhub@{family}.elf",
        f"sensorhub@{family}.map",
        f"sensorhub@{family}.hex",
    ]
    copied = []
    for artifact in artifacts:
        src = os.path.join(src_dir, artifact)
        if not os.path.exists(src):
            continue
        dst = os.path.join(sensorhub_target_dir, artifact)
        shutil.copy2(src, dst)
        copied.append(dst)
        print(f"{GREEN}Copied {artifact} -> {sensorhub_target_dir}{RESET}")
        if logger:
            logger.line(f"Copied {artifact} -> {sensorhub_target_dir}")

    if not copied:
        fail(f"No sensorhub artifacts found under {src_dir}")
    return copied


def prepare_main_config(build_dir, plan, main_defconfig, logger=None, clean=False):
    if clean:
        run_cmd(make_cmd(plan, "distclean", stage="main"), logger)
        run_cmd(make_cmd(plan, "clean", stage="main"), logger)
    if plan.reload_defconfig or not plan.use_current_config:
        run_cmd(make_cmd(plan, main_defconfig, stage="main"), logger)
    apply_version_override(build_dir, plan, logger)


def prepare_sensorhub_config(build_dir, plan, main_defconfig, sensorhub_defconfig, logger=None, clean=False):
    if clean:
        run_cmd(make_cmd(plan, "distclean", stage="sensorhub"), logger)
        run_cmd(make_cmd(plan, "clean", stage="sensorhub"), logger)
    remove_sensorhub_output(build_dir, logger)
    run_cmd(make_cmd(plan, main_defconfig, stage="sensorhub"), logger)
    run_cmd(make_cmd(plan, sensorhub_defconfig, stage="sensorhub", appdir=SENSORHUB_APPDIR), logger)
    apply_version_override(build_dir, plan, logger, appdir=SENSORHUB_APPDIR)


def build_sensorhub(build_dir, plan, main_defconfig, sensorhub_defconfig, logger=None, clean=False):
    prepare_sensorhub_config(build_dir, plan, main_defconfig, sensorhub_defconfig, logger, clean=clean)
    run_cmd(
        make_cmd(plan, stage="sensorhub", appdir=SENSORHUB_APPDIR, build_dir_var=SENSORHUB_APPDIR),
        logger,
    )


def run_build_plan(plan, start_dir, project_root, configs_dir, build_dir):
    ensure_python3_default()
    infer_plan_target_from_config(plan, build_dir)
    configure_plan_version(project_root, plan)
    plan = normalize_plan(plan)

    if plan.family not in find_available_families(configs_dir):
        fail(f"无效芯片目录: {plan.family}")

    main_defconfig, sensorhub_defconfig = resolve_defconfig_paths(
        project_root, configs_dir, plan.family, plan.project
    )

    check_project_switch_requires_reload(build_dir, plan)
    logger = BuildLogger(build_dir, plan.family, plan.project, plan.log)

    os.chdir(build_dir)
    total_start = time.time()
    success = False
    try:
        print(f"\n{YELLOW}脚本启动目录: {start_dir}{RESET}")
        print(f"{YELLOW}项目根目录: {project_root}{RESET}")
        print(f"{YELLOW}实际编译目录: {build_dir}{RESET}")
        print(f"{YELLOW}BuildPlan: {json.dumps(asdict(plan), ensure_ascii=False)}{RESET}")
        if logger.enabled:
            logger.line(f"start: {datetime.now().isoformat(timespec='seconds')}")
            logger.line(f"BuildPlan: {json.dumps(asdict(plan), ensure_ascii=False)}")

        if not plan.use_current_config and not getattr(plan, "_tui_xml_checked", False):
            if not compare_repo_manifest(project_root, plan.project):
                sys.exit(0)

        if plan.mode == "sim":
            sim_defconfig = resolve_sim_defconfig(project_root, plan.family, plan.project)
            run_cmd(make_cmd(plan, "distclean", stage="main"), logger)
            run_cmd(make_cmd(plan, "clean", stage="main"), logger)
            run_cmd(
                make_cmd(plan, main_defconfig, stage="main") + f" BUILD_SIM={shlex.quote(sim_defconfig)}",
                logger,
            )
            run_cmd(make_cmd(plan, "ota", stage="main", threads=plan.threads), logger)
        elif plan.mode == "firmware":
            prepare_main_config(build_dir, plan, main_defconfig, logger, clean=plan.reload_defconfig)
            run_cmd(make_cmd(plan, stage="main", threads=plan.threads), logger)
        elif plan.mode == "ota":
            prepare_main_config(build_dir, plan, main_defconfig, logger, clean=plan.reload_defconfig)
            run_cmd(make_cmd(plan, "ota", stage="main", threads=plan.threads), logger)
        elif plan.mode == "sensorhub":
            build_sensorhub(
                build_dir,
                plan,
                main_defconfig,
                sensorhub_defconfig,
                logger,
                clean=plan.reload_defconfig,
            )
        elif plan.mode == "sensorhub-firmware":
            build_sensorhub(
                build_dir,
                plan,
                main_defconfig,
                sensorhub_defconfig,
                logger,
                clean=plan.reload_defconfig,
            )
            apply_version_override(build_dir, plan, logger)
            run_cmd(make_cmd(plan, stage="main", threads=plan.threads), logger)
        elif plan.mode == "sensorhub-ota":
            build_sensorhub(
                build_dir,
                plan,
                main_defconfig,
                sensorhub_defconfig,
                logger,
                clean=plan.reload_defconfig,
            )
            apply_version_override(build_dir, plan, logger)
            run_cmd(make_cmd(plan, "ota", stage="main", threads=plan.threads), logger)
        else:
            fail(f"未实现的构建模式: {plan.mode}")

        record_build_state(build_dir, plan)
        remove_legacy_last_threads(project_root)
        success = True
    finally:
        total_end = time.time()
        if success:
            print(f"\n{GREEN}编译完成，总耗时 {total_end - total_start:.2f} 秒{RESET}")
        else:
            print(f"\n{RED}编译中断，总耗时 {total_end - total_start:.2f} 秒{RESET}")
        if logger.enabled:
            status = "finish" if success else "failed"
            logger.line(f"{status}: {datetime.now().isoformat(timespec='seconds')}")
            logger.line(f"total seconds: {total_end - total_start:.2f}")
            print(f"{GREEN}日志文件: {logger.latest_path}{RESET}")
            print(f"{GREEN}历史日志: {logger.history_path}{RESET}")
        logger.close()


def active_build_main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if any(arg in {"-h", "-H", "--help"} for arg in argv):
        print_help()
        sys.exit(0)

    early_workspace = None
    for index, arg in enumerate(argv):
        if arg in {"-w", "-W"} and index + 1 < len(argv):
            early_workspace = argv[index + 1]
            break

    start_dir, project_root, configs_dir, build_dir = locate_project_root(
        os.getcwd(), workspace=early_workspace
    )
    plan = parse_args_or_prompt(project_root, configs_dir, argv, cwd=os.getcwd())
    if plan.workspace:
        start_dir, project_root, configs_dir, build_dir = locate_project_root(
            os.getcwd(), workspace=plan.workspace
        )
    run_build_plan(plan, start_dir, project_root, configs_dir, build_dir)


def main(argv=None):
    return active_build_main(argv)


if __name__ == "__main__":
    main()
