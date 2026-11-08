"""本地启动入口（带端口预检与友好提示）。

为什么需要这段：报错信息不可读是排查问题的最大障碍。
uvicorn 在端口被占时只说「Errno 10048 通常每个套接字地址只允许使用一次」，
不会告诉你是哪个进程占的、改了什么、什么时候启动的。这里补上这一层。

换端口：set PORT=8010 && python run.py
生产/部署用 uvicorn CLI 或 gunicorn，不走这个入口。
"""

import os
import socket
import subprocess
import sys

DEFAULT_PORT = 8000


def _who_owns(port: int) -> dict | None:
    """端口被占则返回 {pid, cmd, started}，否则 None。

    查进程信息统一走 PowerShell 的 Get-NetTCPConnection + Get-CimInstance：
    wmic 在新版 Windows 已移除，且中文系统下编码容易炸。
    """
    ps = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -EA SilentlyContinue;"
        f"if ($c) {{ $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c[0].OwningProcess)\" -EA SilentlyContinue;"
        f" \"$($c[0].OwningProcess)`n$($p.CommandLine)`n$($p.CreationDate)\" }}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
        )
        lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        if len(lines) >= 1 and lines[0].isdigit():
            return {
                "pid": lines[0],
                "cmd": lines[1] if len(lines) > 1 else "（命令行读取失败）",
                "started": lines[2] if len(lines) > 2 else "",
            }
    except Exception as e:  # noqa: BLE001
        return {"pid": "?", "cmd": f"（查询失败：{e}）", "started": ""}
    return None


def _is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    port = int(os.getenv("PORT", str(DEFAULT_PORT)))

    if _is_listening(port):
        info = _who_owns(port) or {}
        print("\n" + "=" * 64)
        print(f"✗ 端口 {port} 已被占用，本次不启动")
        print("=" * 64)
        print(f"  占用进程PID : {info.get('pid', '?')}")
        print(f"  启动命令    : {info.get('cmd', '（未知）')}")
        if info.get("started"):
            print(f"  启动时间    : {info['started']}")
        print()
        print("  三种处理方式（选一种）：")
        print(f"    1) 直接用旧实例     → 打开 http://127.0.0.1:{port} 即可")
        print( "                          （但若之后改了代码，需重启才生效）")
        print(f"    2) 停掉旧进程再启动 →  taskkill /PID {info.get('pid', '?')} /F")
        print( "    3) 换端口启动       →  set PORT=8010 && python run.py")
        print("=" * 64 + "\n")
        sys.exit(3)

    import uvicorn
    print(f"启动服务：http://127.0.0.1:{port}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()