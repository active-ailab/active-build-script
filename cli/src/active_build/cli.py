#!/usr/bin/env python3
import filecmp
import multiprocessing
import os
import shutil
import subprocess
import sys
import time


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

VALID_BUILD_MODES = {"release", "debug", "sim"}
VALID_CURRENT_TARGET_ALIASES = {"app": "make", "ota": "ota"}
DERIVED_SUFFIXES = {
    "boot",
    "ht",
    "recovery",
    "jlinkbin",
    "sensorhub",
    "ota",
}
MHS003_ALLOWED_VARIANT_SUFFIXES = {"2pd", "64m"}
LAST_THREADS_FILE = ".hmbuild_last_threads"

HELP_TEXT = """\
用法:
  active-build
  active-build <芯片目录> <项目名称> <编译模式> [线程数]
  active-build -c <app|ota> [线程数]
  active-build --current <app|ota> [线程数]
  active-build -h
  active-build --help

兼容原脚本:
  python hmbuild.py
  python hmbuild.py <芯片目录> <项目名称> <编译模式> [线程数]
  python hmbuild.py -c <app|ota> [线程数]

模式说明:
  无参数:
    进入交互模式，选择芯片目录、项目、编译模式和线程数

  完整编译:
    根据参数执行完整编译流程，编译前会检查 XML，并临时修改主 defconfig

  当前配置编译:
    `-c app`  基于 build/.config 执行 `make -j线程数`
    `-c ota`  基于 build/.config 执行 `make ota -j线程数`

参数说明:
  芯片目录:
    当前支持 `mhs003`、`mhs003s`

  项目名称:
    对应 `configs/<芯片目录>/` 下的主 defconfig
    例如: `mhs003 cologne` -> `configs/mhs003/mhs003_cologne_defconfig`

  编译模式:
    `release` 发布编译
    `debug`   调试编译
    `sim`     模拟器编译

  线程数:
    默认值为 CPU 核数 * 2
    `-c` 模式未传时优先复用上次记录
    记录文件: `%s`

示例:
  active-build
  active-build mhs003 cologne debug
  active-build mhs003 geneva release 16
  active-build mhs003s atlas debug 8
  active-build -c app
  active-build -c ota
  active-build -c ota 12

注意事项:
  - `-c app` / `-c ota` 依赖 build 目录下已有 `.config`
  - 完整编译前会先检查 `.repo/manifest.xml` 与目标 XML
  - 完整编译会临时修改 defconfig，并在结束后自动恢复
  - 实际编译命令在项目根目录的 `build` 下执行
  - 如果签名 sensorhub 不存在，脚本会提示是否回退使用未签名产物
  - 默认 `python` 不是 Python 3 时，会提示切换
""" % LAST_THREADS_FILE


def run_cmd(cmd):
    print(f"\n{YELLOW}>>> 执行: {cmd}{RESET}")
    start_step = time.time()
    result = subprocess.run(cmd, shell=True)
    end_step = time.time()

    if result.returncode != 0:
        print(f"\n{RED}命令执行失败: {cmd}{RESET}")
        print(f"{YELLOW}耗时: {end_step - start_step:.2f} 秒{RESET}")
        sys.exit(result.returncode)

    print(f"{GREEN}完成: {cmd} (耗时 {end_step - start_step:.2f} 秒){RESET}")


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
        print(f"{RED}未找到 update-alternatives，请手动切换默认 python 到 Python 3 后再编译{RESET}")
        sys.exit(1)

    print(f"{YELLOW}尝试启动: sudo update-alternatives --config python{RESET}")
    try:
        result = subprocess.run("sudo update-alternatives --config python", shell=True)
    except Exception as error:
        print(f"{RED}无法执行切换命令: {error}{RESET}")
        print(f"{RED}请手动切换默认 python 到 Python 3 后再编译{RESET}")
        sys.exit(1)

    if result.returncode != 0:
        print(f"{RED}切换命令执行失败，请手动切换默认 python 到 Python 3 后再编译{RESET}")
        sys.exit(1)

    _, new_version_text = get_default_python_version()
    if new_version_text and new_version_text.startswith("Python 3"):
        print(f"{GREEN}默认 python 已切换为 Python 3 ({new_version_text}){RESET}")
        return

    print(f"{RED}切换后默认 python 仍不是 Python 3，请手动处理后再编译{RESET}")
    sys.exit(1)


def load_last_threads(project_root):
    cache_path = os.path.join(project_root, LAST_THREADS_FILE)
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as file:
            threads = file.read().strip()
    except OSError:
        return None

    if threads.isdigit() and int(threads) > 0:
        return threads
    return None


def save_last_threads(project_root, threads):
    cache_path = os.path.join(project_root, LAST_THREADS_FILE)
    try:
        with open(cache_path, "w", encoding="utf-8") as file:
            file.write(str(threads))
    except OSError as error:
        print(f"{YELLOW}记录上次线程数失败: {error}{RESET}")


def print_help():
    print(HELP_TEXT)


def modify_defconfig_version(defconfig_path, target_version="10.0.0"):
    backup_path = defconfig_path + ".backup"
    shutil.copy2(defconfig_path, backup_path)
    print(f"{YELLOW}已备份 {defconfig_path} 到 {backup_path}{RESET}")

    with open(defconfig_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    version_found = False
    modified = False
    old_version = None

    for index, line in enumerate(lines):
        if line.startswith("BOARD_FIRMWARE_VERSION="):
            version_found = True
            old_version = line.strip().split("=")[1].strip('"')
            if old_version != target_version:
                lines[index] = f'BOARD_FIRMWARE_VERSION="{target_version}"\n'
                print(f"{YELLOW}将版本从 {old_version} 修改为 {target_version}{RESET}")
                modified = True
            else:
                print(f"{GREEN}版本已经是 {target_version}，无需修改{RESET}")
            break

    if not version_found:
        lines.append(f'\nBOARD_FIRMWARE_VERSION="{target_version}"\n')
        old_version = "未设置"
        modified = True
        print(f"{YELLOW}新增 BOARD_FIRMWARE_VERSION={target_version}{RESET}")

    if modified:
        with open(defconfig_path, "w", encoding="utf-8") as file:
            file.writelines(lines)
        print(f"{GREEN}已更新 {defconfig_path}{RESET}")

    return backup_path, modified, old_version


def restore_defconfig(backup_path, original_path):
    if os.path.exists(backup_path):
        shutil.move(backup_path, original_path)
        print(f"{GREEN}已恢复 {original_path}{RESET}")


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
    current_display_name = current_xml_name if current_xml_name else "manifest.xml"

    print(f"\n{YELLOW}检查仓库 XML 配置...{RESET}")
    print(f"{YELLOW}当前 manifest: {manifest_path}{RESET}")
    print(f"{YELLOW}当前实际指向: {current_manifest_realpath}{RESET}")

    huamios_xml_path = os.path.join(manifests_dir, "huamiOS.xml")
    if os.path.exists(huamios_xml_path):
        if filecmp.cmp(manifest_path, huamios_xml_path, shallow=False):
            print(f"{GREEN}当前使用的 XML: huamiOS.xml{RESET}")
            print(f"{GREEN}manifest.xml 与 huamiOS.xml 一致{RESET}")
            print(f"{YELLOW}是否需要变更 XML: 否{RESET}")
            return confirm_manifest_choice(False)
        print(f"{YELLOW}manifest.xml 与 huamiOS.xml 不一致，继续检查项目 XML{RESET}")
    else:
        print(f"{YELLOW}未找到 huamiOS.xml，继续检查项目 XML{RESET}")

    project_xml_name = f"{project}.xml"
    project_xml_path = os.path.join(manifests_dir, project_xml_name)
    if not os.path.exists(project_xml_path):
        print(f"{RED}未找到当前项目对应的 XML: {project_xml_path}{RESET}")
        print(f"{YELLOW}当前使用的 XML: {current_display_name}{RESET}")
        print(f"{YELLOW}当前编译项目目标 XML: {project_xml_name}{RESET}")
        print(f"{RED}无法确认是否需要切换 XML{RESET}")
        return confirm_manifest_choice(True)

    if filecmp.cmp(manifest_path, project_xml_path, shallow=False):
        print(f"{GREEN}当前使用的 XML: {project_xml_name}{RESET}")
        print(f"{GREEN}manifest.xml 与 {project_xml_name} 一致{RESET}")
        print(f"{YELLOW}是否需要变更 XML: 否{RESET}")
        return confirm_manifest_choice(False)

    print(f"{YELLOW}当前使用的 XML: {current_display_name}{RESET}")
    print(f"{YELLOW}当前编译项目目标 XML: {project_xml_name}{RESET}")
    print(f"{RED}manifest.xml 与 {project_xml_name} 不一致{RESET}")
    print(f"{YELLOW}是否需要变更 XML: 是{RESET}")
    return confirm_manifest_choice(True)


def confirm_fallback_unsigned_bin(unsigned_bin_path):
    while True:
        try:
            choice = input(
                f"\n{YELLOW}未找到签名文件，是否使用 {unsigned_bin_path} 继续编译? (y/n): {RESET}"
            ).strip().lower()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            return False

        if choice in {"y", "yes"}:
            print(f"{YELLOW}将使用未签名文件继续编译{RESET}")
            return True
        if choice in {"n", "no", ""}:
            print(f"{RED}用户取消编译{RESET}")
            return False
        print(f"{RED}请输入 y/yes 或 n/no{RESET}")


def confirm_continue(modified_files, old_versions):
    if not modified_files:
        print(f"\n{GREEN}没有文件需要修改，直接开始编译...{RESET}")
        return True

    print(f"\n{YELLOW}{'=' * 60}{RESET}")
    print(f"{YELLOW}已修改的配置文件信息:{RESET}")
    print(f"{YELLOW}{'=' * 60}{RESET}")

    for index, (file_path, old_version) in enumerate(zip(modified_files, old_versions), start=1):
        print(f"{YELLOW}{index}. {os.path.basename(file_path)}{RESET}")
        print(f"   原版本: {old_version} -> 新版本: 10.0.0")
        print(f"   路径: {file_path}")

    print(f"\n{YELLOW}{'=' * 60}{RESET}")
    print(f"{YELLOW}编译完成后会自动恢复所有修改{RESET}")
    print(f"{YELLOW}{'=' * 60}{RESET}")

    while True:
        try:
            choice = input(f"\n{YELLOW}是否继续编译? (y/n): {RESET}").strip().lower()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            return False

        if choice in {"y", "yes"}:
            print(f"{GREEN}开始编译...{RESET}")
            return True
        if choice in {"n", "no", ""}:
            print(f"{RED}用户取消编译{RESET}")
            return False
        print(f"{RED}请输入 y/yes 或 n/no{RESET}")


def resolve_sensorhub_binary(sensorhub_output_dir, family):
    sign_bin = os.path.join(sensorhub_output_dir, f"sensorhub@{family}_sign.bin")
    unsigned_bin = os.path.join(sensorhub_output_dir, f"sensorhub@{family}.bin")

    if os.path.exists(sign_bin):
        print(f"{GREEN}找到签名文件: {sign_bin}{RESET}")
        return sign_bin

    print(f"{YELLOW}签名文件不存在: {sign_bin}{RESET}")
    if not os.path.exists(unsigned_bin):
        print(f"{RED}未签名文件也不存在: {unsigned_bin}{RESET}")
        return None

    print(f"{YELLOW}可回退使用未签名文件: {unsigned_bin}{RESET}")
    if confirm_fallback_unsigned_bin(unsigned_bin):
        return unsigned_bin
    return None


def locate_project_root(start_dir=None):
    start_dir = os.path.abspath(start_dir or os.getcwd())
    current_dir = start_dir

    while True:
        configs_dir = os.path.join(current_dir, "configs")
        build_dir = os.path.join(current_dir, "build")
        if os.path.isdir(configs_dir) and os.path.isdir(build_dir):
            return start_dir, current_dir, configs_dir, build_dir

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            print(f"{RED}未找到同时包含 configs 和 build 的项目根目录{RESET}")
            sys.exit(1)
        current_dir = parent_dir


def find_available_families(configs_dir):
    families = []
    for family in ("mhs003", "mhs003s"):
        if os.path.isdir(os.path.join(configs_dir, family)):
            families.append(family)
    return families


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
        print(f"{RED}{title} 没有可选项{RESET}")
        sys.exit(1)

    print(f"\n{YELLOW}{title}{RESET}")
    print_options_horizontal(options)

    while True:
        try:
            raw = input(f"{YELLOW}请输入序号: {RESET}").strip()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            sys.exit(1)

        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(options):
                return options[choice - 1]
        print(f"{RED}输入无效，请输入 1 到 {len(options)} 之间的序号{RESET}")


def prompt_build_mode():
    return prompt_choice("请选择编译模式", ["release", "debug", "sim"])


def prompt_threads(default_threads):
    while True:
        try:
            raw = input(f"{YELLOW}请输入编译线程数 [默认 {default_threads}]: {RESET}").strip()
        except KeyboardInterrupt:
            print(f"\n{RED}用户中断操作{RESET}")
            sys.exit(1)

        if raw == "":
            return str(default_threads)
        if raw.isdigit() and int(raw) > 0:
            return raw
        print(f"{RED}线程数必须是正整数{RESET}")


def collect_interactive_selection(project_root, configs_dir):
    last_threads = load_last_threads(project_root)
    default_threads = int(last_threads) if last_threads else multiprocessing.cpu_count() * 2
    families = find_available_families(configs_dir)
    if not families:
        print(f"{RED}configs 下未找到 mhs003 或 mhs003s 目录{RESET}")
        sys.exit(1)

    family = prompt_choice("请选择芯片目录", families)
    projects = list_projects(project_root, configs_dir, family)
    if not projects:
        print(f"{RED}{family} 下未找到可编译的 defconfig{RESET}")
        sys.exit(1)

    project = prompt_choice(f"请选择 {family} 项目", projects)
    build_mode = prompt_build_mode()
    threads = prompt_threads(default_threads)
    return {
        "action": "full_build",
        "family": family,
        "project": project,
        "build_mode": build_mode,
        "threads": threads,
    }


def parse_args_or_prompt(project_root, configs_dir, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    default_threads = load_last_threads(project_root) or str(multiprocessing.cpu_count() * 2)

    if len(argv) == 0:
        return collect_interactive_selection(project_root, configs_dir)

    if argv[0] in {"-h", "--help"}:
        print_help()
        sys.exit(0)

    if argv[0] in {"-c", "--current"}:
        if len(argv) < 2:
            print(f"{RED}用法: active-build -c <app|ota> [并行线程数]{RESET}")
            sys.exit(1)

        current_target = argv[1].lower()
        threads = argv[2] if len(argv) >= 3 else default_threads

        if current_target not in VALID_CURRENT_TARGET_ALIASES:
            print(f"{RED}无效当前配置编译指令: {current_target}{RESET}")
            sys.exit(1)
        if not threads.isdigit() or int(threads) <= 0:
            print(f"{RED}线程数必须是正整数: {threads}{RESET}")
            sys.exit(1)

        return {
            "action": "reuse_current",
            "target": VALID_CURRENT_TARGET_ALIASES[current_target],
            "threads": threads,
        }

    if len(argv) < 3:
        print(
            f"{RED}用法: active-build <芯片目录> <项目名称> <编译模式: release|debug|sim> [并行线程数]{RESET}"
        )
        print(f"{RED}或:   active-build -c <app|ota> [并行线程数]{RESET}")
        sys.exit(1)

    family = argv[0]
    project = argv[1]
    build_mode = argv[2].lower()
    threads = argv[3] if len(argv) >= 4 else str(multiprocessing.cpu_count() * 2)

    if family not in find_available_families(configs_dir):
        print(f"{RED}无效芯片目录: {family}{RESET}")
        sys.exit(1)
    if build_mode not in VALID_BUILD_MODES:
        print(f"{RED}无效编译模式: {build_mode}{RESET}")
        sys.exit(1)
    if not threads.isdigit() or int(threads) <= 0:
        print(f"{RED}线程数必须是正整数: {threads}{RESET}")
        sys.exit(1)

    return {
        "action": "full_build",
        "family": family,
        "project": project,
        "build_mode": build_mode,
        "threads": threads,
    }


def validate_selection(project_root, configs_dir, family, project, build_mode):
    defconfig_main = os.path.join(configs_dir, family, f"{family}_{project}_defconfig")
    if not os.path.exists(defconfig_main):
        print(f"{RED}主 defconfig 不存在: {defconfig_main}{RESET}")
        sys.exit(1)

    if build_mode == "sim":
        sim_candidates = [
            os.path.join(project_root, "configs", "simulator", f"simx86_{project}_defconfig"),
            os.path.join(project_root, "simulator", "configs", f"simx86_{project}_defconfig"),
        ]
        for sim_defconfig in sim_candidates:
            if os.path.exists(sim_defconfig):
                return defconfig_main, sim_defconfig

        print(f"{RED}未找到模拟器 defconfig，已检查以下路径:{RESET}")
        for sim_defconfig in sim_candidates:
            print(f"{RED}  - {sim_defconfig}{RESET}")
        sys.exit(1)

    sensorhub_defconfig = os.path.join(configs_dir, family, f"{family}_{project}_sensorhub_defconfig")
    if not os.path.exists(sensorhub_defconfig):
        print(f"{RED}未找到 sensorhub defconfig: {sensorhub_defconfig}{RESET}")
        sys.exit(1)

    product_path = resolve_product_path(project_root, family, project)
    if product_path is None:
        print(f"{RED}未找到产品目录: platform/board/{family}/products 下无匹配项 ({project}){RESET}")
        sys.exit(1)

    sensorhub_target_dir = os.path.join(product_path, "sensorhub")
    if not os.path.isdir(sensorhub_target_dir):
        print(f"{RED}未找到 sensorhub 目录: {sensorhub_target_dir}{RESET}")
        sys.exit(1)

    return defconfig_main, sensorhub_target_dir


def validate_current_build_config(build_dir):
    config_path = os.path.join(build_dir, ".config")
    if not os.path.exists(config_path):
        print(f"{RED}当前 build 目录下未找到 .config，无法延用当前配置编译{RESET}")
        sys.exit(1)
    return config_path


def build_hardware_commands(family, project, build_mode, out_hub_dir):
    build_type_arg = " BUILD_TYPE=release" if build_mode == "release" else ""
    commands = [
        "make distclean",
        "make clean",
        f"rm -rf {out_hub_dir}",
        f"make {family}_{project}_defconfig{build_type_arg}",
        f"make {family}_{project}_sensorhub_defconfig{build_type_arg} APPDIR={out_hub_dir}",
        f"make BUILD_DIR={out_hub_dir} APPDIR={out_hub_dir}",
    ]
    ota_cmd = (
        f"make BUILD_TYPE=release ota -j{{threads}}"
        if build_mode == "release"
        else f"make ota -j{{threads}}"
    )
    return commands, ota_cmd


def run(selection, start_dir, project_root, configs_dir, build_dir):
    ensure_python3_default()

    os.chdir(build_dir)
    threads = selection["threads"]

    print(f"\n{YELLOW}脚本启动目录: {start_dir}{RESET}")
    print(f"{YELLOW}项目根目录: {project_root}{RESET}")
    print(f"{YELLOW}实际编译目录: {build_dir}{RESET}")
    print(f"{YELLOW}编译线程数: {threads}{RESET}")

    total_start = time.time()
    backups = []
    modified_files = []
    old_versions = []

    try:
        if selection["action"] == "reuse_current":
            config_path = validate_current_build_config(build_dir)
            target = selection["target"]
            command = f"{target} -j{threads}" if target == "make" else f"make ota -j{threads}"

            print(f"{YELLOW}编译类型: 延用当前配置{RESET}")
            print(f"{YELLOW}当前配置文件: {config_path}{RESET}")
            print(f"{YELLOW}执行指令: {command}{RESET}")

            run_cmd(command)
            save_last_threads(project_root, threads)
        else:
            family = selection["family"]
            project = selection["project"]
            build_mode = selection["build_mode"]
            defconfig_main, build_target = validate_selection(
                project_root, configs_dir, family, project, build_mode
            )

            out_hub_dir = "out_hub"
            sensorhub_output_dir = os.path.join(
                build_dir, out_hub_dir, f"sensorhub@{family}", "binary"
            )

            print(f"{YELLOW}芯片目录: {family}{RESET}")
            print(f"{YELLOW}项目名称: {project}{RESET}")
            print(f"{YELLOW}编译模式: {build_mode}{RESET}")

            if not compare_repo_manifest(project_root, project):
                sys.exit(0)

            if build_mode != "sim" and os.path.exists(defconfig_main):
                print(f"\n{YELLOW}检查主 defconfig: {defconfig_main}{RESET}")
                backup_path, modified, old_version = modify_defconfig_version(defconfig_main)
                backups.append((backup_path, defconfig_main))
                if modified:
                    modified_files.append(defconfig_main)
                    old_versions.append(old_version)

            if not confirm_continue(modified_files, old_versions):
                sys.exit(0)

            if build_mode == "sim":
                sim_defconfig_name = os.path.basename(build_target)
                commands = [
                    "make distclean",
                    "make clean",
                    f"make {family}_{project}_defconfig BUILD_SIM={sim_defconfig_name}",
                    f"make ota -j{threads}",
                ]
                for cmd in commands:
                    run_cmd(cmd)
            else:
                sensorhub_target_dir = build_target
                commands, ota_cmd = build_hardware_commands(family, project, build_mode, out_hub_dir)
                for cmd in commands:
                    run_cmd(cmd)

                sensorhub_binary = resolve_sensorhub_binary(sensorhub_output_dir, family)
                if sensorhub_binary is None:
                    print(f"{RED}未找到可用的 sensorhub 二进制文件，编译终止{RESET}")
                    sys.exit(1)

                run_cmd(f"cp {sensorhub_binary} {sensorhub_target_dir}")
                run_cmd(ota_cmd.format(threads=threads))

            save_last_threads(project_root, threads)

    except Exception as error:
        print(f"\n{RED}编译过程中发生错误: {error}{RESET}")
        for backup_path, original_path in backups:
            restore_defconfig(backup_path, original_path)
        sys.exit(1)
    finally:
        print(f"\n{YELLOW}开始恢复 defconfig 文件...{RESET}")
        for backup_path, original_path in backups:
            restore_defconfig(backup_path, original_path)

        if modified_files:
            print(f"{GREEN}已恢复以下文件:{RESET}")
            for file_path in modified_files:
                print(f"  - {file_path}")
        else:
            print(f"{GREEN}没有文件需要恢复{RESET}")

    total_end = time.time()
    print(f"\n{GREEN}编译完成，总耗时 {total_end - total_start:.2f} 秒{RESET}")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    if argv and argv[0] in {"-h", "--help"}:
        print_help()
        sys.exit(0)

    start_dir, project_root, configs_dir, build_dir = locate_project_root()
    selection = parse_args_or_prompt(project_root, configs_dir, argv)
    run(selection, start_dir, project_root, configs_dir, build_dir)


if __name__ == "__main__":
    main()
