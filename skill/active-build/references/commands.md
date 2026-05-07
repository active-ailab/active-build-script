# active-build Commands

`active-build` is compatible with the original `/home/zepp/workspace/scripts/hmbuild.py` command shape.

## Help

Show this command before running:

```sh
active-build --help
```

## Interactive Full Build

Use when the user did not specify family, project, mode, or threads:

```sh
active-build
```

This opens terminal prompts for family, project, build mode, and thread count.

## Full Build

Use when the user gives a family, project, and mode:

```sh
active-build mhs003 cologne debug
active-build mhs003 geneva release 16
active-build mhs003s atlas debug 8
```

Full builds may:

- check `.repo/manifest.xml`
- ask whether to continue after XML checks
- temporarily set `BOARD_FIRMWARE_VERSION="10.0.0"`
- restore the defconfig in `finally`
- run `make distclean`, `make clean`, sensorhub build, copy sensorhub binary, and `make ota`
- ask whether to use unsigned sensorhub output if the signed file is missing

## Current Config Build

Use only when `build/.config` exists:

```sh
active-build -c app
active-build -c app 8
active-build -c ota
active-build -c ota 12
active-build --current app 8
active-build --current ota 12
```

`app` runs `make -j<threads>`.

`ota` runs `make ota -j<threads>`.

When threads are omitted, the CLI reuses `.hmbuild_last_threads` if available, otherwise uses CPU count times 2.

## Agent Output Rule

Before execution, write the command in the conversation or terminal log:

```text
Running:
cd /path/to/workspace
active-build -c ota 12
```

After execution, summarize the result. On failure, include command, cwd, exit code, and key output.
