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
- 支持直接粘贴链接下载：兼容从表格复制的 HTML 超链接和纯文本 URL。

## 安装依赖

```powershell
pip install -r requirements_google.txt
```

## 运行

```powershell
python sheets_batch_downloader_modern.py
```

## 粘贴链接下载

点击界面里的 `粘贴链接下载`，可以粘贴：

- 从 Google 表格复制的超链接单元格。
- HTML `<a href="...">文件名</a>` 格式。
- 普通纯文本链接，例如 `张三 https://drive.google.com/file/d/...`。

粘贴后可以先预览，也可以直接下载。纯 Drive 链接没有显示文件名时，程序会通过 Drive API 获取真实文件名。

粘贴链接下载不需要填写表格 ID、工作表或回填列；这些配置只用于 Google 表格读取模式。粘贴链接下载不会回填表格。

没有填写表格 ID 或没有选择工作表时，点击主界面的 `开始下载` 会自动进入粘贴链接下载模式。

如果已经点击 `预览粘贴` 并把链接显示在预览表格里，再点击主界面的 `开始下载`，会直接下载当前预览表格里的这批粘贴链接。

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
