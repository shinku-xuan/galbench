"""Bounded CLI discovery in local install directories, without executing apps."""
from __future__ import annotations

import os
import re
from pathlib import Path

FAMILIES = {
    "codex": ("codex",), "claude_code": ("claudecode",),
    "minimax_code": ("minimax",), "zcode": ("zcode",), "kimi_code": ("kimicode", "kimi"),
    "deepseek_harness": ("deepseekharness",), "opencode": ("opencode",),
    "qoder": ("qoder",), "workbuddy": ("workbuddy", "codebuddy"),
}
RELATIVE_ENTRIES = {
    "codex": ("bin/codex.exe", "codex.exe"),
    "claude_code": ("claude.exe", "bin/claude.exe"),
    "minimax_code": ("mcode.cmd", "bin/mcode.cmd", "bin/mcode.exe"),
    "zcode": ("resources/glm/zcode.cjs", "bin/zcode.exe", "bin/zcode.cmd", "zcode.cjs",
              "packages/cli/dist/zcode.cjs", "apps/zcode-cli/packages/cli/dist/zcode.cjs"),
    "kimi_code": ("bin/kimi.exe", "bin/kimi.cmd", "resources/cli/kimi.exe"),
    "deepseek_harness": ("resources/runtime/cli/bin/dsh.cmd", "desktop/resources/runtime/cli/bin/dsh.cmd",
                         "bin/dsh.cmd", "bin/dsh.exe"),
    "opencode": ("bin/opencode.exe", "bin/opencode.cmd"),
    "qoder": ("bin/qoder.exe", "bin/qoder.cmd"),
    "workbuddy": ("resources/app.asar.unpacked/cli/dist/codebuddy-lite-wb.mjs",
                  "bin/codebuddy.exe", "bin/codebuddy.cmd"),
}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def fixed_drives():
    if os.name != "nt": return []
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    mask = kernel.GetLogicalDrives()
    return [Path(chr(65+i)+":/") for i in range(26)
            if mask & (1 << i) and kernel.GetDriveTypeW(chr(65+i)+":\\") == 3]


def registered_installs(provider):
    if os.name != "nt": return []
    import winreg
    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for prefix in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                       r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try: base = winreg.OpenKey(hive, prefix)
            except OSError: continue
            with base:
                for index in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, index)) as key:
                            def read(name):
                                try: return winreg.QueryValueEx(key, name)[0]
                                except OSError: return ""
                            name = read("DisplayName")
                            if not isinstance(name, str) or not any(token in normalized(name) for token in FAMILIES[provider]): continue
                            location, icon = read("InstallLocation"), read("DisplayIcon")
                            if isinstance(location, str) and location.strip(): result.append(Path(location.strip().strip('"')))
                            if isinstance(icon, str):
                                match = re.match(r'^"([^"]+\.exe)"|^(.+?\.exe)(?:,|$)', icon, re.I)
                                if match: result.append(Path(match[1] or match[2]).parent)
                    except OSError: continue
    return [path for path in result if not str(path).startswith("\\\\")]


def extra_cli_candidates(provider):
    """Inspect install roots and one directory level, never recurse over a drive."""
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    local = Path(os.environ.get("LOCALAPPDATA", str(profile/"AppData"/"Local")))
    roaming = Path(os.environ.get("APPDATA", str(profile/"AppData"/"Roaming")))
    roots = [local/"Programs", local, roaming,
             Path(os.environ.get("ProgramFiles", "C:/Program Files")),
             Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")), *fixed_drives()]
    installs = registered_installs(provider)
    override = os.environ.get(provider.upper()+"_INSTALL_DIR", "")
    if override: installs.insert(0, Path(override))
    for root in dict.fromkeys(roots):
        try:
            installs.extend(path for path in sorted(root.iterdir(), key=lambda p:p.name.casefold())
                if path.is_dir() and any(normalized(path.name).startswith(token) for token in FAMILIES[provider]))
        except OSError: continue
    candidates = []
    command = {"minimax_code":"mcode", "kimi_code":"kimi", "deepseek_harness":"dsh",
               "claude_code":"claude", "workbuddy":"codebuddy"}.get(provider, provider)
    for directory in dict.fromkeys(installs):
        candidates.extend(directory/relative for relative in RELATIVE_ENTRIES[provider])
    for folder in (profile/".local"/"bin", profile/".bun"/"bin", profile/".cargo"/"bin",
                   profile/("."+command)/"bin", roaming/"npm"):
        candidates.extend(folder/(command+suffix) for suffix in (".exe", ".cmd", ".ps1"))
    if provider == "kimi_code":
        candidates.append(profile/".local"/"share"/"uv"/"tools"/"kimi-cli"/"Scripts"/"kimi.exe")
        for root in (roaming/"Python", local/"Programs"/"Python"):
            if root.is_dir(): candidates.extend(root.glob("*/Scripts/kimi.exe"))
    return list(dict.fromkeys(candidates))
