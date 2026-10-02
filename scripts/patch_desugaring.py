#!/usr/bin/env python3
"""Patch Flutter Android build.gradle to enable core library desugaring.

Required by flutter_local_notifications (pulled in via flet-android-notifications).
Run after `flet build apk` generates the project but before the final Gradle build.
"""
import sys
import os


def patch_build_gradle(path: str) -> bool:
    if not os.path.exists(path):
        print(f"  WARN: {path} not found, skipping")
        return False

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changed = False

    # 1. Enable coreLibraryDesugaringEnabled in compileOptions
    if "coreLibraryDesugaringEnabled" not in content:
        if "compileOptions {" in content:
            content = content.replace(
                "compileOptions {",
                "compileOptions {\n        coreLibraryDesugaringEnabled true",
                1,
            )
            changed = True
            print(f"  + coreLibraryDesugaringEnabled -> {path}")
        else:
            print(f"  ERROR: compileOptions block not found in {path}")
            return False

    # 2. Add desugar_jdk_libs dependency
    if "desugar_jdk_libs" not in content:
        if "dependencies {" in content:
            content = content.replace(
                "dependencies {",
                'dependencies {\n    coreLibraryDesugaring "com.android.tools:desugar_jdk_libs:2.0.4"',
                1,
            )
            changed = True
            print(f"  + desugar_jdk_libs:2.0.4 -> {path}")
        else:
            print(f"  ERROR: dependencies block not found in {path}")
            return False

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  OK: {path} patched")
    else:
        print(f"  OK: {path} already has desugaring")

    return True


def main():
    paths = sys.argv[1:] if len(sys.argv) > 1 else [
        "build/flutter/android/app/build.gradle",
    ]
    all_ok = True
    for p in paths:
        print(f"Patching {p} ...")
        if not patch_build_gradle(p):
            all_ok = False
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
