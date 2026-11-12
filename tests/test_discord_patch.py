import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discord_patch as app


class InstallerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="discord-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = app.Paths(
            self.root / "config", self.root / "data", self.root / "state"
        )
        self.runtime = self.paths.config / "discord/app-1.0.168"
        self.resources = self.runtime / "resources"
        self.resources.mkdir(parents=True)
        self.build_info = self.resources / "build_info.json"
        self.build_info.write_text(
            json.dumps({"version": "1.0.168", "releaseChannel": "stable"})
        )
        self.binary = self.runtime / "Discord"
        self.binary.write_bytes(b"fixture")
        self.binary.chmod(0o755)
        self.pointer = self.runtime.parent / "Discord"
        self.pointer.symlink_to(self.binary)
        self.stock = self.resources / "app.asar"
        self.renamed = self.resources / "betterdiscord.app.asar"
        self.stock.write_bytes(b"Discord archive")
        self.enterContext(
            patch.dict(
                app.SUPPORTED_BUILDS, {"1.0.168": app.sha256(b"Discord archive")}
            )
        )
        self.loader = self.resources / "app/index.js"
        self.desktop = self.paths.data / "applications/discord-arch-patched.desktop"
        self.plugin = self.paths.config / "BetterDiscord/plugins/YABDP4Nitro.plugin.js"
        self.preset = self.plugin.with_name("YABDP4Nitro.config.json")
        self.plugin_state = self.paths.config / "BetterDiscord/data/stable/plugins.json"
        self.network = self.enterContext(
            patch.object(app, "download", return_value=b"payload")
        )
        self.enterContext(
            patch.object(
                app,
                "build_betterdiscord",
                side_effect=lambda: app.download(app.BD_SOURCE, app.BD_SOURCE_SHA256),
            )
        )
        self.enterContext(patch.object(app, "ensure_discord_closed"))

    def test_install_repeatedly_and_uninstall(self):
        app.install(self.paths, with_yabd4=False)
        first = self.loader.read_bytes()
        self.assertTrue(self.desktop.is_file())
        self.assertEqual(self.renamed.read_bytes(), b"Discord archive")
        self.assertFalse(self.stock.exists())
        self.assertFalse(self.plugin.exists())
        self.assertTrue(app.patched_runtime_ready(self.paths))
        app.install(self.paths, with_yabd4=False)
        self.assertEqual(self.loader.read_bytes(), first)
        app.uninstall(self.paths)
        self.assertFalse(self.loader.exists())
        self.assertFalse(self.desktop.exists())
        self.assertTrue(self.stock.is_file())
        self.assertFalse(self.renamed.exists())
        self.assertTrue(self.pointer.is_symlink())

    def test_opt_in_creates_only_stream_preset_and_preserves_existing_config(self):
        app.install(self.paths, with_yabd4=True)
        self.assertEqual(self.plugin.read_bytes(), b"payload")
        self.assertEqual(
            json.loads(self.plugin_state.read_bytes()), {"YABDP4Nitro": True}
        )
        preset = json.loads(self.preset.read_text())["settings"]
        self.assertEqual(preset["CustomFPS"], 60)
        self.assertEqual(preset["CustomResolution"], 1080)
        self.assertEqual(preset["videoCodec2"], 2)
        self.assertFalse(preset["CustomBitrateEnabled"])
        self.assertNotIn("oAuthToken", preset)
        changed = b'{"settings":{"CustomFPS":30}}'
        self.preset.write_bytes(changed)
        app.install(self.paths, with_yabd4=True)
        app.uninstall(self.paths)
        self.assertEqual(self.preset.read_bytes(), changed)
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.plugin_state.exists())

    def test_uninstall_preserves_loader_dependencies_if_loader_was_edited(self):
        app.install(self.paths, with_yabd4=False)
        self.loader.write_bytes(self.loader.read_bytes() + b"// local edit\n")
        app.uninstall(self.paths)
        self.assertIn(b"// local edit", self.loader.read_bytes())
        self.assertTrue(self.renamed.is_file())
        self.assertTrue(self.paths.asar.is_file())
        self.assertTrue(self.loader.with_name("package.json").is_file())

    def test_existing_plugin_is_preserved(self):
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_bytes(b"user plugin")
        self.preset.write_bytes(b"user preset")
        app.install(self.paths, with_yabd4=True)
        app.uninstall(self.paths)
        self.assertEqual(self.plugin.read_bytes(), b"user plugin")
        self.assertEqual(self.preset.read_bytes(), b"user preset")

    def test_download_error_keeps_existing_discord(self):
        self.network.side_effect = app.SetupError("download failed")
        with self.assertRaisesRegex(app.SetupError, "download failed"):
            app.install(self.paths, with_yabd4=False)
        self.assertFalse(self.loader.exists())
        self.assertFalse(self.desktop.exists())
        self.assertEqual(self.stock.read_bytes(), b"Discord archive")

    def test_failed_write_rolls_back_and_install_can_retry(self):
        write = app.write_file

        def fail_on_desktop(path, data, mode=0o644):
            if path == self.desktop:
                raise OSError("write failed")
            return write(path, data, mode)

        with (
            patch.object(app, "write_file", side_effect=fail_on_desktop),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            app.install(self.paths, with_yabd4=False)
        self.assertFalse(self.loader.exists())
        self.assertTrue(self.stock.is_file())
        app.install(self.paths, with_yabd4=False)
        self.assertTrue(app.patched_runtime_ready(self.paths))

    def test_migrates_known_dual_injection_and_uninstall_restores_it(self):
        renamed = self.resources / "betterdiscord.app.asar"
        self.stock.rename(renamed)
        self.loader.parent.mkdir()
        self.loader.with_name("package.json").write_text(
            '{"name":"discord","main":"index.js"}'
        )
        legacy = self.paths.config / "BetterDiscord/data/betterdiscord.asar"
        old_app = f'require({json.dumps(str(legacy) + "/main.js")});\nrequire("../betterdiscord.app.asar");\n'
        self.loader.write_text(old_app)
        core = (
            self.runtime
            / "modules/discord_desktop_core-1/discord_desktop_core/index.js"
        )
        core.parent.mkdir(parents=True)
        old_core = f"require({json.dumps(str(legacy))});\nmodule.exports = require('./core.asar');\n"
        core.write_text(old_core)
        app.install(self.paths, with_yabd4=False)
        self.assertNotIn("BetterDiscord/data", self.loader.read_text())
        self.assertIn("betterdiscord.app.asar", self.loader.read_text())
        self.assertNotIn("BetterDiscord", core.read_text())
        app.uninstall(self.paths)
        self.assertEqual(core.read_text(), old_core)
        self.assertEqual(self.loader.read_text(), old_app)
        self.assertTrue(renamed.is_file())

    def test_redirects_known_legacy_desktop_entry_and_restores_it_on_uninstall(self):
        desktop = self.desktop.with_name("discord.desktop")
        desktop.parent.mkdir(parents=True)
        old_entry = (
            "[Desktop Entry]\nName=Discord\n"
            f"Exec={Path.home() / '.local/bin/discord-irisxe'} %u\n"
            "MimeType=x-scheme-handler/discord;\n"
        )
        desktop.write_text(old_entry)
        app.install(self.paths, with_yabd4=False)
        self.assertIn(str(self.paths.launcher), desktop.read_text())
        self.assertIn("MimeType=x-scheme-handler/discord;", desktop.read_text())
        app.uninstall(self.paths)
        self.assertEqual(desktop.read_text(), old_entry)

    def test_unknown_loader_is_left_untouched(self):
        self.loader.parent.mkdir()
        self.loader.write_text('require("unknown")')
        with self.assertRaisesRegex(app.SetupError, "index.js"):
            app.install(self.paths, with_yabd4=False)
        self.assertEqual(self.loader.read_text(), 'require("unknown")')
        self.assertFalse(self.desktop.exists())

    def test_changed_stock_archive_is_not_patched_even_with_a_known_version(self):
        self.stock.write_bytes(b"other build with same version")
        with self.assertRaisesRegex(app.SetupError, "checksum"):
            app.install(self.paths, with_yabd4=False)
        self.assertFalse(self.loader.exists())
        self.assertEqual(self.stock.read_bytes(), b"other build with same version")

    def test_update_runs_stock_without_touching_new_runtime(self):
        app.install(self.paths, with_yabd4=False)
        new = self.runtime.with_name("app-1.0.999")
        new.joinpath("resources").mkdir(parents=True)
        new.joinpath("resources/build_info.json").write_text(
            '{"version":"1.0.999","releaseChannel":"stable"}'
        )
        binary = new / "Discord"
        binary.write_bytes(b"updated")
        binary.chmod(0o755)
        self.pointer.unlink()
        self.pointer.symlink_to(binary)
        with (
            patch.object(app.os, "execve") as run,
            patch.object(app, "intel_profile") as profile,
        ):
            app.launch(self.paths, ["discord://example"])
        self.assertEqual(
            run.call_args.args[:2],
            (
                "/usr/bin/discord",
                ["/usr/bin/discord", "--url", "--", "discord://example"],
            ),
        )
        profile.assert_not_called()
        self.assertFalse(new.joinpath("resources/app").exists())

    def test_bootstrap_runs_when_user_runtime_missing(self):
        self.pointer.unlink()

        def bootstrap(*args, **kwargs):
            self.pointer.symlink_to(self.binary)
            return subprocess.CompletedProcess(args, 0)

        with patch.object(app.subprocess, "run", side_effect=bootstrap) as run:
            app.install(self.paths, with_yabd4=False)
        self.assertIn("--no-gui", run.call_args.args[0])
        self.assertIn(str(self.runtime.parent), run.call_args.args[0])
        self.assertTrue(app.patched_runtime_ready(self.paths))

    def test_bootstrap_uses_returned_directory_when_updater_omits_symlink(self):
        self.pointer.unlink()
        result = subprocess.CompletedProcess([], 0, stdout="app-1.0.168\n")
        with patch.object(app.subprocess, "run", return_value=result):
            app.install(self.paths, with_yabd4=False)
        self.assertEqual(self.pointer.resolve(), self.binary)
        self.assertTrue(app.patched_runtime_ready(self.paths))


class GpuTest(unittest.TestCase):
    def test_hybrid_detection_uses_vendors_and_never_masks_intel_node(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sysfs, dev = root / "sys", root / "dev"
            (dev / "dri").mkdir(parents=True)
            for node, vendor, device in (
                ("renderD128", "0x8086", "0x46a6"),
                ("renderD131", "0x10de", "0x25a2"),
                ("card2", "0x10de", "0x25a2"),
            ):
                info = sysfs / node / "device"
                info.mkdir(parents=True)
                (info / "vendor").write_text(vendor)
                (info / "device").write_text(device)
                (dev / "dri" / node).touch()
            (dev / "nvidia1").touch()
            icd = root / "intel_icd.json"
            icd.touch()
            with patch.object(app, "vaapi_usable", return_value=True) as vaapi:
                profile = app.intel_profile(sysfs, dev, icd)
            vaapi.assert_called_once_with(dev / "dri/renderD128")
            self.assertEqual(profile[0]["LIBVA_DRIVER_NAME"], "iHD")
            self.assertIn(str(dev / "dri/renderD131"), profile[1])
            self.assertIn(str(dev / "dri/card2"), profile[1])
            self.assertIn(str(dev / "nvidia1"), profile[1])
            self.assertNotIn(str(dev / "dri/renderD128"), profile[1])
            (sysfs / "renderD131/device/vendor").write_text("0x1002")
            (sysfs / "card2/device/vendor").write_text("0x1002")
            self.assertIsNone(app.intel_profile(sysfs, dev, icd))


if __name__ == "__main__":
    unittest.main()
