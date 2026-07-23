# DIY下载器 v1.1.5

## 安全扫描

- 修复 CodeQL `incomplete-url-substring-sanitization`（共 7 条高危）
- 域名判断改为严格 host 匹配，避免子串误匹配

## 继承 v1.1.4

- 安装启动 python312.dll 问题：onedir 目录安装
- 画质 / 环境检测 / 一键安装 ffmpeg 等功能

请使用本版 **windows-setup** 安装包，勿继续使用 v1.1.4 之前的单文件 exe。
