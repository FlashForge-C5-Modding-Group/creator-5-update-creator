# Creator 5 Pro update creator

This repository builds the encrypted FlashForge USB update format used by
`Creator5Pro-1.9.7-1.2.9-20260810.tgz`. It combines two source submodules:

- `sources/ffklipper13`: current Klippy, FlashForge configuration, C helper,
  and optionally the four replacement MCU images.
- `sources/firmware-archive`: vendor updater scripts, touchscreen executable,
  libraries, kernel source tree, resources, and hardware updater utilities
  that cannot yet be recreated from source.

The small files in `template/` are the outer updater assets recovered from the
reference 1.9.7 package. They are kept unchanged. The build does not run any
printer-side updater or connect to a printer.

## GitHub Actions

Open **Actions → Build Creator 5 Pro update → Run workflow**. The normal
default creates a software/library update and does not include a control
component, so it cannot invoke the MCU flashing stage. The encrypted update,
SHA-256 file, and provenance manifest are uploaded as workflow artifacts.

For a complete developer image, choose `all` components. Choosing
`mcu_mode=replacement` first builds all four ffklipper13 MCU targets and
converts their ELF files to the vendor Intel HEX filenames. A package with a
control component contains the vendor `Update` marker and will run the
printer's MCU update path when installed; use that option only when you intend
to test replacement MCU firmware.

The submodule commits are recorded by the parent repository and again in the
generated manifest, so a package can be traced back to both inputs.

## Local build

On Ubuntu/Debian:

```sh
git clone --recurse-submodules \
  https://github.com/FlashForge-C5-Modding-Group/creator-5-update-creator.git
cd creator-5-update-creator
sudo apt-get install gcc-mipsel-linux-gnu gcc-arm-none-eabi \
  libnewlib-arm-none-eabi squashfs-tools openssl xz-utils
./build.sh
```

The default command creates:

```text
dist/Creator5Pro-1.9.7-1.2.9-20260810.tgz
dist/Creator5Pro-1.9.7-1.2.9-20260810.tgz.sha256
dist/Creator5Pro-1.9.7-1.2.9-20260810.tgz.manifest.json
```

Build a full update with replacement MCU images:

```sh
COMPONENTS=all MCU_MODE=replacement ./build.sh
```

The builder regenerates each inner `md5sum.list`, creates the vendor nested
tar/zip layout, compiles a MIPS32r2 Klippy `c_helper.so`, packages the selected
components, encrypts the outer archive, then decrypts it again and verifies the
round trip before publishing it.

The format uses the printer's legacy compatibility cipher and KDF. The
equivalent manual decrypt command is:

```sh
openssl enc -des-ede3-cbc -d -k 'FFP0331&*%root' -salt -md md5 \
  -in Creator5Pro-1.9.7-1.2.9-20260810.tgz -out decrypted.tgz
```

Set `FF_UPDATE_PASSWORD` to override the format password without placing a
different value on a command line. The default is the known Creator 5 Pro
update-format password shown above.
