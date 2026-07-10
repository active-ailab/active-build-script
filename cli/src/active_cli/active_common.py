#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

STATE_FILE = ".active-build-state.json"
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


class ActiveLogger:
    def __init__(self, build_dir, tool_name, family, project, enabled):
        self.enabled = enabled
        self.handles = []
        self.latest_path = None
        self.history_path = None
        if not enabled:
            return

        safe_family = family or "current"
        safe_project = project or "current"
        base_dir = os.path.join(build_dir, "logs", tool_name, safe_family)
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


def fail(message, code=1):
    print(f"{RED}{message}{RESET}")
    sys.exit(code)


def should_monitor_elf_link(cmd):
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts or os.path.basename(parts[0]) != "make":
        return False
    if any(part.startswith(("APPDIR=", "BUILD_DIR=")) for part in parts[1:]):
        return False
    targets = [part for part in parts[1:] if not part.startswith("-") and "=" not in part]
    return not targets or "ota" in targets


def read_proc_text(path):
    try:
        with open(path, "rb") as file:
            return file.read()
    except OSError:
        return b""


def find_watch_elf_ld_pid():
    proc_root = "/proc"
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None

    for entry in entries:
        if not entry.isdigit():
            continue
        comm = read_proc_text(os.path.join(proc_root, entry, "comm")).decode(
            "utf-8", errors="ignore"
        ).strip()
        if comm != "ld":
            continue
        cmdline = read_proc_text(os.path.join(proc_root, entry, "cmdline")).replace(
            b"\0", b" "
        ).decode("utf-8", errors="ignore")
        if "/binary/watch@" in cmdline and ".elf" in cmdline:
            return entry
    return None


def proc_status_value(pid, key):
    status = read_proc_text(os.path.join("/proc", str(pid), "status")).decode(
        "utf-8", errors="ignore"
    )
    for line in status.splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return "-"


def proc_io_values(pid):
    values = {}
    text = read_proc_text(os.path.join("/proc", str(pid), "io")).decode(
        "utf-8", errors="ignore"
    )
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def proc_stat_fields(pid):
    stat = read_proc_text(os.path.join("/proc", str(pid), "stat")).decode(
        "utf-8", errors="ignore"
    )
    if not stat:
        return "-", "-"
    end_comm = stat.rfind(")")
    fields = stat[end_comm + 2 :].split()
    if len(fields) < 33:
        return "-", "-"
    state = fields[0]
    wchan = fields[32]
    return state, wchan


def print_elf_link_snapshot(pid, logger=None):
    state, wchan = proc_stat_fields(pid)
    io_values = proc_io_values(pid)
    lines = [
        f"{YELLOW}[elf-link] pid={pid} state={state} wchan={wchan}{RESET}",
        (
            "[elf-link] "
            f"VmRSS={proc_status_value(pid, 'VmRSS')} "
            f"VmHWM={proc_status_value(pid, 'VmHWM')} "
            f"Threads={proc_status_value(pid, 'Threads')}"
        ),
        (
            "[elf-link] "
            f"read_bytes={io_values.get('read_bytes', '-')} "
            f"write_bytes={io_values.get('write_bytes', '-')} "
            f"syscr={io_values.get('syscr', '-')} "
            f"syscw={io_values.get('syscw', '-')}"
        ),
        (
            "[elf-link] "
            f"voluntary_ctxt_switches={proc_status_value(pid, 'voluntary_ctxt_switches')} "
            f"nonvoluntary_ctxt_switches={proc_status_value(pid, 'nonvoluntary_ctxt_switches')}"
        ),
    ]
    for line in lines:
        print(line)
        if logger:
            logger.line(line)


def monitor_elf_link(stop_event, logger=None, interval=3):
    active_pid = None
    while not stop_event.is_set():
        pid = find_watch_elf_ld_pid()
        if pid:
            if pid != active_pid:
                active_pid = pid
                line = f"{YELLOW}[elf-link] detected watch ELF linker pid={pid}{RESET}"
                print(line)
                if logger:
                    logger.line(line)
            print_elf_link_snapshot(pid, logger)
        elif active_pid:
            line = f"{GREEN}[elf-link] linker pid={active_pid} finished{RESET}"
            print(line)
            if logger:
                logger.line(line)
            return
        stop_event.wait(interval)


def run_cmd(cmd, logger=None, monitor_elf_link_enabled=False):
    print(f"\n{YELLOW}>>> 执行: {cmd}{RESET}")
    if logger:
        logger.line(f">>> 执行: {cmd}")
    start_step = time.time()

    stop_monitor = threading.Event()
    monitor_thread = None
    if monitor_elf_link_enabled and should_monitor_elf_link(cmd):
        monitor_thread = threading.Thread(
            target=monitor_elf_link,
            args=(stop_monitor, logger),
            daemon=True,
        )
        monitor_thread.start()

    process = subprocess.Popen(
        cmd,
        shell=True,
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
        stop_monitor.set()
        if monitor_thread:
            monitor_thread.join(timeout=1)

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


def read_config_value(config_path, key):
    if not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as file:
        for line in file:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


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


def find_main_defconfig(configs_dir, family, project):
    if not family or not project:
        return None
    path = os.path.join(configs_dir, family, f"{family}_{project}_defconfig")
    return path if os.path.exists(path) else None


def find_available_families(configs_dir):
    if not os.path.isdir(configs_dir):
        return []
    return [
        name
        for name in ALLOWED_FAMILIES
        if os.path.isdir(os.path.join(configs_dir, name))
    ]


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
        if not os.path.exists(sensorhub_defconfig):
            continue
        projects.append(project)
    return sorted(set(projects))


def find_project_families(project_root, configs_dir, project):
    if not project:
        return []
    return [
        family
        for family in find_available_families(configs_dir)
        if project in list_projects(project_root, configs_dir, family)
    ]


def quote_cmd(parts):
    return " ".join(shlex.quote(str(part)) for part in parts)


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


BACK = object()


def is_back(value):
    return value is BACK


def prompt_choice(title, options, allow_back=False):
    if not options:
        fail(f"{title} 没有可选项")

    print(f"\n{YELLOW}{title}{RESET}")
    if allow_back:
        print("  0. 返回上一级")
    print_options_horizontal(options)

    while True:
        try:
            prompt = "请输入序号（输入 0 返回上一级）" if allow_back else "请输入序号"
            raw = input(f"{YELLOW}{prompt}: {RESET}").strip()
        except KeyboardInterrupt:
            fail("\n用户中断操作")

        if allow_back and raw in {"0", "b", "back"}:
            return BACK
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(options):
                return options[choice - 1]
        back_hint = "，或输入 0 返回上一级" if allow_back else ""
        print(f"{RED}输入无效，请输入 1 到 {len(options)} 之间的序号{back_hint}{RESET}")


def prompt_yes_no(prompt, default=True, allow_back=False):
    suffix = "Y/n" if default else "y/N"
    if allow_back:
        suffix = f"{suffix}, 输入 0 返回上一级"
    while True:
        try:
            raw = input(f"{YELLOW}{prompt} [{suffix}]: {RESET}").strip().lower()
        except KeyboardInterrupt:
            fail("\n用户中断操作")
        if allow_back and raw in {"0", "b", "back"}:
            return BACK
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        back_hint = "，或输入 0 返回上一级" if allow_back else ""
        print(f"{RED}请输入 y/yes 或 n/no{back_hint}{RESET}")


def prompt_family_project(project_root, configs_dir, empty_message):
    families = find_available_families(configs_dir)
    if not families:
        fail("configs 下未找到可用芯片目录")

    while True:
        family = prompt_choice("请选择芯片目录", families)
        projects = list_projects(project_root, configs_dir, family)
        if not projects:
            fail(f"{family} 下{empty_message}")

        while True:
            project = prompt_choice(f"请选择 {family} 项目", projects, allow_back=True)
            if is_back(project):
                break
            return family, project
