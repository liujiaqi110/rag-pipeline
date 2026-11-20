# Python 虚拟环境使用指南

本文是为本项目编写的原创示例文档，用于演示检索与问答流程，内容为通用的 Python 开发常识。

## 为什么需要虚拟环境

不同项目可能依赖同一个库的不同版本。如果所有包都安装到系统 Python 中，升级一个项目的依赖就可能破坏另一个项目。虚拟环境为每个项目提供独立的 site-packages 目录和解释器入口，使依赖互不干扰，也便于在新机器上复现相同的环境。

## 创建虚拟环境

Python 3 自带 venv 模块，在项目目录中执行 python -m venv .venv 即可创建名为 .venv 的虚拟环境。目录名可以自定义，但 .venv 是常见约定，很多编辑器会自动识别。虚拟环境目录不应提交到版本库，应写入 .gitignore。

## 激活与退出

在 Windows PowerShell 中执行 .venv\Scripts\Activate.ps1 激活环境；在 Linux 或 macOS 中执行 source .venv/bin/activate。激活后命令提示符前会出现环境名，此时运行的 python 和 pip 都指向虚拟环境。执行 deactivate 即可退出。不激活也可以直接调用虚拟环境里的解释器路径来运行程序。

## 记录与安装依赖

在虚拟环境中执行 pip freeze > requirements.txt 可以把当前安装的包及其精确版本写入文件。在另一台机器上创建新的虚拟环境后，执行 pip install -r requirements.txt 即可安装同样版本的依赖。固定版本号能减少“在我机器上可以运行”的问题。

## 常见问题

PowerShell 提示无法加载激活脚本时，通常是执行策略限制，可以只对当前用户放宽策略，或者不激活而直接使用解释器的完整路径。删除虚拟环境时直接删除整个目录即可，它不会影响系统 Python。
