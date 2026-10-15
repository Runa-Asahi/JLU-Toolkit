"""用 PyInstaller 构建 dist/SuperJLU.exe。需要：pip install ".[desktop,captcha]" pyinstaller
图标使用 assets/brand/superjlu.ico（PR #27），缺失时构建失败。"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if __name__ == "__main__":
    if not (ROOT / "assets" / "brand" / "superjlu.ico").is_file():
        sys.exit("缺少 assets/brand/superjlu.ico（见 PR #27）")
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                           "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
                           str(HERE / "superjlu.spec")], cwd=ROOT)
    print(ROOT / "dist" / "SuperJLU.exe")
