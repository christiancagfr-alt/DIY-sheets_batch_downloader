# DIY下载器 v1.1.1

## 修复

- **修复 YouTube 画质模糊 / 选 1080p 仍很低清的问题**
  - 移除会把格式限制在约 360p 的 android player client
  - 高清强制使用 bestvideo+bestaudio 合并，不再静默降到单文件 360p
  - 改进 ffmpeg 探测；日志显示实际选中的分辨率与编码

请确保本机已安装 ffmpeg 并加入 PATH，否则无法合并真正的 1080p 视频轨。
