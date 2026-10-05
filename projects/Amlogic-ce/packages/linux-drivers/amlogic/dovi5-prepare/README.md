# Optional second Dolby Vision backend

This package prepares a supported user-provided module for p3i's Amlogic-ng kernel and compatibility shim. The original Dolby Vision module remains required and is loaded first. Missing, disabled, rejected or failed preparation keeps playback on the original backend. This package includes no proprietary module.

This integration targets the supported S922X-J / AM6B+ configuration with ARM userspace and an ARM64 kernel. Amlogic-ne and SC2/S905X4 are outside this package's selected scope. The second backend is used only for eligible native Dolby Vision routes; conversion, VP and unsupported refresh routes continue through the original backend.

Provision the original, unmodified new-module file with SHA256:

```text
f6c26659a255447685ceac9441e399c999b1fae9c6435c48d70e14a14dd7f8f7
```

Keep it in one of these locations, in search order:

1. `/storage/.config/dovi5.ko`
2. `/flash/dovi5.ko`
3. `/storage/dovi5.ko`

Preparation reads these files and writes a separate private copy under `/storage/.dovi5/generations/`. It never patches the supplied original or writes `/flash`. Keep the supplied original available: a prepared cache alone cannot authorize loading. Already patched or different module contents are rejected.

The image must contain the paired kernel changes, configured `dv_compat_shim`, Python3 and the exact kernel/shim `Module.symvers` installed by this package. Preparation checks the native module layout, running kernel release, readable kernel exports and each imported symbol's target CRC. The kernel enforces strict version checks for `dovi5` and `dv_compat_shim` while retaining the original vendor module's existing compatibility policy. A mismatch rejects the second backend.

To opt out, create `/storage/.config/dovi5.conf` containing:

```ini
ENABLE=no
```

`ENABLE=yes` permits preparation and loading when all checks succeed. The system default is in `/etc/dovi5.conf`; the user file takes precedence. Other values or malformed configuration reject loading. Configuration is parsed as data and never executed. Disabling preserves supplied files and complete prepared generations; it removes eligibility for a new load. It does not unload a module already running in the current session; use the setting before the next normal boot or service start.

Preparation is ordered before the Dolby loader. The loader independently rechecks the current source, configuration, kernel/shim references, symbol versions, manifest and complete canonical output before loading the second module. `/run/dovi5-path` identifies a validated generation for that boot and cannot authorize an arbitrary file. Preparation and loader messages are available in the system journal.

Successful preparation and loading establish software compatibility checks. CMv4 rendering, chroma quality, cadence and HDMI behavior still require acceptance testing on the paired image and display chain.

For maintainers, tests contain synthetic references only. Supply the optional real module fixture outside the repository:

```sh
DOVI5_TEST_SOURCE=/path/to/unmodified/dovi5.ko python3 projects/Amlogic-ce/packages/linux-drivers/amlogic/dovi5-prepare/tests/test_dovi5.py
```

`--fixture /path/to/unmodified/dovi5.ko` is equivalent. Tests resolve the repository's actual opentee loader; `--loader /path/to/dovi-loader.sh` is available for isolated staging. Without a supplied module, real-module cases are explicitly skipped and synthetic validation tests still run. Never add a module fixture to the repository.
