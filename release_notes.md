# DIY下载器 v1.1.3

## 新增：环境检测与一键安装组件

- YouTube/FB 视频页新增 **检测环境 / 一键安装组件**
- 自动检测 yt-dlp、ffmpeg、ffprobe
- 缺少 ffmpeg 时可一键下载便携版并安装到用户目录
- 开始下载前若缺组件会提示安装
- 高清合并必需 ffmpeg；安装后无需手动配 PATH（程序会自动查找）

安装位置：`%LOCALAPPDATA%\DIYDownloader\tools\bin`
