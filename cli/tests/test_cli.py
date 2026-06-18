import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from active_build import cli


def make_workspace(root: Path) -> None:
    (root / "build").mkdir()
    (root / "configs" / "mhs003").mkdir(parents=True)
    (root / "configs" / "mhs003s").mkdir(parents=True)
    (root / "configs" / "futurechip").mkdir(parents=True)
    (root / "platform" / "board" / "mhs003" / "products" / "cologne" / "sensorhub").mkdir(
        parents=True
    )
    (root / "platform" / "board" / "mhs003s" / "products" / "atlas" / "sensorhub").mkdir(
        parents=True
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_defconfig").write_text(
        "# main\n"
        "AMOLED_PANEL_WIDTH=390\n"
        "AMOLED_PANEL_HEIGHT=450\n"
        "STORYBOARD_DISPLAY_WIDTH=466\n"
        "STORYBOARD_DISPLAY_HEIGHT=466\n"
        'HM_DISPLAY_DENSTIY="0.9708"\n'
        'HM_FONT_DENSTIY="1.09"\n',
        encoding="utf-8",
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_sensorhub_defconfig").write_text(
        "# sensorhub\n", encoding="utf-8"
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_boot_defconfig").write_text(
        "# derived\n", encoding="utf-8"
    )
    (root / "configs" / "mhs003s" / "mhs003s_atlas_defconfig").write_text(
        "# main\n"
        "AMOLED_PANEL_WIDTH=480\n"
        "AMOLED_PANEL_HEIGHT=480\n"
        "STORYBOARD_DISPLAY_WIDTH=480\n"
        "STORYBOARD_DISPLAY_HEIGHT=480\n"
        'HM_FONT_DENSTIY="1"\n',
        encoding="utf-8",
    )
    (root / "configs" / "mhs003s" / "mhs003s_atlas_sensorhub_defconfig").write_text(
        "# sensorhub\n", encoding="utf-8"
    )


def enable_build_fw_ver(root: Path) -> None:
    rules_dir = root / "build" / "build_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "fw_version.mk").write_text("# test marker\n", encoding="utf-8")


class ActiveBuildCliTest(unittest.TestCase):
    def test_locate_project_root_from_subdir_and_build_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            subdir = root / "ui" / "Sports"
            subdir.mkdir(parents=True)

            start, project_root, configs_dir, build_dir = cli.locate_project_root(str(subdir))

            self.assertEqual(start, str(subdir))
            self.assertEqual(project_root, str(root))
            self.assertEqual(configs_dir, str(root / "configs"))
            self.assertEqual(build_dir, str(root / "build"))

            _, project_root, _, build_dir = cli.locate_project_root(str(subdir), str(root / "build"))
            self.assertEqual(project_root, str(root))
            self.assertEqual(build_dir, str(root / "build"))

    def test_legacy_positional_full_build_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            with self.assertRaises(SystemExit) as ctx:
                cli.parse_args_or_prompt(
                    str(root), str(root / "configs"), ["mhs003", "cologne", "debug", "8"]
                )

            self.assertEqual(ctx.exception.code, 1)

    def test_parse_lowercase_short_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-f", "mhs003", "-p", "cologne", "-m", "debug", "-j", "8"],
            )

            self.assertEqual(plan.family, "mhs003")
            self.assertEqual(plan.project, "cologne")
            self.assertEqual(plan.mode, "sensorhub-ota")
            self.assertIsNone(plan.build_type)
            self.assertEqual(plan.threads, "8")
            self.assertEqual(plan.version, "10.0.0")
            self.assertFalse(plan.version_explicit)
            self.assertTrue(plan.reload_defconfig)

    def test_parse_uppercase_short_args_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-F", "mhs003s", "-P", "atlas", "-M", "sensorhub", "-J", "6", "-D", "-V", "1.2.3"],
            )

            self.assertEqual(plan.family, "mhs003s")
            self.assertEqual(plan.project, "atlas")
            self.assertEqual(plan.mode, "sensorhub")
            self.assertEqual(plan.threads, "6")
            self.assertEqual(plan.version, "1.2.3")
            self.assertTrue(plan.version_explicit)
            self.assertTrue(plan.reload_defconfig)

    def test_new_workspace_uses_build_fw_ver_default_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            enable_build_fw_ver(root)

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-f", "mhs003", "-p", "cologne", "-m", "ota"],
            )
            self.assertEqual(plan.version, "99.9")
            self.assertFalse(plan.version_explicit)

            for version in ("23.4", "6.1.23.4"):
                plan = cli.parse_args_or_prompt(
                    str(root),
                    str(root / "configs"),
                    ["-f", "mhs003", "-p", "cologne", "-m", "ota", "-v", version],
                )
                self.assertEqual(plan.version, version)
                self.assertTrue(plan.version_explicit)

            with self.assertRaises(SystemExit):
                cli.parse_args_or_prompt(
                    str(root),
                    str(root / "configs"),
                    ["-f", "mhs003", "-p", "cologne", "-m", "ota", "-v", "10.0.0"],
                )

    def test_parse_current_build_modes_and_split_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            (root / "build" / cli.STATE_FILE).write_text(
                json.dumps({"family": "mhs003", "project": "cologne", "threads": "12"}),
                encoding="utf-8",
            )

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-c", "app"],
            )
            self.assertTrue(plan.use_current_config)
            self.assertEqual(plan.mode, "firmware")
            self.assertEqual(plan.threads, "12")
            self.assertFalse(plan.version_explicit)

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-c", "sensorhub-ota", "-a", "debug", "-u", "release"],
            )
            self.assertEqual(plan.mode, "sensorhub-ota")
            self.assertEqual(plan.main_build_type, "debug")
            self.assertEqual(plan.sensorhub_build_type, "release")

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-c", "fw", "-v", "4.0.0"],
            )
            self.assertTrue(plan.use_current_config)
            self.assertEqual(plan.mode, "firmware")
            self.assertEqual(plan.version, "4.0.0")
            self.assertTrue(plan.version_explicit)

    def test_load_last_threads_keeps_legacy_file_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            (root / cli.LEGACY_LAST_THREADS_FILE).write_text("10", encoding="utf-8")

            self.assertEqual(cli.load_last_threads(str(root)), "10")

    def test_build_plan_json_and_mixed_args_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            plan_path = root / "build-plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "action": "build",
                        "family": "mhs003s",
                        "project": "atlas",
                        "mode": "sensorhub-ota",
                        "threads": 8,
                        "reload_defconfig": True,
                        "version": "2.0.0",
                        "build_type": "release",
                    }
                ),
                encoding="utf-8",
            )

            plan = cli.parse_args_or_prompt(
                str(root), str(root / "configs"), ["-i", str(plan_path)], cwd=str(root)
            )
            self.assertEqual(plan.family, "mhs003s")
            self.assertEqual(plan.project, "atlas")
            self.assertEqual(plan.mode, "sensorhub-ota")
            self.assertEqual(plan.threads, "8")
            self.assertEqual(plan.version, "2.0.0")
            self.assertTrue(plan.version_explicit)

            plan = cli.parse_args_or_prompt(
                str(root),
                str(root / "configs"),
                ["-i", str(plan_path), "-w", str(root / "build")],
                cwd=str(root),
            )
            self.assertEqual(plan.workspace, str(root / "build"))

            with self.assertRaises(SystemExit):
                cli.parse_args_or_prompt(
                    str(root),
                    str(root / "configs"),
                    ["-i", str(plan_path), "-f", "mhs003"],
                    cwd=str(root),
                )

    def test_bstylenc_args_and_json_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            style = root / "ui" / "Sports" / "prototype" / "style" / "466x466-mdpi" / "Foo.style"
            style.parent.mkdir(parents=True)
            style.write_text("<style />\n", encoding="utf-8")

            plan = cli.parse_bstylenc_args(
                ["-f", "mhs003", "-p", "cologne", "-i", str(style), "--dry-run"]
            )
            self.assertEqual(plan.action, "bstylenc")
            self.assertEqual(plan.family, "mhs003")
            self.assertEqual(plan.project, "cologne")
            self.assertEqual(plan.input, str(style))
            self.assertTrue(plan.dry_run)

            plan_path = root / "bstylenc-plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "action": "bstylenc",
                        "input": str(style),
                        "width": 320,
                        "height": 380,
                        "pixel_ratio": "1.0",
                        "dry_run": True,
                    }
                ),
                encoding="utf-8",
            )
            plan = cli.plan_from_json(str(plan_path), str(root))
            self.assertEqual(plan.width, "320")
            self.assertEqual(plan.height, "380")
            self.assertEqual(plan.pixel_ratio, "1.0")

    def test_main_accepts_bstyle_and_bstylenc_subcommands(self):
        for subcommand in ("bstyle", "bstylenc"):
            with self.subTest(subcommand=subcommand), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                make_workspace(root)
                style = root / "Foo.style"
                style.write_text("<style />\n", encoding="utf-8")
                tool = root / "build" / "cmd" / "linux64" / "bstylenc"
                tool.parent.mkdir(parents=True)
                tool.write_text("#!/bin/sh\n", encoding="utf-8")

                with mock.patch.object(cli.platform, "architecture", return_value=("64bit", "")):
                    cli.main(
                        [
                            subcommand,
                            "-w",
                            str(root),
                            "-f",
                            "mhs003",
                            "-p",
                            "cologne",
                            "-i",
                            str(style),
                            "--dry-run",
                        ]
                    )

    def test_run_bstylenc_plan_infers_context_and_default_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            style = root / "Foo.style"
            style.write_text("<style />\n", encoding="utf-8")
            tool = root / "build" / "cmd" / "linux64" / "bstylenc"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "build" / cli.STATE_FILE).write_text(
                json.dumps({"family": "mhs003", "project": "cologne"}),
                encoding="utf-8",
            )
            plan = cli.BstylencPlan(input=str(style), dry_run=True)

            with mock.patch.object(cli.platform, "architecture", return_value=("64bit", "")):
                cli.run_bstylenc_plan(
                    cli.normalize_bstylenc_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(root / "build"),
                )

            self.assertEqual(plan.family, "mhs003")
            self.assertEqual(plan.project, "cologne")
            self.assertEqual(plan.output, str(root / "Foo.bstyle"))
            self.assertEqual(plan.width, "466")
            self.assertEqual(plan.height, "466")
            self.assertEqual(plan.pixel_ratio, "1.09")

    def test_run_bstylenc_plan_resolves_relative_paths_from_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            style = root / "ui" / "Sports" / "prototype" / "style" / "466x466-mdpi" / "Foo.style"
            style.parent.mkdir(parents=True)
            style.write_text("<style />\n", encoding="utf-8")
            tool = root / "build" / "cmd" / "linux64" / "bstylenc"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\n", encoding="utf-8")

            plan = cli.BstylencPlan(
                input="ui/Sports/prototype/style/466x466-mdpi/Foo.style",
                width="466",
                height="466",
                pixel_ratio="1.0",
                dry_run=True,
            )

            with mock.patch.object(cli.platform, "architecture", return_value=("64bit", "")):
                cli.run_bstylenc_plan(
                    cli.normalize_bstylenc_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(root / "build"),
                )

            self.assertEqual(plan.input, str(style))
            self.assertEqual(plan.output, str(style.with_suffix(".bstyle")))

    def test_run_bstylenc_plan_rejects_input_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mod"
            other = Path(tmp) / "geneva"
            root.mkdir()
            other.mkdir()
            make_workspace(root)
            make_workspace(other)
            style = other / "ui" / "Sports" / "prototype" / "style" / "466x466-mdpi" / "Foo.style"
            style.parent.mkdir(parents=True)
            style.write_text("<style />\n", encoding="utf-8")
            tool = root / "build" / "cmd" / "linux64" / "bstylenc"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\n", encoding="utf-8")

            plan = cli.BstylencPlan(input=str(style), dry_run=True)

            with self.assertRaises(SystemExit):
                cli.run_bstylenc_plan(
                    cli.normalize_bstylenc_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(root / "build"),
                )

    def test_run_bstylenc_plan_uses_json_dimensions_without_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            style = root / "Foo.style"
            style.write_text("<style />\n", encoding="utf-8")
            tool = root / "build" / "cmd" / "linux64" / "bstylenc"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            plan = cli.BstylencPlan(
                input=str(style),
                width="320",
                height="380",
                pixel_ratio="1.0",
                dry_run=True,
            )

            with mock.patch.object(cli.platform, "architecture", return_value=("64bit", "")):
                cli.run_bstylenc_plan(
                    cli.normalize_bstylenc_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(root / "build"),
                )

            self.assertIsNone(plan.family)
            self.assertIsNone(plan.project)
            self.assertEqual(plan.output, str(root / "Foo.bstyle"))
            self.assertEqual(
                cli.make_bstylenc_cmd(str(tool), plan),
                f"{tool} -i {style} -o {root / 'Foo.bstyle'} -w 320 -h 380 -p 1.0",
            )

    def test_list_projects_filters_derived_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            projects = cli.list_projects(str(root), str(root / "configs"), "mhs003")

            self.assertEqual(projects, ["cologne"])

    def test_find_available_families_only_exposes_enabled_chips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            families = cli.find_available_families(str(root / "configs"))

            self.assertEqual(families, ["mhs003", "mhs003s"])

    def test_patch_config_version_replaces_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".config"
            config.write_text('A=1\nBOARD_FIRMWARE_VERSION="1.2.3"\n', encoding="utf-8")

            cli.patch_config_version(str(config), "10.0.0")
            self.assertIn('BOARD_FIRMWARE_VERSION="10.0.0"', config.read_text(encoding="utf-8"))

            config.write_text("A=1\n", encoding="utf-8")
            cli.patch_config_version(str(config), "2.0.0")
            self.assertTrue(config.read_text(encoding="utf-8").endswith('BOARD_FIRMWARE_VERSION="2.0.0"\n'))

    def test_make_cmd_only_injects_release_build_type(self):
        plan = cli.BuildPlan(build_type="release", main_build_type="debug", sensorhub_build_type="inspect")

        self.assertEqual(
            cli.make_cmd(plan, "ota", stage="main", threads="4"),
            "make ota -j4",
        )
        self.assertEqual(
            cli.make_cmd(plan, stage="sensorhub", appdir="out_hub", build_dir_var="out_hub"),
            "make BUILD_DIR=out_hub APPDIR=out_hub",
        )
        self.assertEqual(
            cli.make_cmd(cli.BuildPlan(build_type="release"), "ota", stage="main", threads="4"),
            "make ota BUILD_TYPE=release -j4",
        )
        self.assertEqual(cli.make_cmd(plan, "clean", stage="main"), "make clean")

    def test_make_cmd_derives_ota_version_strategy_from_version_segments(self):
        two_part = cli.BuildPlan(version="23.4")
        setattr(two_part, "_use_build_fw_ver", True)
        self.assertEqual(
            cli.make_cmd(two_part, "ota", stage="main", threads="8"),
            "make ota FW_VER_STRATEGY=os_global BUILD_FW_VER=23.4 -j8",
        )
        self.assertEqual(
            cli.make_cmd(two_part, stage="main", threads="8"),
            "make -j8",
        )

        four_part = cli.BuildPlan(version="6.1.23.4")
        setattr(four_part, "_use_build_fw_ver", True)
        self.assertEqual(
            cli.make_cmd(four_part, "ota", stage="main", threads="8"),
            "make ota BUILD_FW_VER=6.1.23.4 -j8",
        )

    def test_project_switch_requires_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            state = root / "build" / cli.STATE_FILE
            state.write_text(json.dumps({"family": "mhs003", "project": "cologne"}), encoding="utf-8")

            plan = cli.BuildPlan(family="mhs003s", project="atlas", reload_defconfig=False)
            with self.assertRaises(SystemExit):
                cli.check_project_switch_requires_reload(str(root / "build"), plan)

            plan.reload_defconfig = True
            cli.check_project_switch_requires_reload(str(root / "build"), plan)

    def test_copy_sensorhub_outputs_copies_all_known_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "build" / "out_hub" / "sensorhub@mhs003" / "binary"
            dst = root / "target"
            src.mkdir(parents=True)
            dst.mkdir()
            for suffix in ["_sign.bin", ".bin", ".elf", ".map", ".hex"]:
                (src / f"sensorhub@mhs003{suffix}").write_bytes(suffix.encode())

            copied = cli.copy_sensorhub_outputs(str(root / "build"), "mhs003", str(dst))

            self.assertEqual(len(copied), 5)
            self.assertTrue((dst / "sensorhub@mhs003_sign.bin").exists())
            self.assertTrue((dst / "sensorhub@mhs003.hex").exists())

    def test_copy_sensorhub_outputs_fails_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build" / "out_hub" / "sensorhub@mhs003" / "binary").mkdir(parents=True)
            (root / "target").mkdir()

            with self.assertRaises(SystemExit):
                cli.copy_sensorhub_outputs(str(root / "build"), "mhs003", str(root / "target"))

    def test_run_build_plan_sensorhub_ota_command_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            build_dir = root / "build"
            (root / cli.LEGACY_LAST_THREADS_FILE).write_text("4", encoding="utf-8")
            commands = []

            def fake_run_cmd(command, logger=None):
                commands.append(command)
                if "mhs003_cologne_defconfig" in command:
                    (build_dir / ".config").write_text(
                        'HMI_BUILD_BOARD="mhs003"\nHMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n',
                        encoding="utf-8",
                    )
                if "mhs003_cologne_sensorhub_defconfig" in command:
                    (build_dir / "out_hub").mkdir(exist_ok=True)
                    (build_dir / "out_hub" / ".config").write_text(
                        'HMI_BUILD_BOARD="mhs003"\nHMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n',
                        encoding="utf-8",
                    )
                if "BUILD_DIR=out_hub" in command and "silentoldconfig" not in command:
                    out = build_dir / "out_hub" / "sensorhub@mhs003" / "binary"
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "sensorhub@mhs003_sign.bin").write_bytes(b"signed")

            plan = cli.BuildPlan(
                family="mhs003",
                project="cologne",
                mode="sensorhub-ota",
                threads="8",
                reload_defconfig=True,
                version="10.0.0",
                version_explicit=True,
                main_build_type="debug",
                sensorhub_build_type="release",
            )

            with mock.patch.object(cli, "ensure_python3_default"), mock.patch.object(
                cli, "compare_repo_manifest", return_value=True
            ), mock.patch.object(cli, "run_cmd", side_effect=fake_run_cmd):
                cli.run_build_plan(
                    cli.normalize_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(build_dir),
                )

            self.assertIn("make distclean", commands)
            self.assertIn("make mhs003_cologne_sensorhub_defconfig BUILD_TYPE=release APPDIR=out_hub", commands)
            self.assertIn(
                "make BUILD_TYPE=release BUILD_DIR=out_hub APPDIR=out_hub",
                commands,
            )
            self.assertLess(
                commands.index("make BUILD_TYPE=release BUILD_DIR=out_hub APPDIR=out_hub"),
                commands.index("make silentoldconfig"),
            )
            self.assertEqual(
                [command for command in commands if "silentoldconfig" in command],
                ["make silentoldconfig"],
            )
            self.assertLess(
                commands.index("make silentoldconfig"),
                commands.index("make ota -j8"),
            )
            self.assertIn("make ota -j8", commands)
            self.assertIn(
                'BOARD_FIRMWARE_VERSION="10.0.0"',
                (build_dir / ".config").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "BOARD_FIRMWARE_VERSION",
                (build_dir / "out_hub" / ".config").read_text(encoding="utf-8"),
            )
            self.assertTrue((root / "platform" / "board" / "mhs003" / "products" / "cologne" / "sensorhub" / "sensorhub@mhs003_sign.bin").exists())
            self.assertFalse((root / cli.LEGACY_LAST_THREADS_FILE).exists())
            state = json.loads((build_dir / cli.STATE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(state["threads"], "8")

    def test_run_build_plan_sensorhub_single_explicit_version_overrides_out_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            build_dir = root / "build"
            commands = []

            def fake_run_cmd(command, logger=None):
                commands.append(command)
                if "mhs003_cologne_defconfig" in command:
                    (build_dir / ".config").write_text(
                        'HMI_BUILD_BOARD="mhs003"\nHMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n',
                        encoding="utf-8",
                    )
                if "mhs003_cologne_sensorhub_defconfig" in command:
                    (build_dir / "out_hub").mkdir(exist_ok=True)
                    (build_dir / "out_hub" / ".config").write_text(
                        'HMI_BUILD_BOARD="mhs003"\nHMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n',
                        encoding="utf-8",
                    )
                if "BUILD_DIR=out_hub" in command and "silentoldconfig" not in command:
                    out = build_dir / "out_hub" / "sensorhub@mhs003" / "binary"
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "sensorhub@mhs003_sign.bin").write_bytes(b"signed")

            plan = cli.BuildPlan(
                family="mhs003",
                project="cologne",
                mode="sensorhub",
                threads="8",
                reload_defconfig=True,
                version="3.0.0",
                version_explicit=True,
            )

            with mock.patch.object(cli, "ensure_python3_default"), mock.patch.object(
                cli, "compare_repo_manifest", return_value=True
            ), mock.patch.object(cli, "run_cmd", side_effect=fake_run_cmd):
                cli.run_build_plan(
                    cli.normalize_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(build_dir),
                )

            self.assertEqual(
                [command for command in commands if "silentoldconfig" in command],
                ["make silentoldconfig APPDIR=out_hub"],
            )
            self.assertIn(
                'BOARD_FIRMWARE_VERSION="3.0.0"',
                (build_dir / "out_hub" / ".config").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "BOARD_FIRMWARE_VERSION",
                (build_dir / ".config").read_text(encoding="utf-8"),
            )

    def test_compare_repo_manifest_accepts_repo_generated_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            manifests = root / ".repo" / "manifests"
            manifests.mkdir(parents=True)
            (root / ".repo" / "manifest.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<manifest>\n"
                '  <include name="cologne.xml" />\n'
                "</manifest>\n",
                encoding="utf-8",
            )
            (manifests / "huamiOS.xml").write_text("<manifest />\n", encoding="utf-8")
            (manifests / "cologne.xml").write_text(
                '<manifest><project name="cologne" /></manifest>\n',
                encoding="utf-8",
            )

            with mock.patch.object(cli, "confirm_manifest_choice", return_value=True) as confirm:
                self.assertTrue(cli.compare_repo_manifest(str(root), "cologne"))

            confirm.assert_called_once_with(False)

    def test_compare_repo_manifest_accepts_legacy_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            repo = root / ".repo"
            manifests = repo / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "huamiOS.xml").write_text("<manifest />\n", encoding="utf-8")
            (manifests / "cologne.xml").write_text(
                '<manifest><project name="cologne" /></manifest>\n',
                encoding="utf-8",
            )
            (repo / "manifest.xml").symlink_to(manifests / "cologne.xml")

            with mock.patch.object(cli, "confirm_manifest_choice", return_value=True) as confirm:
                self.assertTrue(cli.compare_repo_manifest(str(root), "cologne"))

            confirm.assert_called_once_with(False)

    def test_compare_repo_manifest_flags_different_repo_generated_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            manifests = root / ".repo" / "manifests"
            manifests.mkdir(parents=True)
            (root / ".repo" / "manifest.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<manifest>\n"
                '  <include name="atlas.xml" />\n'
                "</manifest>\n",
                encoding="utf-8",
            )
            (manifests / "huamiOS.xml").write_text("<manifest />\n", encoding="utf-8")
            (manifests / "cologne.xml").write_text(
                '<manifest><project name="cologne" /></manifest>\n',
                encoding="utf-8",
            )

            with mock.patch.object(cli, "confirm_manifest_choice", return_value=True) as confirm:
                self.assertTrue(cli.compare_repo_manifest(str(root), "cologne"))

            confirm.assert_called_once_with(True)

    def test_run_new_workspace_uses_default_build_fw_ver_on_ota_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            enable_build_fw_ver(root)
            build_dir = root / "build"
            commands = []

            def fake_run_cmd(command, logger=None):
                commands.append(command)
                if "mhs003_cologne_defconfig" in command:
                    (build_dir / ".config").write_text(
                        "HMI_BUILD_BOARD=\"mhs003\"\nHMI_PRODUCT_CUSTOMIZE_DIR=\"cologne\"\n",
                        encoding="utf-8",
                    )

            plan = cli.BuildPlan(
                family="mhs003",
                project="cologne",
                mode="ota",
                threads="8",
                reload_defconfig=True,
            )

            with mock.patch.object(cli, "ensure_python3_default"), mock.patch.object(
                cli, "compare_repo_manifest", return_value=True
            ), mock.patch.object(cli, "run_cmd", side_effect=fake_run_cmd):
                cli.run_build_plan(
                    cli.normalize_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(build_dir),
                )

            self.assertEqual(plan.version, "99.9")
            self.assertIn("make mhs003_cologne_defconfig", commands)
            self.assertNotIn("make mhs003_cologne_defconfig BUILD_FW_VER=99.9", commands)
            self.assertIn("make ota FW_VER_STRATEGY=os_global BUILD_FW_VER=99.9 -j8", commands)
            self.assertNotIn("make silentoldconfig", commands)
            self.assertNotIn(
                "BOARD_FIRMWARE_VERSION",
                (build_dir / ".config").read_text(encoding="utf-8"),
            )

    def test_run_current_config_firmware_skips_version_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            build_dir = root / "build"
            config_text = (
                'HMI_BUILD_BOARD="mhs003"\n'
                'HMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n'
                'BOARD_FIRMWARE_VERSION="1.2.3"\n'
            )
            (build_dir / ".config").write_text(config_text, encoding="utf-8")
            commands = []

            def fake_run_cmd(command, logger=None):
                commands.append(command)

            plan = cli.BuildPlan(
                mode="fw",
                threads="8",
                use_current_config=True,
                version="10.0.0",
            )

            with mock.patch.object(cli, "ensure_python3_default"), mock.patch.object(
                cli, "run_cmd", side_effect=fake_run_cmd
            ):
                cli.run_build_plan(
                    cli.normalize_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(build_dir),
                )

            self.assertEqual(commands, ["make -j8"])
            self.assertEqual((build_dir / ".config").read_text(encoding="utf-8"), config_text)

    def test_run_current_config_firmware_explicit_version_overrides_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            build_dir = root / "build"
            (build_dir / ".config").write_text(
                'HMI_BUILD_BOARD="mhs003"\n'
                'HMI_PRODUCT_CUSTOMIZE_DIR="cologne"\n'
                'BOARD_FIRMWARE_VERSION="1.2.3"\n',
                encoding="utf-8",
            )
            commands = []

            def fake_run_cmd(command, logger=None):
                commands.append(command)

            plan = cli.BuildPlan(
                mode="fw",
                threads="8",
                use_current_config=True,
                version="10.0.0",
                version_explicit=True,
            )

            with mock.patch.object(cli, "ensure_python3_default"), mock.patch.object(
                cli, "run_cmd", side_effect=fake_run_cmd
            ):
                cli.run_build_plan(
                    cli.normalize_plan(plan),
                    str(root),
                    str(root),
                    str(root / "configs"),
                    str(build_dir),
                )

            self.assertEqual(commands, ["make silentoldconfig", "make -j8"])
            self.assertIn(
                'BOARD_FIRMWARE_VERSION="10.0.0"',
                (build_dir / ".config").read_text(encoding="utf-8"),
            )

    def test_interactive_quick_and_advanced_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            quick_inputs = iter(["1", "1", "1", "2", "4"])
            with mock.patch("builtins.input", side_effect=lambda _: next(quick_inputs)):
                plan = cli.collect_interactive_plan(str(root), str(root / "configs"))
            self.assertEqual(plan.family, "mhs003")
            self.assertEqual(plan.project, "cologne")
            self.assertEqual(plan.mode, "sensorhub-ota")
            self.assertIsNone(plan.build_type)
            self.assertTrue(plan.reload_defconfig)

            advanced_inputs = iter(["1", "1", "2", "3", "y", "n", "3.0.0", "4", "2", "5", "y", "6"])
            with mock.patch("builtins.input", side_effect=lambda _: next(advanced_inputs)):
                plan = cli.collect_interactive_plan(str(root), str(root / "configs"))
            self.assertEqual(plan.mode, "sensorhub")
            self.assertTrue(plan.reload_defconfig)
            self.assertEqual(plan.version, "3.0.0")
            self.assertTrue(plan.version_explicit)
            self.assertEqual(plan.build_type, "release_log")
            self.assertEqual(plan.main_build_type, "debug")
            self.assertEqual(plan.sensorhub_build_type, "release")
            self.assertTrue(plan.log)

            style = root / "Foo.style"
            style.write_text("<style />\n", encoding="utf-8")
            bstyle_inputs = iter(["1", "1", "3", str(style), "n"])
            with mock.patch("builtins.input", side_effect=lambda _: next(bstyle_inputs)):
                plan = cli.collect_interactive_plan(str(root), str(root / "configs"))
            self.assertIsInstance(plan, cli.BstylencPlan)
            self.assertEqual(plan.family, "mhs003")
            self.assertEqual(plan.project, "cologne")
            self.assertEqual(plan.input, str(style))
            self.assertEqual(plan.output, str(root / "Foo.bstyle"))


if __name__ == "__main__":
    unittest.main()
