#!/usr/bin/env python3
"""Build and verify an encrypted Creator 5 Pro USB update."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


DEFAULT_PASSWORD = "FFP0331&*%root"
EXECUTABLE_NAMES = {
    "IAPCommand", "ISPCommand", "ISPCommand-bak", "firmwareExe",
    "play", "unTar", "wakeup_level",
}
MCU_IMAGES = {
    "mainboard": "mainBoardGD.hex",
    "eboard": "eBoard.hex",
    "heaterboard": "heaterBoard.hex",
    "levelboard": "levelBoard.hex",
}
CHELPER_SOURCES = [
    "pyhelper.c", "serialqueue.c", "stepcompress.c", "steppersync.c",
    "itersolve.c", "trapq.c", "pollreactor.c", "msgblock.c",
    "trdispatch.c", "kin_cartesian.c", "kin_corexy.c",
    "kin_corexz.c", "kin_delta.c", "kin_deltesian.c", "kin_polar.c",
    "kin_rotary_delta.c", "kin_winch.c", "kin_extruder.c",
    "kin_shaper.c", "kin_idex.c", "kin_generic.c",
]


def fail(message: str) -> None:
    raise SystemExit(message)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(command))
    return subprocess.run(command, check=True, **kwargs)


def command_path(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and name == "openssl":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/usr/bin/openssl.exe",
            Path("C:/msys64/usr/bin/openssl.exe"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    fail("Required command not found: {}".format(name))


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def ignore_build_files(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names
               if name in {".git", ".github", ".idea", "__pycache__"}
               or name.endswith((".pyc", ".pyo"))}
    return ignored


def copy_tree(source: Path, target: Path,
              extra_ignore: set[str] | None = None) -> None:
    if not source.is_dir():
        fail("Missing source directory: {}".format(source))

    def ignore(directory: str, names: list[str]) -> set[str]:
        result = ignore_build_files(directory, names)
        if extra_ignore:
            result.update(name for name in names if name in extra_ignore)
        return result

    shutil.copytree(source, target, symlinks=True, ignore=ignore,
                    dirs_exist_ok=True)


def normalized_mode(path: Path) -> int:
    try:
        source_mode = path.lstat().st_mode
    except OSError:
        source_mode = 0
    executable = bool(source_mode & stat.S_IXUSR)
    if path.suffix == ".sh" or path.name in EXECUTABLE_NAMES:
        executable = True
    return 0o755 if executable else 0o644


def tree_paths(root: Path) -> list[Path]:
    return sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())


def add_tree_to_tar(archive: tarfile.TarFile, root: Path,
                    epoch: int) -> None:
    for path in tree_paths(root):
        relative = path.relative_to(root).as_posix()
        info = archive.gettarinfo(str(path), arcname="./" + relative)
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = epoch
        if info.isdir():
            info.mode = 0o755
            archive.addfile(info)
        elif info.isfile():
            info.mode = normalized_mode(path)
            with path.open("rb") as source:
                archive.addfile(info, source)
        else:
            archive.addfile(info)


def make_tar(root: Path, output: Path, compression: str,
             epoch: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if compression == "none":
        with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
            add_tree_to_tar(archive, root, epoch)
        return
    if compression == "xz":
        with tarfile.open(output, "w:xz", format=tarfile.PAX_FORMAT,
                          preset=9) as archive:
            add_tree_to_tar(archive, root, epoch)
        return
    if compression != "gz":
        fail("Unsupported tar compression: {}".format(compression))
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        with tarfile.open(temp_path, "w", format=tarfile.PAX_FORMAT) as archive:
            add_tree_to_tar(archive, root, epoch)
        with temp_path.open("rb") as source, output.open("wb") as target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target,
                               mtime=epoch, compresslevel=9) as compressed:
                shutil.copyfileobj(source, compressed)
    finally:
        temp_path.unlink(missing_ok=True)


def make_zip(source: Path, output: Path, epoch: int) -> None:
    # ZIP timestamps cannot precede 1980.
    import datetime
    stamp = datetime.datetime.fromtimestamp(
        max(epoch, 315532800), datetime.timezone.utc)
    timestamp = (stamp.year, stamp.month, stamp.day,
                 stamp.hour, stamp.minute, stamp.second)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        base = source.parent
        for path in [source] + tree_paths(source):
            relative = path.relative_to(base).as_posix()
            if path.is_dir():
                info = zipfile.ZipInfo(relative.rstrip("/") + "/", timestamp)
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(relative, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | normalized_mode(path)) << 16
            archive.writestr(info, path.read_bytes())


def write_md5_list(root: Path) -> None:
    checksum_file = root / "md5sum.list"
    checksum_file.unlink(missing_ok=True)
    lines = []
    for path in tree_paths(root):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(root).as_posix()
            lines.append("{}  ./{}\n".format(digest(path, "md5"), relative))
    checksum_file.write_text("".join(lines), encoding="ascii", newline="\n")


def git_revision(repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*",
             "-C", str(repository), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def verify_elf(path: Path, machine: int, label: str) -> None:
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:6] != b"\x7fELF\x01\x01":
        fail("{} is not an ELF32 little-endian file: {}".format(label, path))
    if struct.unpack_from("<H", header, 18)[0] != machine:
        fail("{} has the wrong ELF machine: {}".format(label, path))


def build_c_helper(klipper: Path, updates: Path, target: Path,
                   mode: str, compiler: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "preserve":
        source = (updates / "software/klipper/chelper/chelper/c_helper.so")
        if not source.is_file():
            fail("Vendor c_helper.so is unavailable: {}".format(source))
        shutil.copy2(source, target)
    else:
        source_dir = klipper / "klippy/chelper"
        sources = [str(source_dir / name) for name in CHELPER_SOURCES]
        for source in sources:
            if not Path(source).is_file():
                fail("Missing Klippy C helper source: {}".format(source))
        run([
            command_path(compiler), "-Wall", "-O2", "-g", "-shared", "-fPIC",
            "-march=mips32r2", "-mabi=32", "-mhard-float", "-mfp64",
            "-mnan=2008", "-Wl,--hash-style=sysv", "-o", str(target),
            *sources,
        ])
    verify_elf(target, 8, "Klippy C helper")
    target.chmod(0o755)


def stage_software(updates: Path, klipper: Path, target: Path,
                   c_helper_mode: str, mips_compiler: str) -> None:
    source = updates / "software"
    copy_tree(source, target, {"klipper", "md5sum.list"})
    package = target / "klipper"
    (package / "klippy").mkdir(parents=True)
    for path in sorted((klipper / "klippy").glob("*.py")):
        shutil.copy2(path, package / "klippy" / path.name)
    copy_tree(klipper / "klippy/kinematics", package / "kinematics")
    copy_tree(klipper / "klippy/extras", package / "extras")
    copy_tree(klipper / "config/flashforge", package / "config")

    chelper_stage = target.parent / "chelper-stage/chelper"
    copy_tree(klipper / "klippy/chelper", chelper_stage,
              {"c_helper.so"})
    build_c_helper(klipper, updates, chelper_stage / "c_helper.so",
                   c_helper_mode, mips_compiler)
    make_tar(chelper_stage.parent, package / "chelper.tar", "none", 0)
    write_md5_list(target)


def convert_mcus(klipper: Path, control: Path, objcopy: str) -> None:
    objcopy_path = command_path(objcopy)
    for board, image_name in MCU_IMAGES.items():
        elf = klipper / "out-flashforge" / board / "klipper.elf"
        if not elf.is_file():
            fail("Missing {} output; build FlashForge MCUs first: {}"
                 .format(board, elf))
        verify_elf(elf, 40, board + " firmware")
        run([objcopy_path, "-O", "ihex", str(elf),
             str(control / image_name)])


def stage_control(updates: Path, klipper: Path, target: Path,
                  mcu_mode: str, objcopy: str) -> None:
    copy_tree(updates / "control", target, {"md5sum.list"})
    if mcu_mode == "replacement":
        convert_mcus(klipper, target, objcopy)
    write_md5_list(target)


def replace_rootfs_metadata(config: Path, size: int, md5: str) -> None:
    text = config.read_text(encoding="utf-8")
    blocks = re.split(r"(\r?\n\s*\r?\n)", text)
    found = False
    for index in range(0, len(blocks), 2):
        block = blocks[index]
        if re.search(r"^img_type=rootfs\s*$", block, re.MULTILINE):
            block = re.sub(r"^img_size=.*$", "img_size={}".format(size),
                           block, flags=re.MULTILINE)
            block = re.sub(r"^img_md5=.*$", "img_md5={}".format(md5),
                           block, flags=re.MULTILINE)
            blocks[index] = block
            found = True
    if not found:
        fail("No rootfs section in {}".format(config))
    config.write_text("".join(blocks), encoding="utf-8", newline="\n")


def stage_kernel(updates: Path, target: Path, epoch: int,
                 mksquashfs: str) -> None:
    source = updates / "kernel"
    copy_tree(source, target,
              {"md5sum.list", "module", "rootfs_extracted"})
    module_stage = source / "module"
    make_tar(module_stage, target / "module.tar", "none", epoch)

    source_rootfs = source / "ota_kernel_emmc/ota_v1/rootfs_extracted"
    ota_target = target / "ota_kernel_emmc/ota_v1"
    if source_rootfs.is_dir():
        for old in ota_target.glob("rootfs.squashfs.*"):
            old.unlink()
        for old in ota_target.glob("ota_md5_rootfs.squashfs.*"):
            old.unlink()
        raw_image = target.parent / "rootfs.squashfs"
        run([
            command_path(mksquashfs), str(source_rootfs), str(raw_image),
            "-noappend", "-all-root", "-no-xattrs", "-comp", "lzo",
            "-b", "131072", "-mkfs-time", str(epoch),
        ])
        image_md5 = digest(raw_image, "md5")
        destination = ota_target / (
            "rootfs.squashfs.0000." + image_md5)
        shutil.move(raw_image, destination)
        marker = ota_target / ("ota_md5_rootfs.squashfs." + image_md5)
        marker.write_text(image_md5 + "\n", encoding="ascii", newline="\n")
        replace_rootfs_metadata(ota_target / "ota_update.in",
                                destination.stat().st_size, image_md5)
    write_md5_list(target)


def stage_library(updates: Path, target: Path, epoch: int) -> None:
    source = updates / "library"
    for path in source.iterdir():
        if path.name in {"md5sum.list", "zip"}:
            continue
        if path.is_dir():
            copy_tree(path, target / path.name)
        else:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / path.name)
    for source_dir in sorted((source / "zip").iterdir()):
        if source_dir.is_dir():
            make_zip(source_dir, target / "zip" / (source_dir.name + ".zip"),
                     epoch)
    write_md5_list(target)


def verify_component_md5(root: Path) -> None:
    for line in (root / "md5sum.list").read_text(encoding="ascii").splitlines():
        expected, relative = line.split(None, 1)
        path = root / relative.removeprefix("./")
        if not path.is_file() or digest(path, "md5") != expected:
            fail("Component checksum verification failed: {}".format(path))


def encrypt_and_verify(plain: Path, encrypted: Path, openssl: str,
                       password: str, expected_members: set[str]) -> None:
    environment = os.environ.copy()
    environment["FF_UPDATE_PASSWORD"] = password
    run([
        openssl, "enc", "-des-ede3-cbc", "-e", "-salt", "-md", "md5",
        "-pass", "env:FF_UPDATE_PASSWORD", "-in", str(plain),
        "-out", str(encrypted),
    ], env=environment)
    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as temp:
        decrypted = Path(temp.name)
    try:
        run([
            openssl, "enc", "-des-ede3-cbc", "-d", "-salt", "-md", "md5",
            "-pass", "env:FF_UPDATE_PASSWORD", "-in", str(encrypted),
            "-out", str(decrypted),
        ], env=environment)
        if digest(decrypted) != digest(plain):
            fail("Encrypted update did not round-trip to the plaintext archive")
        with tarfile.open(decrypted, "r:gz") as archive:
            members = {Path(member.name).name for member in archive.getmembers()
                       if member.isfile()}
        missing = expected_members - members
        if missing:
            fail("Encrypted update is missing: {}".format(", ".join(missing)))
    finally:
        decrypted.unlink(missing_ok=True)


def parse_components(value: str) -> list[str]:
    if value == "all":
        return ["control", "kernel", "library", "software"]
    components = [item.strip() for item in value.split(",") if item.strip()]
    invalid = set(components) - {"control", "kernel", "library", "software"}
    if invalid or "software" not in components:
        fail("Components must include software and use only control, kernel, "
             "library, software")
    return components


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--klipper", required=True, type=Path)
    parser.add_argument("--updates", required=True, type=Path)
    parser.add_argument("--template", type=Path,
                        default=Path(__file__).resolve().parents[1] / "template")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--filename",
                        default="Creator5Pro-1.9.7-1.2.9-20260810.tgz")
    parser.add_argument("--software-version", default="1.9.7")
    parser.add_argument("--control-version", default="1.2.9")
    parser.add_argument("--kernel-version", default="2.0.5")
    parser.add_argument("--library-version", default="1.2.2")
    parser.add_argument("--components", default="all")
    parser.add_argument("--mcu-mode", choices=("preserve", "replacement"),
                        default="preserve")
    parser.add_argument("--c-helper-mode", choices=("preserve", "rebuild"),
                        default="rebuild")
    parser.add_argument("--mips-cc", default="mipsel-linux-gnu-gcc")
    parser.add_argument("--arm-objcopy", default="arm-none-eabi-objcopy")
    parser.add_argument("--mksquashfs", default="mksquashfs")
    parser.add_argument("--keep-plaintext", action="store_true")
    args = parser.parse_args()

    updates = args.updates.resolve()
    klipper = args.klipper.resolve()
    template = args.template.resolve()
    for required in [updates / "software", klipper / "klippy",
                     template / "runFirmwareExe.sh", template / "play",
                     template / "start.img", template / "end.img"]:
        if not required.exists():
            fail("Required input is missing: {}".format(required))
    components = parse_components(args.components)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    encrypted = output_dir / args.filename
    plain_output = output_dir / (args.filename + ".decrypted.tgz")

    versions = {
        "control": args.control_version,
        "kernel": args.kernel_version,
        "library": args.library_version,
        "software": args.software_version,
    }
    with tempfile.TemporaryDirectory(prefix="creator5pro-update-") as temp_name:
        work = Path(temp_name)
        component_archives: dict[str, Path] = {}
        for component in components:
            stage = work / (component + "-stage")
            stage.mkdir()
            print("Staging {} {}".format(component, versions[component]))
            if component == "software":
                stage_software(updates, klipper, stage,
                               args.c_helper_mode, args.mips_cc)
            elif component == "control":
                stage_control(updates, klipper, stage,
                              args.mcu_mode, args.arm_objcopy)
            elif component == "kernel":
                stage_kernel(updates, stage, epoch, args.mksquashfs)
            elif component == "library":
                stage_library(updates, stage, epoch)
            verify_component_md5(stage)
            name = "{}-{}.tar.xz".format(component, versions[component])
            archive = work / "payload" / name
            make_tar(stage, archive, "xz", epoch)
            component_archives[component] = archive

        payload = work / "outer"
        payload.mkdir()
        for name in ["runFirmwareExe.sh", "play", "start.img", "end.img"]:
            shutil.copy2(template / name, payload / name)
        for archive in component_archives.values():
            shutil.copy2(archive, payload / archive.name)
        make_tar(payload, plain_output, "gz", epoch)

        expected = {"runFirmwareExe.sh", "play", "start.img", "end.img"}
        expected.update(path.name for path in component_archives.values())
        password = os.environ.get("FF_UPDATE_PASSWORD") or DEFAULT_PASSWORD
        openssl = command_path("openssl")
        encrypt_and_verify(plain_output, encrypted, openssl, password, expected)

    manifest = {
        "format": "FlashForge Creator 5 Pro encrypted USB update",
        "filename": encrypted.name,
        "sha256": digest(encrypted),
        "plaintext_sha256": digest(plain_output),
        "components": components,
        "versions": versions,
        "mcu_mode": args.mcu_mode,
        "c_helper_mode": args.c_helper_mode,
        "sources": {
            "ffklipper13": git_revision(klipper),
            "creator-5-pro-firmware-archive": git_revision(updates),
        },
    }
    manifest_path = output_dir / (encrypted.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    (output_dir / (encrypted.name + ".sha256")).write_text(
        "{}  {}\n".format(manifest["sha256"], encrypted.name),
        encoding="ascii", newline="\n")
    if not args.keep_plaintext:
        plain_output.unlink()
    print("Built and verified: {}".format(encrypted))
    print("Manifest: {}".format(manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
