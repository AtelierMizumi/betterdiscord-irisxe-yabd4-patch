#!/usr/bin/python3
"""Install and launch Discord Stable with BetterDiscord on Arch Linux."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

NAME = "discord-arch-patched"
DISCORD = "/usr/bin/discord"
BOOTSTRAP = "/usr/share/discord/updater_bootstrap"
SUPPORTED_BUILDS = {
    "1.0.168": "f0d9a2fe3054dbcad7711ac52d809ff824707935d5212700200eb3a9c9173212",
    "1.0.169": "e8faedb86bd2237c8240b56d704b700e191ea21aa20b8dace071381fd0aeb780",
}
BD_COMMIT = "bc75c6f612355b7d63b19c08e9211a6ffc654171"
BD_SOURCE = (
    f"https://codeload.github.com/BetterDiscord/BetterDiscord/tar.gz/{BD_COMMIT}"
)
BD_SOURCE_SHA256 = "5d05928a4edcfb6c3024da100480f8862e0f6eacb35a67e5effda9f84284525e"
BD_ASAR_SHA256 = "3f9c9a1588813dd3b4ee1f573157da9b93db474286fd0a28603b0e3fafe71655"
YABD_COMMIT = "3934168fd50ec95c76c5c9d73af0158290048870"
YABD_URL = f"https://raw.githubusercontent.com/riolubruh/YABDP4Nitro/{YABD_COMMIT}/YABDP4Nitro.plugin.js"
YABD_SHA256 = "e9a1ad76a47c418116dc3f37129989b675476d7ec6f6d2645478087c8756b26d"
STOCK_CORE = b"module.exports = require('./core.asar');\n"
IRIS_XE_IDS = {
    "0x46a6",
    "0x46a8",
    "0x46aa",
    "0x46b0",
    "0x46b1",
    "0x46b6",
    "0x46b8",
    "0x46ba",
    "0x46c1",
    "0x9a40",
    "0x9a49",
    "0xa7a0",
    "0xa7a1",
}
PRESET = {
    "settings": {
        "screenSharing": True,
        "ResolutionSwapper": True,
        "CustomResolution": 1080,
        "CustomFPS": 60,
        "videoCodec2": 2,
        "CustomBitrateEnabled": False,
        "minBitrate": -1,
        "targetBitrate": -1,
        "maxBitrate": -1,
        "checkForUpdates": False,
    }
}


class SetupError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path

    @classmethod
    def from_environment(cls):
        def xdg(variable, default):
            value = Path(os.environ.get(variable) or default)
            return (value if value.is_absolute() else default).resolve()

        home = Path.home()
        return cls(
            xdg("XDG_CONFIG_HOME", home / ".config"),
            xdg("XDG_DATA_HOME", home / ".local/share"),
            xdg("XDG_STATE_HOME", home / ".local/state"),
        )

    @property
    def discord(self):
        return self.config / "discord"

    @property
    def backup_dir(self):
        return self.state / NAME

    @property
    def asar(self):
        return self.backup_dir / "betterdiscord.asar"

    @property
    def launcher(self):
        return self.data / NAME / "discord-patched"


def warn(message):
    print(f"{NAME}: {message}", file=sys.stderr)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode()


def download(url, checksum):
    with tempfile.TemporaryDirectory(prefix=f"{NAME}-download-") as directory:
        artifact = Path(directory) / "artifact"
        try:
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--location",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--retry",
                    "2",
                    "--max-time",
                    "120",
                    "--output",
                    artifact,
                    url,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SetupError(f"Download failed: {url}") from exc
        data = artifact.read_bytes()
    if sha256(data) != checksum:
        raise SetupError(f"SHA-256 verification failed: {url}")
    return data


def build_betterdiscord():
    source = download(BD_SOURCE, BD_SOURCE_SHA256)
    with tempfile.TemporaryDirectory(prefix=f"{NAME}-build-") as directory:
        build = Path(directory)
        archive = build / "source.tar.gz"
        archive.write_bytes(source)
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(build, filter="data")
        except tarfile.TarError as exc:
            raise SetupError(
                "Could not unpack the pinned BetterDiscord source"
            ) from exc
        project = build / f"BetterDiscord-{BD_COMMIT}"
        env = dict(
            os.environ,
            BRANCH_NAME="main",
            COMMIT_HASH=BD_COMMIT[:8],
            BUN_INSTALL_CACHE_DIR=str(build / "cache"),
        )
        try:
            subprocess.run(
                ["bun", "install", "--frozen-lockfile", "--ignore-scripts"],
                cwd=project,
                env=env,
                check=True,
            )
            subprocess.run(["bun", "run", "dist"], cwd=project, env=env, check=True)
        except subprocess.CalledProcessError as exc:
            raise SetupError("Could not build the pinned BetterDiscord source") from exc
        asar = (project / "dist/betterdiscord.asar").read_bytes()
        if sha256(asar) != BD_ASAR_SHA256:
            raise SetupError(
                "The BetterDiscord build did not match the verified ASAR SHA-256"
            )
        return asar


def runtime_from_binary(paths, binary):
    runtime = binary.parent
    if (
        runtime.parent != paths.discord
        or binary.name != "Discord"
        or not binary.is_file()
        or not os.access(binary, os.X_OK)
    ):
        raise SetupError(f"Unexpected Discord executable: {binary}")
    metadata = runtime / "resources/build_info.json"
    try:
        info = json.loads(metadata.read_bytes())
        version = info["version"]
        if (
            info["releaseChannel"] != "stable"
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or runtime.name not in {version, f"app-{version}"}
        ):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SetupError(f"Unrecognized Discord Stable build: {metadata}") from exc
    return runtime, version


def active_runtime(paths, *, bootstrap=False):
    check_path(paths, paths.discord)
    pointer = paths.discord / "Discord"
    if pointer.exists() or pointer.is_symlink():
        return runtime_from_binary(paths, pointer.resolve())
    if not bootstrap:
        raise SetupError("Discord runtime is missing")

    paths.discord.mkdir(parents=True, exist_ok=True)
    print("Bootstrapping Discord Stable…", flush=True)
    try:
        result = subprocess.run(
            [
                BOOTSTRAP,
                "--no-gui",
                str(paths.discord),
                "stable",
                "https://updates.discord.com/",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SetupError("Discord bootstrap failed") from exc

    if not pointer.exists() and not pointer.is_symlink():
        folder = (result.stdout or "").strip()
        if not re.fullmatch(r"(?:app-)?[0-9]+\.[0-9]+\.[0-9]+", folder):
            raise SetupError(
                "Discord bootstrap returned an unexpected runtime directory"
            )
        relative_binary = Path(folder) / "Discord"
        runtime_from_binary(paths, (paths.discord / relative_binary).resolve())
        try:
            pointer.symlink_to(relative_binary)
        except FileExistsError:
            pass
    return runtime_from_binary(paths, pointer.resolve())


def ensure_discord_closed(paths):
    for proc in Path("/proc").glob("[0-9]*/exe"):
        try:
            target = Path(os.readlink(proc).removesuffix(" (deleted)"))
        except OSError:
            continue
        if target.name == "Discord" and target.is_relative_to(paths.discord):
            raise SetupError("Quit Discord from the system tray and run setup again")


def check_path(paths, path):
    for root in (paths.config, paths.data, paths.state):
        if path.is_relative_to(root):
            current = root
            for part in path.relative_to(root).parts:
                current /= part
                if current.is_symlink():
                    raise SetupError(f"Unexpected symlink: {current}")
            return
    raise SetupError(f"Path outside the configured XDG directories: {path}")


def read_file(path):
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SetupError(f"Expected a regular file: {path}")
        return path.read_bytes()
    except FileNotFoundError:
        return None


def write_file(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".discord-patch-")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            os.fchmod(file.fileno(), mode)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def fingerprint(data):
    return sha256(data) if data is not None else None


@contextmanager
def lock(paths, *, exclusive):
    directory = paths.backup_dir
    check_path(paths, directory / "lock")
    if exclusive:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "lock").open("a+b" if exclusive else "rb") as file:
        fcntl.flock(file, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield


def load_json(path, default):
    data = read_file(path)
    return json.loads(data) if data is not None else default


def restore_entries(paths, entries):
    for entry in reversed(entries):
        path = Path(entry["path"])
        check_path(paths, path)
        data = read_file(path)
        if fingerprint(data) != entry["after"]:
            if fingerprint(data) != entry["before_hash"]:
                warn(f"Preserving changed file: {path}")
            continue
        if entry["before"] is None:
            path.unlink(missing_ok=True)
        else:
            write_file(path, base64.b64decode(entry["before"]), entry["mode"])


def restore_empty_app_dirs(paths, entries):
    for entry in entries:
        path = Path(entry["path"])
        if (
            entry["before"] is None
            and path.parent.name == "app"
            and path.parent.parent.name == "resources"
        ):
            check_path(paths, path.parent)
            try:
                path.parent.rmdir()
            except OSError:
                pass


def recover(paths):
    journal = paths.backup_dir / "transaction.json"
    if journal.is_file():
        pending = load_json(journal, None)
        manifest = paths.backup_dir / "installed.json"
        if load_json(manifest, None) != pending["committed"]:
            restore_entries(paths, pending["writes"])
            restore_empty_app_dirs(paths, pending["writes"])
        journal.unlink()


def apply_changes(paths, changes, runtime, archive, old):
    records = {entry["path"]: entry for entry in old.get("entries", [])}
    writes = []
    for path, (data, mode) in changes.items():
        check_path(paths, path)
        before = read_file(path)
        if before == data:
            continue
        after = fingerprint(data)
        previous = records.get(str(path))
        if previous and fingerprint(before) not in (
            previous["before_hash"],
            previous["after"],
        ):
            raise SetupError(f"Managed file changed outside setup: {path}")
        entry = {
            "path": str(path),
            "before": base64.b64encode(before).decode() if before is not None else None,
            "before_hash": fingerprint(before),
            "mode": stat.S_IMODE(path.stat().st_mode) if before is not None else mode,
            "after": after,
        }
        writes.append(entry)
        records[str(path)] = {**(previous or entry), "after": after}

    manifest = paths.backup_dir / "installed.json"
    committed = {
        "runtime": str(runtime),
        "archive": archive.name,
        "entries": list(records.values()),
    }
    if not writes and committed == old:
        return
    journal = paths.backup_dir / "transaction.json"
    write_file(journal, json_bytes({"writes": writes, "committed": committed}), 0o600)
    try:
        for entry in writes:
            path = Path(entry["path"])
            data, mode = changes[path]
            if fingerprint(read_file(path)) != entry["before_hash"]:
                raise SetupError(f"File changed during setup: {path}")
            if data is None:
                path.unlink()
            else:
                write_file(path, data, mode)
        write_file(manifest, json_bytes(committed), 0o600)
    except BaseException:
        recover(paths)
        raise
    journal.unlink()


def app_loader(asar, archive):
    return (
        f"require({json.dumps(str(asar) + '/main.js')});\n"
        f"require({json.dumps('../' + archive.name)});\n"
    ).encode()


def loader_changes(paths, runtime, version):
    resources = runtime / "resources"
    package = resources / "app/package.json"
    index = resources / "app/index.js"
    stock = resources / "app.asar"
    renamed = resources / "betterdiscord.app.asar"
    for path in (package, index, stock, renamed):
        check_path(paths, path)

    old_index = read_file(index)
    old_package = read_file(package)
    legacy_asar = paths.config / "BetterDiscord/data/betterdiscord.asar"
    stock_data = read_file(stock)
    renamed_data = read_file(renamed)
    if (stock_data is not None) == (renamed_data is not None):
        raise SetupError(f"Expected exactly one Discord app archive in {resources}")
    if (
        fingerprint(stock_data if stock_data is not None else renamed_data)
        != SUPPORTED_BUILDS[version]
    ):
        raise SetupError(f"Unrecognized Discord app archive checksum in {resources}")
    current = app_loader(paths.asar, renamed)
    if old_index is not None:
        legacy = app_loader(legacy_asar, renamed)
        if old_index.rstrip() not in {current.rstrip(), legacy.rstrip()}:
            raise SetupError(f"Unrecognized app loader: {index}")
        if stock_data is not None:
            raise SetupError(f"Unrecognized Discord app layout: {index}")
    if old_package is not None:
        try:
            recognized = json.loads(old_package) == {
                "name": "discord",
                "main": "index.js",
            }
        except (ValueError, UnicodeError):
            recognized = False
        if not recognized:
            raise SetupError(f"Unrecognized app package: {package}")
    if old_index is None and (
        old_package is not None
        or stock_data is None
        or (index.parent.exists() and any(index.parent.iterdir()))
    ):
        raise SetupError(f"Unrecognized Discord app layout: {index}")

    changes = {}
    modules = runtime / "modules"
    direct = modules / "discord_desktop_core/index.js"
    core_indexes = sorted(
        modules.glob("discord_desktop_core-*/discord_desktop_core/index.js")
    )
    if direct.exists():
        core_indexes.append(direct)
    known_core = f"require({json.dumps(str(legacy_asar))});\n".encode() + STOCK_CORE
    for core in core_indexes:
        check_path(paths, core)
        data = read_file(core)
        if data == known_core:
            changes[core] = (STOCK_CORE, 0o644)
        elif data != STOCK_CORE:
            raise SetupError(f"Unrecognized desktop core loader: {core}")

    if stock_data is not None:
        changes[renamed] = (stock_data, stat.S_IMODE(stock.stat().st_mode))
    if old_package is None:
        changes[package] = (json_bytes({"name": "discord", "main": "index.js"}), 0o644)
    changes[index] = (current, 0o644)
    if stock_data is not None:
        changes[stock] = (None, 0o644)
    return renamed, changes


def desktop_exec(launcher):
    # Exec quoting has separate Desktop Entry and command-line escape layers.
    escaped = re.sub(r'(["`$\\])', r"\\\1", str(launcher).replace("%", "%%"))
    escaped = escaped.replace("\\", "\\\\")
    return f'Exec="{escaped}" %u\n'


def desktop_entry(launcher):
    return (
        "[Desktop Entry]\nType=Application\nName=Discord (BetterDiscord)\n"
        f"{desktop_exec(launcher)}"
        "Icon=discord\nTerminal=false\nCategories=Network;InstantMessaging;\n"
        "StartupWMClass=discord\n"
    ).encode()


def redirect_legacy_desktops(paths, changes):
    legacy_exec = f"Exec={Path.home() / '.local/bin/discord-irisxe'} %u\n".encode()
    replacement = desktop_exec(paths.launcher).encode()
    for name in ("discord.desktop", "discord-irisxe.desktop"):
        desktop = paths.data / "applications" / name
        if not desktop.is_file() or desktop.is_symlink():
            continue
        check_path(paths, desktop)
        lines = desktop.read_bytes().splitlines(keepends=True)
        if legacy_exec in lines:
            data = b"".join(
                replacement if line == legacy_exec else line for line in lines
            )
            changes[desktop] = (data, stat.S_IMODE(desktop.stat().st_mode))


def install(paths, with_yabd4):
    with lock(paths, exclusive=True):
        ensure_discord_closed(paths)
        recover(paths)
        runtime, version = active_runtime(paths, bootstrap=True)
        if version not in SUPPORTED_BUILDS:
            raise SetupError(
                f"Discord {version} is not supported yet. Use the stock Discord launcher"
            )
        manifest = load_json(paths.backup_dir / "installed.json", {})
        archive, changes = loader_changes(paths, runtime, version)
        desktop = paths.data / f"applications/{NAME}.desktop"
        existing = {entry["path"]: entry for entry in manifest.get("entries", [])}

        for path in (paths.launcher, desktop, paths.asar):
            check_path(paths, path)
            if path.exists() and str(path) not in existing:
                raise SetupError(f"File exists outside setup ownership: {path}")
        if not paths.asar.is_file():
            changes = {paths.asar: (build_betterdiscord(), 0o600), **changes}
        elif fingerprint(read_file(paths.asar)) != existing[str(paths.asar)]["after"]:
            raise SetupError(f"Managed BetterDiscord archive changed: {paths.asar}")

        if with_yabd4:
            plugins = paths.config / "BetterDiscord/plugins"
            plugin = plugins / "YABDP4Nitro.plugin.js"
            preset = plugins / "YABDP4Nitro.config.json"
            enabled = paths.config / "BetterDiscord/data/stable/plugins.json"
            for path in (plugin, preset, enabled):
                check_path(paths, path)
            if not plugin.exists():
                changes[plugin] = (download(YABD_URL, YABD_SHA256), 0o644)
                if not preset.exists():
                    changes[preset] = (json_bytes(PRESET), 0o600)
                plugin_state = load_json(enabled, {})
                if not isinstance(plugin_state, dict):
                    raise SetupError(
                        f"Unrecognized BetterDiscord plugin state: {enabled}"
                    )
                if "YABDP4Nitro" not in plugin_state:
                    plugin_state["YABDP4Nitro"] = True
                    changes[enabled] = (json_bytes(plugin_state), 0o600)

        changes[paths.launcher] = (Path(__file__).read_bytes(), 0o755)
        changes[desktop] = (desktop_entry(paths.launcher), 0o644)
        redirect_legacy_desktops(paths, changes)
        ensure_discord_closed(paths)
        if active_runtime(paths)[0] != runtime:
            raise SetupError("Discord updated during setup. Run setup again")
        apply_changes(paths, changes, runtime, archive, manifest)
    print("Installed. Open Discord (BetterDiscord) from the application menu.")


def uninstall(paths):
    if not paths.backup_dir.exists():
        return
    with lock(paths, exclusive=True):
        ensure_discord_closed(paths)
        recover(paths)
        manifest = paths.backup_dir / "installed.json"
        installed = load_json(manifest, {})
        entries = installed.get("entries", [])
        retained = []
        changed_runtimes = set()
        for entry in entries:
            path = Path(entry["path"])
            check_path(paths, path)
            runtime = managed_runtime(paths, path)
            if runtime and runtime.is_dir():
                current = fingerprint(read_file(path))
                if current not in (entry["before_hash"], entry["after"]):
                    changed_runtimes.add(runtime)
                    warn(f"Preserving changed Discord runtime: {path}")

        restorable = []
        for entry in entries:
            path = Path(entry["path"])
            runtime = managed_runtime(paths, path)
            if runtime in changed_runtimes or (changed_runtimes and path == paths.asar):
                retained.append(entry)
            elif not runtime or runtime.is_dir():
                restorable.append(entry)
        restore_entries(paths, restorable)
        restore_empty_app_dirs(paths, restorable)
        if retained:
            write_file(manifest, json_bytes({**installed, "entries": retained}), 0o600)
        else:
            manifest.unlink(missing_ok=True)
    print("Removed managed files. Discord and your profile are still available.")


def managed_runtime(paths, path):
    if path.is_relative_to(paths.discord):
        return paths.discord / path.relative_to(paths.discord).parts[0]
    return None


def patched_runtime_ready(paths):
    try:
        runtime, version = active_runtime(paths)
        if version not in SUPPORTED_BUILDS:
            return False
        installed = load_json(paths.backup_dir / "installed.json", {})
        if str(runtime) != installed.get("runtime"):
            return False
        resources = runtime / "resources"
        archive = resources / installed["archive"]
        index = resources / "app/index.js"
        package = resources / "app/package.json"
        expected = {entry["path"]: entry["after"] for entry in installed["entries"]}
        if fingerprint(read_file(archive)) != SUPPORTED_BUILDS[version]:
            return False
        if read_file(index) != app_loader(paths.asar, archive):
            return False
        if json.loads(package.read_bytes()) != {"name": "discord", "main": "index.js"}:
            return False
        return (
            fingerprint(read_file(paths.asar)) == expected.get(str(paths.asar))
            and paths.asar.is_file()
        )
    except (SetupError, OSError, KeyError, ValueError, RuntimeError):
        return False


def drm_devices(sysfs):
    for node in sorted(sysfs.iterdir()) if sysfs.is_dir() else ():
        if not re.fullmatch(r"(?:renderD|card)[0-9]+", node.name):
            continue
        try:
            vendor = (node / "device/vendor").read_text().strip()
            device = (node / "device/device").read_text().strip()
        except OSError:
            continue
        yield node.name, vendor, device


def vaapi_usable(node):
    try:
        result = subprocess.run(
            ["vainfo", "--display", "drm", "--device", node],
            env=dict(os.environ, LIBVA_DRIVER_NAME="iHD"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and any(
        "VAProfileH264" in line and "VAEntrypointEncSlice" in line
        for line in result.stdout.splitlines()
    )


def intel_profile(
    sysfs=Path("/sys/class/drm"),
    dev=Path("/dev"),
    icd=Path("/usr/share/vulkan/icd.d/intel_icd.json"),
):
    nodes = list(drm_devices(sysfs))
    nvidia = [dev / "dri" / name for name, vendor, _ in nodes if vendor == "0x10de"]
    if not nvidia or not icd.is_file():
        return None
    intel = next(
        (
            dev / "dri" / name
            for name, vendor, device in nodes
            if name.startswith("renderD")
            and vendor == "0x8086"
            and device in IRIS_XE_IDS
            and (dev / "dri" / name).exists()
        ),
        None,
    )
    if intel is None or not vaapi_usable(intel):
        return None
    nvidia.extend(path for path in dev.glob("nvidia*") if path.name != "nvidia-caps")
    masks = [str(node) for node in nvidia if node.exists() and not node.is_dir()]
    pci = (sysfs / intel.name / "device").resolve().name
    prime = "0"
    if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", pci):
        prime = "pci-" + re.sub(r"[:.]", "_", pci)
    return {
        "LIBVA_DRIVER_NAME": "iHD",
        "DRI_PRIME": prime,
        "VK_DRIVER_FILES": str(icd),
        "VK_ICD_FILENAMES": str(icd),
        "CUDA_VISIBLE_DEVICES": "-1",
        "__GLX_VENDOR_LIBRARY_NAME": "mesa",
        "__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/50_mesa.json",
    }, masks


def bubblewrap_args(paths, masks):
    run_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    args = [
        "bwrap",
        "--unshare-user",
        "--uid",
        str(os.getuid()),
        "--gid",
        str(os.getgid()),
        "--ro-bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
    ]
    # Bind the real session directories, including the X11 socket in /tmp.
    for directory in dict.fromkeys(
        (Path.home(), run_dir, Path("/tmp"), paths.config, paths.data, paths.state)
    ):
        if directory.is_dir():
            args.extend(("--bind", str(directory), str(directory)))
    for node in masks:
        args.extend(("--ro-bind", "/dev/null", node))
    if Path("/dev/nvidia-caps").is_dir():
        args.extend(("--tmpfs", "/dev/nvidia-caps"))
    return args


def report_fallback(message, *, notify=False):
    warn(message)
    if notify and shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "Discord (BetterDiscord)", message],
                check=False,
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def launch(paths, args):
    command = [DISCORD, "--url", "--", *args]
    env = os.environ.copy()
    try:
        with lock(paths, exclusive=False):
            ready = not (
                paths.backup_dir / "transaction.json"
            ).exists() and patched_runtime_ready(paths)
    except FileNotFoundError:
        ready = False
    if not ready:
        report_fallback(
            "Patched runtime unavailable. Opening Discord with default settings.",
            notify=True,
        )
    else:
        profile = intel_profile()
        if profile and shutil.which("bwrap"):
            gpu_env, masks = profile
            sandbox = bubblewrap_args(paths, masks)
            try:
                probe = subprocess.run(
                    [*sandbox, "--", "/usr/bin/true"],
                    check=False,
                    timeout=5,
                    capture_output=True,
                )
                if probe.returncode == 0:
                    warn("Using Intel Iris Xe with VA-API iHD.")
                    os.execve(
                        shutil.which("bwrap"), [*sandbox, "--", *command], env | gpu_env
                    )
            except (OSError, subprocess.TimeoutExpired):
                pass
        report_fallback("Using Discord’s default GPU settings.")
    os.execve(DISCORD, command, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "launch"))
    if len(sys.argv) > 1 and sys.argv[1] == "launch":
        launch(Paths.from_environment(), sys.argv[2:])
        return
    parser.add_argument("--with-yabd4", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.with_yabd4 and args.uninstall:
        parser.error("--uninstall and --with-yabd4 cannot be combined")
    if os.getuid() == 0 or platform.machine() != "x86_64":
        raise SetupError("Run setup as a regular user on Arch Linux x86_64")
    paths = Paths.from_environment()
    if args.uninstall:
        uninstall(paths)
    else:
        install(paths, args.with_yabd4)


if __name__ == "__main__":
    try:
        if Path(sys.argv[0]).name == "discord-patched":
            launch(Paths.from_environment(), sys.argv[1:])
        else:
            main()
    except (SetupError, OSError, ValueError) as error:
        warn(str(error))
        sys.exit(1)
