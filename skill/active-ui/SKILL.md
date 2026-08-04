---
name: active-ui
description: Use when building Active/Zepp UI resources with the independent active-ui CLI, including Application.bin, Application.crc, locale.zip, interactive UI builds, BuildPlan execution, or dry-run inspection.
---

# active-ui

Owner: cs-dongqi@zepp.com
Organization: Active.Bu

`active-ui` is the independent UI resource compiler. It follows the common workspace, argument, interactive, logging, BuildPlan, and dry-run conventions of `active-bstyle`, while keeping the existing build-ui resource generation sequence.

## Commands

```sh
active-ui
active-ui --gapp
active-ui --csv
active-ui --all
active-ui -f <family> -p <project> [--gapp|--csv|--all]
active-ui -w <workspace> --dry-run --all
active-ui -i <plan.json>
```

When no resource type is specified in command-line mode, the default is `all`. Running `active-ui` with no arguments enters interactive mode and asks for family, project, and resource type.

Supported resource types:

- `--gapp`: build `Application.bin` and `Application.crc`.
- `--csv`: build `locale.zip`.
- `--all`: build both outputs. `makeUI.py` runs only once.

## Workspace and configuration

Workspace discovery, `-w`, `-f/-p`, `-i`, `-l`, TUI/text fallback, and logging follow `active-bstyle` conventions.

- Without `-f/-p`, use the current workspace `build/.config` directly.
- `-f` and `-p` must be supplied together.
- With `-f/-p`, run only the main-project preparation sequence:

```text
make distclean
make clean
make <family>_<project>_defconfig
```

No sensorhub defconfig or sensorhub build is run.

## UI generation sequence

The configuration transaction is part of every UI generation, regardless of whether `-f/-p` was provided:

```text
backup build/.config
→ temporarily set HMI_UPGRADE_CHECK_GAPP=n
→ python3 scripts/makeUI.py ../ui .
→ generate selected UI outputs
→ restore build/.config
→ remove the backup and report cleanup warnings
```

For `gapp` mode, `Application_<project>.gapp` is preferred, with `Application.gapp` as fallback. `cmd/linux64/gapp2bin` generates `Application.bin`; the tool then computes `Application.crc` using zlib CRC32.

For `csv` mode, the generated `locale/` directory is packaged as `build/locale.zip`.

For `all` mode, the same `makeUI.py` output is reused for both GAPP and locale generation.

## Success and cleanup rules

The expected outputs must be non-empty and must be created or changed by the current run. A pre-existing non-empty file is not sufficient.

The `.config` backup is always restored in cleanup handling. If restoration fails, report the configuration error. If backup-file deletion fails, report the leftover path and warning; this warning does not invalidate already verified UI outputs.

## Dry run

`--dry-run` prints the configuration preparation and UI generation commands but does not:

- execute `make`, `makeUI.py`, or `gapp2bin`;
- modify or back up `.config`;
- create UI outputs;
- prompt for device deployment.

After a successful interactive build, ask whether to use the Watch-Link targeted push入口:

- `gapp`: `wlctl fs push-gapp --from-path <build-dir>`
- `csv`: `wlctl fs push-csv --from-path <build-dir>`
- `all`: run both targeted入口s in sequence

Non-interactive and `--dry-run` execution skips the push confirmation. `push-gapp` only accepts `Application.bin` and `Application.crc`; `push-csv` only accepts `locale.zip`, clears the fixed device `SYSTEM/resources/locale/` directory, and writes `SYSTEM/resources/locale/locale.zip`.

`active-ui` never runs `make ota`, sensorhub builds, generic SYSTEM filesystem operations, or firmware flashing.
