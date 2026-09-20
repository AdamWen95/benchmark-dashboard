# 开发环境

使用 Python 3.13 或更新版本，在项目根目录创建独立 `.venv`。Windows、Linux 和 macOS 的安装及启动命令见 [README](../README.md)。系统 Python 升级后应重新创建环境，不要把另一操作系统的虚拟环境复制到部署机器。

直接依赖的版本范围位于 [requirements.in](../requirements.in)，完整依赖版本位于 [requirements.txt](../requirements.txt)。主要组件为 Streamlit、pandas、requests、python-dotenv 和 pytest；SQLite 随 Python 提供。

安装后使用当前虚拟环境的解释器运行 `-m pip check` 和离线测试，确认本机组合可用。不同平台和 Python 版本的实际兼容性以测试结果为准。虚拟环境、pip 缓存、测试缓存和本地生成文件不提交到仓库。

服务器上的 systemd 和 Nginx 示例仅适用于采用相应组件的 Linux 环境，见 [部署说明](deployment.md)。它们不属于本地网页启动的必要条件。
