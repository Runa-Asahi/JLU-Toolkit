"""PyInstaller 入口：SuperJLU.exe（窗口模式，托盘 + 浏览器）。"""
import sys

from jlu_toolkit.desktop import main

if __name__ == "__main__":
    sys.exit(main())
