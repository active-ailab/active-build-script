#!/usr/bin/env python3
import argparse
import filecmp
import json
import multiprocessing
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

DEFAULT_VERSION = "10.0.0"
LEGACY_LAST_THREADS_FILE = ".hmbuild_last_threads"
STATE_FILE = ".active-build-state.json"
SENSORHUB_APPDIR = "out_hub"
VALID_BUILD_TYPES = {"debug", "inspect", "release_log", "release"}
ALLOWED_FAMILIES = ("mhs003", "mhs003s")
DERIVED_SUFFIXES = {
    "boot",
    "ht",
    "recovery",
    "jlinkbin",
    "sensorhub",
    "ota",
}
MHS003_ALLOWED_VARIANT_SUFFIXES = {"2pd", "64m"}

HELP_TEXT = """\
用法:
  active-build
  active-build bstyle [-i input.style] [-o output.bstyle] [-f family] [-p project] [-w workspace]
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
  -v <version>      临时覆写 BOARD_FIRMWARE_VERSION，默认 10.0.0
  -w <path>         指定 workspace 根目录或 build 目录
  -i <file>         从 BuildPlan JSON 文件读取构建计划
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
  active-build bstyle 使用当前 workspace 下 build/cmd/linux32 或 linux64 的 bstylenc。
  active-build bstylenc 是兼容别名。
  -i/-o 直接传递给底层 bstylenc；-o 不传时自动生成同目录同名 .bstyle。
  底层 bstylenc 的 -w/-h/-p 默认从 configs/<family>/<family>_<project>_defconfig 推导。
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


@dataclass
class BstylencPlan:
    action: str = "bstylenc"
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


class BuildLogger:
    def __init__(self, build_dir, family, project, enabled):
        self.enabled = enabled
        self.handles = []
        self.latest_path = None
        self.history_path = None
        if not enabled:
            return

        safe_family = family or "current"
        safe_project = project or "current"
        base_dir = os.path.join(build_dir, "logs", "active-build", safe_family)
        history_dir = os.path.join(base_dir, safe_project)
        os.makedirs(history_dir, exist_ok=True)
        self.latest_path = os.path.join(base_dir, f"{safe_project}.log")
        self.history_path = os.path.join(
            history_dir, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        )
        self.handles = [
            open(self.latest_path, "w", encoding="utf-8"),
            open(self.history_path, "w", encoding="utf-8"),
        ]

    def write(self, text):
        if not self.enabled:
            return
        for handle in self.handles:
            handle.write(text)
            handle.flush()

    def line(self, text):
        self.write(text + "\n")

    def close(self):
        for handle in self.handles:
            handle.close()
        self.handles = []


def print_help():
    print(HELP_TEXT)


def fail(message, code=1):
    print(f"{RED}{message}{RESET}")
    sys.exit(code)


def run_cmd(cmd, logger=None):
    print(f"\n{YELLOW}>>> 执行: {cmd}{RESET}")
    if logger:
        logger.line(f">>> 执行: {cmd}")
    start_step = time.time()

    if logger and logger.enabled:
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            print(line, end="")
            logger.write(line)
        returncode = process.wait()
    else:
        result = subprocess.run(cmd, shell=True)
        returncode = result.returncode

    end_step = time.time()
    if returncode != 0:
        print(f"\n{RED}命令执行失败: {cmd}{RESET}")
        print(f"{YELLOW}耗时: {end_step - start_step:.2f} 秒{RESET}")
        if logger:
            logger.line(f"命令执行失败: {cmd}")
            logger.line(f"耗时: {end_step - start_step:.2f} 秒")
        sys.exit(returncode)

    print(f"{GREEN}完成: {cmd} (耗时 {end_step - start_step:.2f} 秒){RESET}")
    if logger:
        logger.line(f"完成: {cmd} (耗时 {end_step - start_step:.2f} 秒)")


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


def ensure_python3_default():
    python_path, version_text = get_default_python_version()
    if version_text and version_text.startswith("Python 3"):
        print(f"{GREEN}当前默认 python 正常: {python_path} ({version_text}){RESET}")
        return

    print(f"{YELLOW}检测到默认 python 不是 Python 3{RESET}")
    if python_path and version_text:
        print(f"{YELLOW}当前默认 python: {python_path} ({version_text}){RESET}")
    elif python_path:
        print(f"{YELLOW}当前默认 python: {python_path} (版本未知){RESET}")
    else:
        print(f"{YELLOW}当前环境未找到 python 命令{RESET}")

    if shutil.which("update-alternatives") is None:
        fail("未找到 update-alternatives，请手动切换默认 python 到 Python 3 后再编译")

    print(f"{YELLOW}尝试启动: sudo update-alternatives --config python{RESET}")
    result = subprocess.run("sudo update-alternatives --config python", shell=True)
    if result.returncode != 0:
        fail("切换命令执行失败，请手动切换默认 python 到 Python 3 后再编译")

    _, new_version_text = get_default_python_version()
    if new_version_text and new_version_text.startswith("Python 3"):
        print(f"{GREEN}默认 python 已切换为 Python 3 ({new_version_text}){RESET}")
        return
    fail("切换后默认 python 仍不是 Python 3，请手动处理后再编译")


def load_build_state(build_dir):
    state_path = os.path.join(build_dir, STATE_FILE)
    if not os.path.exists(state_path):
        return {}

    try:
        with open(state_path, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


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


def normalize_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


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


def normalize_optional_positive_int(value, label):
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if text.isdigit() and int(text) > 0:
        return text
    fail(f"{label} 必须是正整数: {value}")


def normalize_optional_positive_float(value, label):
    if value in {None, ""}:
        return None
    text = str(value).strip().strip('"')
    try:
        number = float(text)
    except ValueError:
        fail(f"{label} 必须是正数: {value}")
    if number <= 0:
        fail(f"{label} 必须是正数: {value}")
    return text


def normalize_bstylenc_plan(plan):
    if plan.action != "bstylenc":
        fail(f"不支持的 BstylencPlan action: {plan.action}")
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


def bstylenc_arg_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-f", "-F", dest="family")
    parser.add_argument("-p", "-P", dest="project")
    parser.add_argument("-i", "-I", dest="input")
    parser.add_argument("-o", "-O", dest="output")
    parser.add_argument("-w", "-W", dest="workspace")
    parser.add_argument("-l", "-L", dest="log", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("-h", "-H", "--help", dest="help", action="store_true")
    return parser


def parse_bstylenc_args(argv):
    parser = bstylenc_arg_parser()
    args = parser.parse_args(argv)
    if args.help:
        print_help()
        sys.exit(0)
    return normalize_bstylenc_plan(
        BstylencPlan(
            family=args.family,
            project=args.project,
            input=args.input,
            output=args.output,
            workspace=args.workspace,
            dry_run=args.dry_run,
            log=args.log,
        )
    )


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
    if action == "build":
        allowed = set(BuildPlan.__dataclass_fields__.keys())
    elif action == "bstylenc":
        allowed = set(BstylencPlan.__dataclass_fields__.keys())
    else:
        fail(f"不支持的 BuildPlan action: {action}")

    unknown = sorted(set(data.keys()) - allowed)
    if unknown:
        fail(f"BuildPlan 包含未知字段: {', '.join(unknown)}")
    if action == "build" and "version" in data and "version_explicit" not in data:
        data["version_explicit"] = True

    plan = BuildPlan(**data) if action == "build" else BstylencPlan(**data)
    if plan.workspace and not os.path.isabs(plan.workspace):
        plan.workspace = os.path.abspath(os.path.join(cwd, plan.workspace))
    if action == "build":
        return normalize_plan(plan)
    return normalize_bstylenc_plan(plan)


def parse_args_or_prompt(project_root=None, configs_dir=None, argv=None, cwd=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cwd = os.path.abspath(cwd or os.getcwd())
    project_root = project_root or cwd
    default_threads = load_last_threads(project_root) or str(multiprocessing.cpu_count() * 2)

    if len(argv) == 0:
        if configs_dir is None:
            _, project_root, configs_dir, _ = locate_project_root(cwd)
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

    if args.current_mode:
        plan = BuildPlan(
            mode=args.current_mode,
            threads=args.threads or default_threads,
            reload_defconfig=args.reload_defconfig,
            version=args.version or DEFAULT_VERSION,
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
        version=args.version or DEFAULT_VERSION,
        version_explicit=args.version is not None,
        build_type=args.build_type,
        main_build_type=args.main_build_type,
        sensorhub_build_type=args.sensorhub_build_type,
        use_current_config=False,
        workspace=args.workspace,
        log=args.log,
    )
    return normalize_plan(plan)


def read_config_value(config_path, key):
    if not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as file:
        for line in file:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


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


def normalize_workspace_root(candidate):
    abs_candidate = os.path.abspath(candidate)
    if os.path.isdir(os.path.join(abs_candidate, "configs")) and os.path.isdir(
        os.path.join(abs_candidate, "build")
    ):
        return abs_candidate
    if os.path.basename(abs_candidate) == "build" and os.path.isdir(
        os.path.join(os.path.dirname(abs_candidate), "configs")
    ):
        return os.path.dirname(abs_candidate)
    return None


def locate_project_root(start_dir=None, workspace=None):
    if workspace:
        root = normalize_workspace_root(workspace)
        if root is None:
            fail(f"无效 workspace: {workspace}，必须是项目根目录或 build 目录")
        return os.path.abspath(start_dir or os.getcwd()), root, os.path.join(root, "configs"), os.path.join(root, "build")

    start_dir = os.path.abspath(start_dir or os.getcwd())
    current_dir = start_dir
    while True:
        root = normalize_workspace_root(current_dir)
        if root:
            return start_dir, root, os.path.join(root, "configs"), os.path.join(root, "build")

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            fail("未找到同时包含 configs 和 build 的项目根目录")
        current_dir = parent_dir


def find_available_families(configs_dir):
    if not os.path.isdir(configs_dir):
        return []
    return [
        name
        for name in ALLOWED_FAMILIES
        if os.path.isdir(os.path.join(configs_dir, name))
    ]


def normalize_name(name):
    return name.replace("-", "_")


def resolve_product_path(project_root, family, project):
    products_root = os.path.join(project_root, "platform", "board", family, "products")
    if not os.path.isdir(products_root):
        return None

    project_key = normalize_name(project.replace("_64m", ""))
    candidates = []
    for entry in os.listdir(products_root):
        full_path = os.path.join(products_root, entry)
        if not os.path.isdir(full_path):
            continue
        entry_key = normalize_name(entry)
        if project_key == entry_key:
            return full_path
        if project_key.startswith(entry_key + "_"):
            candidates.append((len(entry_key), full_path))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def list_projects(project_root, configs_dir, family):
    family_dir = os.path.join(configs_dir, family)
    prefix = f"{family}_"
    suffix = "_defconfig"
    projects = []

    if not os.path.isdir(family_dir):
        return projects

    for name in sorted(os.listdir(family_dir)):
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        project = name[len(prefix) : -len(suffix)]
        if not project or project.split("_")[-1] in DERIVED_SUFFIXES:
            continue
        if family == "mhs003":
            parts = project.split("_")
            if len(parts) > 1 and parts[-1] not in MHS003_ALLOWED_VARIANT_SUFFIXES:
                continue
        sensorhub_defconfig = os.path.join(family_dir, f"{family}_{project}_sensorhub_defconfig")
        product_path = resolve_product_path(project_root, family, project)
        if not os.path.exists(sensorhub_defconfig) or product_path is None:
            continue
        projects.append(project)
    return sorted(set(projects))


def print_options_horizontal(options):
    terminal_width = shutil.get_terminal_size((120, 20)).columns
    entries = [f"{index}. {option}" for index, option in enumerate(options, start=1)]
    entry_width = max(len(entry) for entry in entries) + 4
    columns = max(1, terminal_width // entry_width)

    for row_start in range(0, len(entries), columns):
        row = entries[row_start : row_start + columns]
        if len(row) == 1:
            print(f"  {row[0]}")
            continue
        print("  " + "".join(item.ljust(entry_width) for item in row).rstrip())


def prompt_choice(title, options):
    if not options:
        fail(f"{title} 没有可选项")

    print(f"\n{YELLOW}{title}{RESET}")
    print_options_horizontal(options)

    while True:
        try:
            raw = input(f"{YELLOW}请输入序号: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")

        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(options):
                return options[choice - 1]
        print(f"{RED}输入无效，请输入 1 到 {len(options)} 之间的序号{RESET}")


def prompt_yes_no(prompt, default=False):
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{YELLOW}{prompt} [{suffix}]: {RESET}").strip().lower()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print(f"{RED}请输入 y/yes 或 n/no{RESET}")


def prompt_threads(default_threads):
    while True:
        try:
            raw = input(f"{YELLOW}请输入编译线程数 [默认 {default_threads}]: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")

        if raw == "":
            return str(default_threads)
        if raw.isdigit() and int(raw) > 0:
            return raw
        print(f"{RED}线程数必须是正整数{RESET}")


def prompt_optional_build_type(label):
    options = ["默认", "debug", "inspect", "release_log", "release"]
    choice = prompt_choice(label, options)
    return None if choice == "默认" else choice


def default_bstyle_output(input_path):
    return os.path.splitext(input_path)[0] + ".bstyle"


def prompt_style_input():
    while True:
        try:
            raw = input(f"{YELLOW}请输入 style 输入文件路径: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if not raw:
            print(f"{RED}style 输入文件路径不能为空{RESET}")
            continue
        if not raw.endswith(".style"):
            print(f"{RED}输入文件必须是 .style: {raw}{RESET}")
            continue
        if not os.path.isfile(raw):
            print(f"{RED}style 输入文件不存在: {raw}{RESET}")
            continue
        return raw


def collect_interactive_bstylenc_plan(project_root, family, project):
    input_path = prompt_style_input()
    output_path = default_bstyle_output(input_path)
    print(f"{YELLOW}输出文件默认: {output_path}{RESET}")
    if prompt_yes_no("是否修改输出路径", False):
        try:
            output_path = input(f"{YELLOW}请输入 bstyle 输出文件路径: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if not output_path:
            fail("bstyle 输出文件路径不能为空")

    return normalize_bstylenc_plan(
        BstylencPlan(
            family=family,
            project=project,
            input=input_path,
            output=output_path,
            workspace=project_root,
        )
    )


def collect_interactive_plan(project_root, configs_dir):
    last_threads = load_last_threads(project_root)
    default_threads = int(last_threads) if last_threads else multiprocessing.cpu_count() * 2
    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")

    family = prompt_choice("请选择芯片目录", families)
    projects = list_projects(project_root, configs_dir, family)
    if not projects:
        fail(f"{family} 下未找到可编译的 defconfig")

    project = prompt_choice(f"请选择 {family} 项目", projects)
    entry = prompt_choice("请选择构建入口", ["快速完整编译", "高级构建", "bstyle 编译"])
    if entry == "快速完整编译":
        mode = prompt_choice("请选择编译模式", ["release", "debug", "sim"])
        reload_defconfig = True
        version = DEFAULT_VERSION
        version_explicit = False
        build_type = None
        main_build_type = None
        sensorhub_build_type = None
        log = False
    elif entry == "高级构建":
        mode = prompt_choice(
            "请选择高级构建模式",
            ["firmware", "ota", "sensorhub", "sensorhub-firmware", "sensorhub-ota"],
        )
        reload_defconfig = prompt_yes_no("是否重新加载 defconfig", False)
        version = DEFAULT_VERSION
        version_explicit = False
        if not prompt_yes_no("是否使用默认版本 10.0.0", True):
            version = input(f"{YELLOW}请输入版本号: {RESET}").strip()
            version_explicit = True
        build_type = prompt_optional_build_type("请选择全局 BUILD_TYPE")
        main_build_type = prompt_optional_build_type("请选择 main/fw/ota 阶段 BUILD_TYPE")
        sensorhub_build_type = prompt_optional_build_type("请选择 sensorhub 阶段 BUILD_TYPE")
        log = prompt_yes_no("是否写入构建日志", False)
    else:
        return collect_interactive_bstylenc_plan(project_root, family, project)

    threads = prompt_threads(default_threads)
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


def confirm_manifest_choice(should_switch):
    if should_switch:
        prompt = "检测到当前 XML 可能需要变更，是否仍继续后续流程? (y/n): "
    else:
        prompt = "检测到当前 XML 可继续使用，是否继续后续流程? (y/n): "

    while True:
        try:
            choice = input(f"\n{YELLOW}{prompt}{RESET}").strip().lower()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            return False

        if choice in {"y", "yes"}:
            print(f"{GREEN}继续后续流程{RESET}")
            return True
        if choice in {"n", "no", ""}:
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


def manifest_matches_xml(manifest_path, current_xml_name, include_names, xml_name, xml_path):
    if current_xml_name == xml_name:
        return True
    if xml_name in include_names:
        return True
    return filecmp.cmp(manifest_path, xml_path, shallow=False)


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

    product_path = resolve_product_path(project_root, family, project)
    if product_path is None:
        fail(f"未找到产品目录: platform/board/{family}/products 下无匹配项 ({project})")
    sensorhub_target_dir = os.path.join(product_path, "sensorhub")
    if not os.path.isdir(sensorhub_target_dir):
        fail(f"未找到 sensorhub 目录: {sensorhub_target_dir}")

    return os.path.basename(defconfig_main), os.path.basename(sensorhub_defconfig), sensorhub_target_dir


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


def find_main_defconfig(configs_dir, family, project):
    if not family or not project:
        return None
    path = os.path.join(configs_dir, family, f"{family}_{project}_defconfig")
    return path if os.path.exists(path) else None


def find_bstylenc_project_families(configs_dir, project):
    if not project or not os.path.isdir(configs_dir):
        return []
    families = []
    for family in sorted(os.listdir(configs_dir)):
        family_dir = os.path.join(configs_dir, family)
        if not os.path.isdir(family_dir):
            continue
        if find_main_defconfig(configs_dir, family, project):
            families.append(family)
    return families


def infer_bstylenc_target(plan, configs_dir, build_dir):
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
        families = find_bstylenc_project_families(configs_dir, plan.project)
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


def resolve_bstylenc_input_output(plan, start_dir):
    if not plan.input:
        plan.input = infer_single_style_input(start_dir)
    if not plan.input.endswith(".style"):
        fail(f"输入文件必须是 .style: {plan.input}")
    if not os.path.isfile(plan.input):
        fail(f"style 输入文件不存在: {plan.input}")
    if not plan.output:
        plan.output = default_bstyle_output(plan.input)


def parse_bstylenc_params_from_defconfig(defconfig_path, plan):
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
    normalize_bstylenc_plan(plan)


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


def make_bstylenc_cmd(tool_path, plan):
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


def quote_cmd(parts):
    return " ".join(shlex.quote(str(part)) for part in parts)


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


def make_cmd(plan, target=None, stage=None, appdir=None, build_dir_var=None, threads=None):
    parts = ["make"]
    if target:
        parts.append(target)
    build_type = command_build_type(plan, stage)
    if build_type and target not in {"clean", "distclean"}:
        parts.append(f"BUILD_TYPE={build_type}")
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


def build_sensorhub(build_dir, plan, main_defconfig, sensorhub_defconfig, sensorhub_target_dir, logger=None, clean=False):
    prepare_sensorhub_config(build_dir, plan, main_defconfig, sensorhub_defconfig, logger, clean=clean)
    run_cmd(
        make_cmd(plan, stage="sensorhub", appdir=SENSORHUB_APPDIR, build_dir_var=SENSORHUB_APPDIR),
        logger,
    )
    copy_sensorhub_outputs(build_dir, plan.family, sensorhub_target_dir, logger)


def run_bstylenc_plan(plan, start_dir, project_root, configs_dir, build_dir):
    plan = normalize_bstylenc_plan(plan)
    resolve_bstylenc_input_output(plan, start_dir)
    needs_defconfig = plan.width is None or plan.height is None or plan.pixel_ratio is None
    defconfig = None
    if needs_defconfig:
        defconfig = infer_bstylenc_target(plan, configs_dir, build_dir)
        parse_bstylenc_params_from_defconfig(defconfig, plan)
    tool_path = resolve_bstylenc_tool(project_root)
    command = make_bstylenc_cmd(tool_path, plan)
    logger = BuildLogger(build_dir, plan.family, plan.project, plan.log)
    success = False

    try:
        print(f"\n{YELLOW}脚本启动目录: {start_dir}{RESET}")
        print(f"{YELLOW}项目根目录: {project_root}{RESET}")
        if defconfig:
            print(f"{YELLOW}主 defconfig: {defconfig}{RESET}")
        print(f"{YELLOW}BstylencPlan: {json.dumps(asdict(plan), ensure_ascii=False)}{RESET}")
        if logger.enabled:
            logger.line(f"start: {datetime.now().isoformat(timespec='seconds')}")
            logger.line(f"BstylencPlan: {json.dumps(asdict(plan), ensure_ascii=False)}")
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


def run_build_plan(plan, start_dir, project_root, configs_dir, build_dir):
    ensure_python3_default()
    infer_plan_target_from_config(plan, build_dir)
    plan = normalize_plan(plan)

    if plan.family not in find_available_families(configs_dir):
        fail(f"无效芯片目录: {plan.family}")

    main_defconfig, sensorhub_defconfig, sensorhub_target_dir = resolve_defconfig_paths(
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

        if not plan.use_current_config and not compare_repo_manifest(project_root, plan.project):
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
                sensorhub_target_dir,
                logger,
                clean=plan.reload_defconfig,
            )
        elif plan.mode == "sensorhub-firmware":
            build_sensorhub(
                build_dir,
                plan,
                main_defconfig,
                sensorhub_defconfig,
                sensorhub_target_dir,
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
                sensorhub_target_dir,
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"bstyle", "bstylenc"}:
        plan = parse_bstylenc_args(argv[1:])
        start_dir, project_root, configs_dir, build_dir = locate_project_root(
            os.getcwd(), workspace=plan.workspace
        )
        run_bstylenc_plan(plan, start_dir, project_root, configs_dir, build_dir)
        return

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
    if isinstance(plan, BstylencPlan):
        run_bstylenc_plan(plan, start_dir, project_root, configs_dir, build_dir)
    else:
        run_build_plan(plan, start_dir, project_root, configs_dir, build_dir)


if __name__ == "__main__":
    main()
