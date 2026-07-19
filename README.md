# DIY下载器

DIY下载器是一个 Windows 本地批量下载工具，用于从 Google 表格或手动粘贴的链接中读取 Google Drive 文件，并按规则自动命名、分组和下载。

## 主要功能

- 支持读取 Google 表格指定工作表，默认按 `A` 列判断系列/文件夹，按 `P` 列读取真实链接。
- 支持自定义名称列、链接列、回填列、起始行、结束行和筛选关键词。
- 支持粘贴链接下载，可兼容 Google 表格复制出来的超链接、HTML 链接和纯文本 URL。
- 粘贴链接预览时会先读取 Google Drive 真实文件名，预览后点击“开始下载”会直接下载当前预览内容。
- 支持 Google Drive 文件夹链接，检测到文件夹后会读取文件夹内文件并下载。
- 自动判断是否创建系列文件夹：同系列文件较多时创建文件夹，单独文件直接下载到所选目录。
- 文件下载后使用源文件名保存，不再使用行号命名。
- 支持已下载文件跳过、任务停止、打开下载文件夹、清空日志。
- 支持下载成功后回填名称到表格，默认回填 `Q` 列，失败不会写入。
- 支持多个配置方案保存、切换和按顺序执行。
- 支持浅色/深色模式切换。
- 授权 token 会保存在用户配置目录，已授权过的账号通常不需要每次重新授权。

## 使用方法

1. 准备 Google OAuth 凭据或服务账号 JSON 文件。
2. 打开程序，选择凭据文件和下载目录。
3. 使用表格模式时，填写 Google 表格 ID，点击“加载工作表”，选择对应工作表后预览或下载。
4. 使用粘贴模式时，点击“粘贴链接下载”，从剪贴板读取或手动粘贴链接，先预览再下载。
5. 如果勾选“下载成功后回填表格”，成功下载后会把匹配到的名称写入指定回填列。

## 授权说明

程序会优先复用已经授权过的 token。默认保存位置：

```text
C:\Users\当前用户\AppData\Roaming\DIY下载器\token.json
```

如果使用同一个公用邮箱授权，并且权限范围没有变化，复制或保留该 token 后通常不需要每个人都重新授权。

注意：`token.json` 等同于授权凭据，不要上传到公开仓库。

## 安全文件

请不要把以下本地文件上传到公开仓库：

- `token.json`
- `谷歌服务账号.json`
- `credentials.json`
- `diy_downloader_configs.json`
- 任何包含 Google、GitHub 或其它私密凭据的 JSON / env 文件

## 本地运行

```powershell
pip install -r requirements_google.txt
python sheets_batch_downloader_modern.py
```

## GitHub 安全发布

仓库已配置 `.github/workflows/release.yml`，发布方式为推送版本 tag：

```powershell
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions 会在 `windows-latest` runner 上构建 Windows exe，生成 Artifact Attestation，并使用默认 `GITHUB_TOKEN` 创建 Release 和上传产物。

请不要手动在 GitHub Release 页面拖拽上传产物，否则平台的 L2 Attestation 校验可能失败。
