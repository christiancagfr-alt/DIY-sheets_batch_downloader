# DIY下载器 v1.1.4

## 修复：安装后启动报 python312.dll / Failed to load Python DLL

### 原因
旧版使用 **PyInstaller onefile（单文件 exe）**。  
每次启动都要把运行库解压到：

`%TEMP%\_MEIxxxxx\python312.dll`

在部分电脑上会因杀毒拦截、临时目录权限、依赖 DLL 缺失等导致：

> Failed to load Python DLL ... LoadLibrary: 找不到指定的模块

### 本版改动
- 改为 **onedir 目录版** 打包：DLL 与 exe 同目录安装，**不再每次从 Temp 解压**
- 安装包安装到：`%LOCALAPPDATA%\DIYDownloader\`
- 便携版改为 **zip 目录包**（解压即用）
- 禁用 UPX 压缩，降低启动失败概率

### 请这样安装
1. **卸载/删除** 旧版（含以前的单文件 DIYDownloader.exe）
2. 下载 **`DIYDownloader-v1.1.4-windows-setup.exe`** 重新安装
3. 从开始菜单或桌面快捷方式启动

若仍提示缺少运行库，请安装 Microsoft **Visual C++ 2015–2022 x64** 可再发行组件：  
https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
