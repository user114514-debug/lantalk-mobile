#!/usr/bin/env python3
"""Patch flet's bundled Android templates to enable core library desugaring.

Must run BEFORE `flet build apk` so the generated project inherits the fix.
Handles both Groovy (build.gradle) and Kotlin DSL (build.gradle.kts) templates.
Required by flutter_local_notifications (pulled in via flet-android-notifications).
"""
import os
import sys
import importlib


def find_template_files():
    """Find all build.gradle / build.gradle.kts under flet and flet_core packages."""
    found = []
    for pkg_name in ("flet", "flet_core"):
        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError:
            print(f"  [skip] {pkg_name} not installed")
            continue
        pkg_dir = os.path.dirname(pkg.__file__)
        for root, _dirs, files in os.walk(pkg_dir):
            for fn in files:
                if fn in ("build.gradle", "build.gradle.kts"):
                    found.append(os.path.join(root, fn))
    return found


def patch_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    is_kotlin = path.endswith(".kts")
    changed = False

    # --- 1. Enable coreLibraryDesugaringEnabled in compileOptions ---
    if "coreLibraryDesugaringEnabled" not in content and "isCoreLibraryDesugaringEnabled" not in content:
        if "compileOptions {" in content:
            if is_kotlin:
                replacement = "compileOptions {\n        isCoreLibraryDesugaringEnabled = true"
            else:
                replacement = "compileOptions {\n        coreLibraryDesugaringEnabled true"
            content = content.replace("compileOptions {", replacement, 1)
            changed = True
            print(f"    + isCoreLibraryDesugaringEnabled -> {os.path.basename(path)}")

    # --- 2. Add desugar_jdk_libs dependency ---
    if "desugar_jdk_libs" not in content:
        if "dependencies {" in content:
            if is_kotlin:
                dep = 'dependencies {\n    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.0.4")'
            else:
                dep = 'dependencies {\n    coreLibraryDesugaring "com.android.tools:desugar_jdk_libs:2.0.4"'
            content = content.replace("dependencies {", dep, 1)
            changed = True
            print(f"    + desugar_jdk_libs:2.0.4 -> {os.path.basename(path)}")

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"    Patched: {path}")
    else:
        print(f"    Already OK: {path}")

    return changed


def main():
    print("=== Patching flet Android templates for core library desugaring ===")
    files = find_template_files()
    if not files:
        print("ERROR: No build.gradle templates found in flet/flet_core packages!")
        # 打印 site-packages 路径供调试
        import site
        print("Site packages:", site.getsitepackages())
        sys.exit(1)

    print(f"Found {len(files)} template file(s):")
    any_patched = False
    for fp in files:
        print(f"  {fp}")
        if patch_file(fp):
            any_patched = True

    if not any_patched:
        print("WARNING: No files were patched (all already have desugaring or no matching blocks)")
    print("Done.")


if __name__ == "__main__":
    main()
