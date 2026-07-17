# DIY下载器

一个用于从 Google 表格读取链接并批量下载文件的本地客户端。

## 主要功能

- 读取 Google 表格指定工作表。
- 默认按 `A` 列名称创建文件夹，按 `P` 列链接下载源文件。
- 支持筛选“只下载包含”的人名或组合名。
- 支持下载成功后回填到指定列，默认 `Q` 列。
- 支持跳过本地已存在文件。
- 支持保存多个配置方案，并一键按顺序执行所有方案。
- 支持暗黑模式切换、预览、停止、打开下载文件夹。

## 安装依赖

```powershell
pip install -r requirements_google.txt
```

## 运行

```powershell
python sheets_batch_downloader_modern.py
```

## Google 凭据

程序需要 Google OAuth 客户端 JSON 文件或服务账号 JSON 文件。

为了安全，仓库不要提交下面这些本地文件：

- `token.json`
- `谷歌服务账号.json`
- `credentials.json`
- `diy_downloader_configs.json`

首次运行时在界面里选择你的凭据文件即可。授权生成的 `token.json` 会保存在程序目录。

## 打包 exe

如果需要重新打包：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "DIY下载器" --icon "logo.ico" --add-data "assets;assets" --add-data "logo.png;." sheets_batch_downloader_modern.py
```

如果你希望把自己的凭据文件也打进 exe，可以额外添加：

```powershell
--add-data "谷歌服务账号.json;."
```

但不建议把包含凭据的 exe 或 JSON 上传到公开仓库。
