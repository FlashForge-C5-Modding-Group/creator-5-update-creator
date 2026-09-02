#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MCU_MODE=${MCU_MODE:-preserve}
COMPONENTS=${COMPONENTS:-software,library}
FILENAME=${FILENAME:-Creator5Pro-1.9.7-1.2.9-20260810.tgz}

if [[ "$MCU_MODE" == replacement ]]; then
    JOBS=${JOBS:-4} "$ROOT/sources/ffklipper13/scripts/build-flashforge-mcus.sh" all
fi

python3 "$ROOT/scripts/build_update.py" \
    --klipper "$ROOT/sources/ffklipper13" \
    --updates "$ROOT/sources/firmware-archive" \
    --template "$ROOT/template" \
    --output-dir "$ROOT/dist" \
    --filename "$FILENAME" \
    --components "$COMPONENTS" \
    --mcu-mode "$MCU_MODE" \
    --c-helper-mode rebuild

