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
    (root / "platform" / "board" / "mhs003" / "products" / "cologne" / "sensorhub").mkdir(
        parents=True
    )
    (root / "platform" / "board" / "mhs003s" / "products" / "atlas" / "sensorhub").mkdir(
        parents=True
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_defconfig").write_text(
        'BOARD_FIRMWARE_VERSION="1.2.3"\n', encoding="utf-8"
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_sensorhub_defconfig").write_text(
        "# sensorhub\n", encoding="utf-8"
    )
    (root / "configs" / "mhs003" / "mhs003_cologne_boot_defconfig").write_text(
        "# derived\n", encoding="utf-8"
    )
    (root / "configs" / "mhs003s" / "mhs003s_atlas_defconfig").write_text(
        "# main\n", encoding="utf-8"
    )
    (root / "configs" / "mhs003s" / "mhs003s_atlas_sensorhub_defconfig").write_text(
        "# sensorhub\n", encoding="utf-8"
    )


class ActiveBuildCliTest(unittest.TestCase):
    def test_locate_project_root_from_subdir(self):
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

    def test_parse_current_build_reuses_last_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)
            (root / cli.LAST_THREADS_FILE).write_text("12", encoding="utf-8")

            selection = cli.parse_args_or_prompt(
                str(root), str(root / "configs"), ["--current", "ota"]
            )

            self.assertEqual(
                selection,
                {"action": "reuse_current", "target": "ota", "threads": "12"},
            )

    def test_parse_full_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            selection = cli.parse_args_or_prompt(
                str(root), str(root / "configs"), ["mhs003", "cologne", "debug", "8"]
            )

            self.assertEqual(selection["action"], "full_build")
            self.assertEqual(selection["family"], "mhs003")
            self.assertEqual(selection["project"], "cologne")
            self.assertEqual(selection["build_mode"], "debug")
            self.assertEqual(selection["threads"], "8")

    def test_list_projects_filters_derived_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root)

            projects = cli.list_projects(str(root), str(root / "configs"), "mhs003")

            self.assertEqual(projects, ["cologne"])

    def test_modify_and_restore_defconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            defconfig = Path(tmp) / "mhs003_cologne_defconfig"
            defconfig.write_text('BOARD_FIRMWARE_VERSION="1.2.3"\n', encoding="utf-8")

            backup_path, modified, old_version = cli.modify_defconfig_version(str(defconfig))

            self.assertTrue(modified)
            self.assertEqual(old_version, "1.2.3")
            self.assertIn('BOARD_FIRMWARE_VERSION="10.0.0"', defconfig.read_text(encoding="utf-8"))

            cli.restore_defconfig(backup_path, str(defconfig))
            self.assertEqual(
                defconfig.read_text(encoding="utf-8"),
                'BOARD_FIRMWARE_VERSION="1.2.3"\n',
            )

    def test_resolve_sensorhub_binary_prefers_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            signed = output_dir / "sensorhub@mhs003_sign.bin"
            unsigned = output_dir / "sensorhub@mhs003.bin"
            signed.write_bytes(b"signed")
            unsigned.write_bytes(b"unsigned")

            self.assertEqual(cli.resolve_sensorhub_binary(str(output_dir), "mhs003"), str(signed))

    def test_resolve_sensorhub_binary_unsigned_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            unsigned = output_dir / "sensorhub@mhs003.bin"
            unsigned.write_bytes(b"unsigned")

            with mock.patch.object(cli, "confirm_fallback_unsigned_bin", return_value=True):
                self.assertEqual(
                    cli.resolve_sensorhub_binary(str(output_dir), "mhs003"),
                    str(unsigned),
                )

    def test_build_hardware_commands(self):
        commands, ota_cmd = cli.build_hardware_commands("mhs003", "cologne", "release", "out_hub")

        self.assertIn("make distclean", commands)
        self.assertIn("make mhs003_cologne_defconfig BUILD_TYPE=release", commands)
        self.assertEqual(ota_cmd.format(threads="16"), "make BUILD_TYPE=release ota -j16")


if __name__ == "__main__":
    unittest.main()
