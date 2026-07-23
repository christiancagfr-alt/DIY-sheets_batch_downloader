"""YouTube / Facebook 视频批量下载板块（基于 yt-dlp）。

支持：
- YouTube 单视频 / 播放列表
- Facebook 单视频 / Reels / 可识别的播放清单
- 多链接批量
- 断点续传（.part 续传、已完成跳过）
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None


APP_SECTION = "YouTube / FB 视频"
URL_RE = re.compile(r"https?://[^\s<>\"'，。；、]+", re.I)
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 真实播放列表前缀；RD/UL 等为电台/混播，默认不当整表下载
YOUTUBE_REAL_PLAYLIST_PREFIXES = ("PL", "UU", "FL", "OL", "LL", "WL", "TL")

# 格式串说明：
# - YouTube 高清几乎都是「视频轨 + 音频轨」分离，必须用 bestvideo+bestaudio 再合并
# - 单文件 progressive（format 18 等）最高通常只有 360p，绝不能当 1080p 用
# - 优先 avc1+m4a 便于合成兼容性好的 mp4；没有则回退 vp9/av1 + 最佳音轨
def _height_format(max_h: int) -> str:
    return (
        f"bestvideo[height<={max_h}][vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={max_h}][vcodec^=avc1]+bestaudio/"
        f"bestvideo[height<={max_h}]+bestaudio/"
        f"best[height<={max_h}]/"
        f"bestvideo+bestaudio/best"
    )


QUALITY_OPTIONS = {
    "最佳质量": (
        "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
        "bestvideo+bestaudio/best"
    ),
    "最高 1080p": _height_format(1080),
    "最高 720p": _height_format(720),
    "最高 480p": _height_format(480),
    "仅音频": "bestaudio/best",
}

# 下载模式
MODE_AUTO = "自动识别（推荐）"
MODE_SINGLE = "仅单视频"
MODE_PLAYLIST = "展开播放列表/清单"

DOWNLOAD_MODES = [MODE_AUTO, MODE_SINGLE, MODE_PLAYLIST]


@dataclass
class VideoItem:
    index: int
    url: str
    platform: str
    title: str = ""
    duration: str = ""
    status: str = "待处理"
    filepath: str = ""
    source: str = ""  # 来源：单视频 / 播放列表名
    playlist_title: str = ""
    playlist_index: int = 0
    video_id: str = ""
    is_partial: bool = False


def sanitize_filename(value: str) -> str:
    text = str(value or "video")
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text[:180] or "video")


def detect_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "youtube.com" in host or "youtu.be" in host or "youtube-nocookie.com" in host:
        return "YouTube"
    if "facebook.com" in host or "fb.watch" in host or host.endswith("fb.com") or "fbcdn.net" in host:
        return "Facebook"
    return "其他"


def extract_youtube_video_id(url: str) -> str:
    text = unquote(str(url or "").strip())
    if not text:
        return ""
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query)

    for key in ("v", "video_id"):
        values = query.get(key) or []
        if values and YOUTUBE_ID_RE.fullmatch(values[0]):
            return values[0]

    if "youtu.be" in host:
        part = path.strip("/").split("/")[0]
        if YOUTUBE_ID_RE.fullmatch(part):
            return part

    match = re.search(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)", path)
    if match:
        return match.group(1)

    match = re.search(r"(?:^|[?&#])v=([A-Za-z0-9_-]{11})(?:[&#]|$)", text)
    if match:
        return match.group(1)
    return ""


def extract_youtube_list_id(url: str) -> str:
    text = unquote(str(url or "").strip())
    if not text:
        return ""
    qs = parse_qs(urlparse(text).query)
    values = qs.get("list") or []
    return values[0] if values else ""


def is_youtube_real_playlist_id(list_id: str) -> bool:
    text = str(list_id or "").strip()
    if not text:
        return False
    return text.startswith(YOUTUBE_REAL_PLAYLIST_PREFIXES)


def is_youtube_radio_list_id(list_id: str) -> bool:
    text = str(list_id or "").strip()
    return bool(text) and (text.startswith("RD") or text.startswith("UL") or text.startswith("RDMM"))


def clean_raw_url(url: str) -> str:
    raw = str(url or "").strip()
    raw = raw.strip(" \t\r\n\"'<>")
    raw = raw.rstrip(").,;]}>\"'")
    return raw


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_playlist_url(list_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={list_id}"


def normalize_facebook_url(url: str) -> str:
    raw = clean_raw_url(url)
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    qs = parse_qs(parsed.query)

    if "fb.watch" in host:
        return raw.split("?")[0].rstrip("/")

    # /reel/ID or /reels/ID
    match = re.search(r"/reels?/([0-9A-Za-z._-]+)", path)
    if match:
        return f"https://www.facebook.com/reel/{match.group(1)}"

    if qs.get("v"):
        return f"https://www.facebook.com/watch/?v={qs['v'][0]}"

    # 多视频 / 相册类保留原路径，去掉常见追踪参数
    if any(x in path for x in ("/videos/", "/watch/", "/share/", "/groups/")):
        base = f"{parsed.scheme}://{parsed.netloc}{path}"
        keep = []
        for key in ("v", "story_fbid", "id", "set", "theater"):
            if key in qs and qs[key]:
                keep.append(f"{key}={qs[key][0]}")
        if keep:
            return base + "?" + "&".join(keep)
        return base.rstrip("/")

    return raw


def prepare_source_url(url: str, mode: str) -> tuple[str, str, bool]:
    """
    返回 (最终用于解析的 URL, 链接类型说明, 是否应按播放列表展开)。
    """
    raw = clean_raw_url(url)
    if not raw.startswith("http"):
        return "", "无效", False

    platform = detect_platform(raw)

    if platform == "YouTube":
        video_id = extract_youtube_video_id(raw)
        list_id = extract_youtube_list_id(raw)
        path = (urlparse(raw).path or "").lower()
        is_playlist_page = "playlist" in path and bool(list_id)

        if mode == MODE_SINGLE:
            if video_id:
                return youtube_watch_url(video_id), "单视频", False
            if is_playlist_page:
                # 仅单视频模式下，纯播放列表仍展开（否则无内容）
                return youtube_playlist_url(list_id), "播放列表", True
            return raw, "单视频", False

        if mode == MODE_PLAYLIST:
            if list_id:
                # 强制按 list 展开（含电台混播，可能很长）
                if is_playlist_page or not video_id:
                    return youtube_playlist_url(list_id), "播放列表", True
                return f"{youtube_watch_url(video_id)}&list={list_id}", "播放列表", True
            if video_id:
                return youtube_watch_url(video_id), "单视频", False
            return raw, "链接", True

        # MODE_AUTO
        if is_playlist_page and list_id:
            return youtube_playlist_url(list_id), "播放列表", True
        if list_id and is_youtube_real_playlist_id(list_id):
            # watch?v=x&list=PLxxx → 整表
            return youtube_playlist_url(list_id), "播放列表", True
        if video_id:
            # 电台混播 / 普通单视频 → 只下当前视频
            return youtube_watch_url(video_id), "单视频", False
        if list_id:
            return youtube_playlist_url(list_id), "播放列表", True
        return raw, "链接", False

    if platform == "Facebook":
        fb = normalize_facebook_url(raw)
        path = (urlparse(fb).path or "").lower()
        # 看起来像合集/多内容的路径，尝试展开
        looks_like_set = any(
            token in path
            for token in ("/set/", "/playlist", "/videos_by", "/reels_tab", "/reels/")
        ) or "set=" in fb
        # /reel/xxx 通常是单条；/reels/ 页可能是列表
        if re.search(r"/reel/[0-9A-Za-z._-]+/?$", path):
            looks_like_set = False

        if mode == MODE_SINGLE:
            return fb, "单视频", False
        if mode == MODE_PLAYLIST:
            return fb, "清单/视频", True
        # AUTO：单 reel/watch 不强制展开；合集类允许展开
        return fb, "清单" if looks_like_set else "单视频", looks_like_set

    return raw, "链接", mode != MODE_SINGLE


def extract_urls(text: str) -> list[str]:
    """提取原始链接（保留 list 等参数，后续按模式处理）。"""
    seen = set()
    urls = []
    for match in URL_RE.findall(str(text or "")):
        url = clean_raw_url(match)
        if not url.startswith("http"):
            continue
        # 轻量规范化 FB 追踪参数，但 YT 保留 list
        if detect_platform(url) == "Facebook":
            url = normalize_facebook_url(url)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def default_ydl_opts(**extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 45,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "extractor_retries": 3,
        "concurrent_fragment_downloads": 4,
        # 断点续传核心
        "continuedl": True,
        "nopart": False,
        "updatetime": False,
        # 优先高分辨率 / 高码率；不要用 android client（会只剩 360p 左右）
        "format_sort": ["res", "fps", "hdr:12", "vbr", "abr", "tbr", "size"],
        "format_sort_force": True,
    }
    ffmpeg = resolve_ffmpeg_path()
    if ffmpeg:
        # yt-dlp 接受 ffmpeg 可执行文件所在目录
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
    opts.update(extra)
    return opts


def format_duration(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_ffmpeg_path() -> str:
    """查找可用 ffmpeg，避免因找不到工具而静默降到 360p 单文件。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 常见便携/自定义安装路径
    candidates = [
        r"D:\software\audiokit\ffmpeg_win\ffmpeg.EXE",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.path.expanduser("~"), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe"),
    ]
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        candidates.extend([
            os.path.join(base, "ffmpeg.exe"),
            os.path.join(base, "bin", "ffmpeg.exe"),
            os.path.join(getattr(sys, "_MEIPASS", base), "ffmpeg.exe"),
        ])
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    try:
        import imageio_ffmpeg  # type: ignore
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return ""


def has_ffmpeg() -> bool:
    return bool(resolve_ffmpeg_path())


def describe_selected_format(info: dict) -> str:
    """用于日志：显示最终选中的分辨率/编码，方便排查画质问题。"""
    if not info:
        return "未知"
    parts = []
    req = info.get("requested_formats")
    if req:
        for f in req:
            h = f.get("height")
            v = f.get("vcodec") or "none"
            a = f.get("acodec") or "none"
            fid = f.get("format_id")
            note = f.get("format_note") or ""
            if v != "none":
                parts.append(f"视频 {h or '?'}p/{fid}/{v}/{note}".strip("/"))
            if a != "none":
                parts.append(f"音频 {fid}/{a}")
    else:
        h = info.get("height")
        parts.append(
            f"{h or '?'}p id={info.get('format_id')} "
            f"v={info.get('vcodec')} a={info.get('acodec')}"
        )
    return " + ".join(parts) if parts else "未知"

def entry_to_url(entry: dict, fallback: str = "") -> str:
    if not entry:
        return fallback
    for key in ("webpage_url", "original_url", "url"):
        value = entry.get(key)
        if value and str(value).startswith("http"):
            return str(value)
    video_id = entry.get("id")
    # YouTube flat entry
    if video_id and YOUTUBE_ID_RE.fullmatch(str(video_id)):
        return youtube_watch_url(str(video_id))
    if video_id and str(video_id).isdigit():
        # Facebook numeric id 常见
        return f"https://www.facebook.com/reel/{video_id}"
    return fallback


def find_existing_or_partial(target_dir: str, title: str, video_id: str) -> tuple[str, bool]:
    """
    返回 (path, is_complete)。
    完整文件优先；否则返回 .part 以便续传提示。
    """
    if not os.path.isdir(target_dir):
        return "", False

    safe_title = sanitize_filename(title)
    complete = []
    partial = []

    for name in os.listdir(target_dir):
        path = os.path.join(target_dir, name)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            continue
        matched = False
        if video_id and f"[{video_id}]" in name:
            matched = True
        elif safe_title and (name.startswith(safe_title) or safe_title in name):
            matched = True
        if not matched:
            continue
        if name.endswith(".part"):
            partial.append(path)
        else:
            # 跳过 yt-dlp 中间分片 f401/f251 等未合并文件时的误判：仅当无主文件时才算
            if re.search(r"\.f\d+\.(mp4|webm|m4a)$", name, re.I):
                partial.append(path)
            else:
                complete.append(path)

    if complete:
        # 优先非临时扩展
        complete.sort(key=lambda p: (0 if p.lower().endswith((".mp4", ".webm", ".mkv", ".mp3", ".m4a")) else 1, -os.path.getsize(p)))
        return complete[0], True
    if partial:
        partial.sort(key=lambda p: -os.path.getsize(p))
        return partial[0], False
    return "", False


def expand_source_urls(
    urls: list[str],
    mode: str,
    playlist_limit: int = 0,
    stop_event: threading.Event | None = None,
    on_item: Callable[[VideoItem], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[VideoItem]:
    """
    自动解析并展开单视频 / 播放列表 / FB 清单。
    下载前无需单独预览，开始下载时会调用本函数。
    """
    def log(msg: str):
        if on_log:
            on_log(msg)

    if yt_dlp is None:
        raise RuntimeError("未安装 yt-dlp。请执行：pip install yt-dlp")

    items: list[VideoItem] = []
    index = 0
    limit = max(0, int(playlist_limit or 0))

    for raw_url in urls:
        if stop_event is not None and stop_event.is_set():
            log("加载已停止。")
            break

        source_url, kind, expand = prepare_source_url(raw_url, mode)
        platform = detect_platform(source_url or raw_url)
        if source_url != raw_url:
            log(f"自动加载 [{kind}]：{raw_url} -> {source_url}")
        else:
            log(f"自动加载 [{platform}/{kind}]：{source_url}")

        opts = default_ydl_opts(
            skip_download=True,
            extract_flat="in_playlist",
            noplaylist=not expand,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=False)
        except Exception as exc:
            index += 1
            item = VideoItem(
                index=index,
                url=source_url or raw_url,
                platform=platform,
                title=f"链接-{index}",
                status=f"解析失败：{exc}",
                source=kind,
            )
            items.append(item)
            if on_item:
                on_item(item)
            log(f"解析失败：{source_url} -> {exc}")
            continue

        if info is None:
            index += 1
            item = VideoItem(
                index=index,
                url=source_url,
                platform=platform,
                title=f"链接-{index}",
                status="解析失败：无结果",
                source=kind,
            )
            items.append(item)
            if on_item:
                on_item(item)
            continue

        playlist_title = ""
        if info.get("_type") == "playlist" or (expand and info.get("entries") is not None):
            playlist_title = sanitize_filename(info.get("title") or info.get("id") or "播放列表")
            entries = [e for e in (info.get("entries") or []) if e]
            if limit > 0:
                entries = entries[:limit]
            if not entries and info.get("id") and info.get("_type") != "playlist":
                entries = [info]
            log(f"播放列表/清单「{playlist_title}」自动展开 {len(entries)} 项。")
        else:
            entries = [info]

        if not entries:
            index += 1
            item = VideoItem(
                index=index,
                url=source_url,
                platform=platform,
                title=playlist_title or f"链接-{index}",
                status="解析失败：列表为空（可能需登录或无公开权限）",
                source=kind,
                playlist_title=playlist_title,
            )
            items.append(item)
            if on_item:
                on_item(item)
            continue

        for pos, entry in enumerate(entries, start=1):
            if stop_event is not None and stop_event.is_set():
                break
            index += 1
            entry_url = entry_to_url(entry, source_url)
            if detect_platform(entry_url) == "YouTube":
                vid = extract_youtube_video_id(entry_url) or (
                    entry.get("id") if YOUTUBE_ID_RE.fullmatch(str(entry.get("id") or "")) else ""
                )
                if vid:
                    entry_url = youtube_watch_url(str(vid))

            title = sanitize_filename(
                entry.get("title")
                or entry.get("id")
                or f"{playlist_title or '视频'}-{pos}"
            )
            video_id = str(entry.get("id") or extract_youtube_video_id(entry_url) or "")
            item = VideoItem(
                index=index,
                url=entry_url,
                platform=detect_platform(entry_url) or platform,
                title=title,
                duration=format_duration(entry.get("duration")),
                status="待下载",
                source=playlist_title if playlist_title else kind,
                playlist_title=playlist_title,
                playlist_index=pos if playlist_title else 0,
                video_id=video_id,
            )
            items.append(item)
            if on_item:
                on_item(item)

        if playlist_title:
            log(f"已自动展开「{playlist_title}」→ {len(entries)} 个视频")
        elif items:
            log(f"加载成功 [{platform}] {items[-1].title}")

    return items


class Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.layout.addWidget(title_label)


class VideoPreviewWorker(QThread):
    log = Signal(str)
    failed = Signal(str)
    item_ready = Signal(object)
    done = Signal(list)

    def __init__(self, urls: list[str], mode: str, playlist_limit: int):
        super().__init__()
        self.urls = urls
        self.mode = mode
        self.playlist_limit = max(0, int(playlist_limit or 0))
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        if yt_dlp is None:
            self.failed.emit("未安装 yt-dlp。请执行：pip install yt-dlp")
            self.done.emit([])
            return
        try:
            items = expand_source_urls(
                self.urls,
                self.mode,
                self.playlist_limit,
                stop_event=self.stop_event,
                on_item=lambda item: self.item_ready.emit(item),
                on_log=lambda msg: self.log.emit(msg),
            )
            self.done.emit(items)
        except Exception as exc:
            self.failed.emit(f"预览失败：{exc}")
            self.done.emit([])


class VideoDownloadWorker(QThread):
    log = Signal(str)
    failed = Signal(str)
    item_update = Signal(object)
    progress = Signal(dict)
    done = Signal()

    def __init__(
        self,
        source_urls: list[str],
        output_dir: str,
        quality_key: str,
        skip_existing: bool,
        split_by_platform: bool,
        playlist_subfolder: bool,
        resume: bool,
        mode: str,
        playlist_limit: int,
    ):
        super().__init__()
        self.source_urls = list(source_urls)
        self.items: list[VideoItem] = []
        self.output_dir = output_dir
        self.quality_key = quality_key
        self.skip_existing = skip_existing
        self.split_by_platform = split_by_platform
        self.playlist_subfolder = playlist_subfolder
        self.resume = resume
        self.mode = mode
        self.playlist_limit = max(0, int(playlist_limit or 0))
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def _target_dir(self, item: VideoItem) -> str:
        parts = [self.output_dir]
        if self.split_by_platform and item.platform in ("YouTube", "Facebook"):
            parts.append(item.platform)
        if self.playlist_subfolder and item.playlist_title:
            parts.append(sanitize_filename(item.playlist_title))
        path = os.path.join(*parts)
        os.makedirs(path, exist_ok=True)
        return path

    def _outtmpl(self, target_dir: str, item: VideoItem) -> str:
        if item.playlist_index:
            # 播放列表按序号命名，方便排序与续传识别
            name = f"{item.playlist_index:03d} - %(title).160B [%(id)s].%(ext)s"
        else:
            name = "%(title).180B [%(id)s].%(ext)s"
        return os.path.join(target_dir, name)

    def _build_opts(self, target_dir: str, item: VideoItem, progress_hook: Callable) -> dict:
        fmt = QUALITY_OPTIONS.get(self.quality_key, QUALITY_OPTIONS["最佳质量"])
        ffmpeg = resolve_ffmpeg_path()
        opts = default_ydl_opts(
            outtmpl=self._outtmpl(target_dir, item),
            format=fmt,
            progress_hooks=[progress_hook],
            ignoreerrors=False,
            continuedl=self.resume,
            noplaylist=True,  # 单项下载，列表已在开始时自动展开
            noprogress=True,
            # 断点续传相关
            overwrites=not self.skip_existing,
            # 网络不稳时更耐用
            retries=15,
            fragment_retries=15,
        )
        if ffmpeg:
            opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
            opts["merge_output_format"] = "mp4"
            self.log.emit(f"使用 ffmpeg：{ffmpeg}")
        else:
            # 切勿降级为单文件 b（YouTube 常只有 360p）
            self.log.emit(
                "警告：未检测到 ffmpeg。YouTube 1080p/高清需要合并视频+音频，"
                "缺少 ffmpeg 时可能失败或画质很差。请安装 ffmpeg 并加入 PATH。"
            )
        if self.quality_key == "仅音频":
            if ffmpeg:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            else:
                self.log.emit("未检测到 ffmpeg，仅音频将保留原始音频格式。")
        return opts

    def run(self):
        if yt_dlp is None:
            self.failed.emit("未安装 yt-dlp。请执行：pip install yt-dlp")
            self.done.emit()
            return

        success = skipped = failed = resumed = 0
        os.makedirs(self.output_dir, exist_ok=True)

        # 自动加载：单视频 / 播放列表无需先点解析预览
        self.log.emit(f"正在自动加载 {len(self.source_urls)} 个链接（{self.mode}）...")
        try:
            items = expand_source_urls(
                self.source_urls,
                self.mode,
                self.playlist_limit,
                stop_event=self.stop_event,
                on_item=lambda item: self.item_update.emit(item),
                on_log=lambda msg: self.log.emit(msg),
            )
        except Exception as exc:
            self.failed.emit(f"自动加载失败：{exc}")
            self.done.emit()
            return

        self.items = items
        downloadable = [i for i in items if not str(i.status).startswith("解析失败")]
        self.log.emit(
            f"自动加载完成：共 {len(items)} 项，可下载 {len(downloadable)} | "
            f"断点续传：{'开' if self.resume else '关'} | 目录：{self.output_dir}"
        )
        if not downloadable:
            self.log.emit("没有可下载的视频。")
            self.done.emit()
            return

        for item in downloadable:
            if self.stop_event.is_set():
                self.log.emit("任务已停止。未完成的文件已保留，可再次开始续传。")
                break

            item.status = "下载中"
            self.item_update.emit(item)
            target_dir = self._target_dir(item)
            last_file = {"path": ""}

            def hook(d, current=item, file_box=last_file):
                if self.stop_event.is_set():
                    raise Exception("用户停止下载")
                status = d.get("status")
                if status == "downloading":
                    percent = (d.get("_percent_str") or "").strip()
                    speed = (d.get("_speed_str") or "").strip()
                    eta = (d.get("_eta_str") or "").strip()
                    downloaded = d.get("downloaded_bytes") or 0
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    if total and downloaded:
                        # yt-dlp 续传时 percent 会从已有进度继续
                        current.status = f"下载中 {percent} {speed} ETA {eta}".strip()
                    else:
                        current.status = f"下载中 {percent} {speed}".strip()
                    self.item_update.emit(current)
                elif status == "finished":
                    filename = d.get("filename") or ""
                    if filename:
                        file_box["path"] = filename
                    current.status = "处理中/合并"
                    self.item_update.emit(current)

            try:
                # 规范化单条 URL
                if detect_platform(item.url) == "YouTube":
                    vid = extract_youtube_video_id(item.url) or item.video_id
                    if vid and YOUTUBE_ID_RE.fullmatch(str(vid)):
                        item.url = youtube_watch_url(str(vid))
                        item.video_id = str(vid)
                elif detect_platform(item.url) == "Facebook":
                    item.url = normalize_facebook_url(item.url)

                path, is_complete = find_existing_or_partial(
                    target_dir, item.title, item.video_id or extract_youtube_video_id(item.url)
                )
                if path and is_complete and self.skip_existing:
                    item.filepath = path
                    item.status = "已存在，跳过"
                    skipped += 1
                    self.item_update.emit(item)
                    self.progress.emit({
                        "success": success, "skipped": skipped, "failed": failed, "resumed": resumed,
                    })
                    self.log.emit(f"已存在，跳过：{path}")
                    continue
                if path and not is_complete and self.resume:
                    resumed += 1
                    item.is_partial = True
                    item.status = "断点续传中"
                    self.item_update.emit(item)
                    self.log.emit(f"发现未完成文件，继续下载：{path}")

                opts = self._build_opts(target_dir, item, hook)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    # 先解析并确认实际选中的分辨率（避免误下 360p）
                    info = ydl.extract_info(item.url, download=False)
                    if info:
                        if info.get("_type") == "playlist":
                            entries = [e for e in (info.get("entries") or []) if e]
                            info = entries[0] if entries else info
                        item.title = sanitize_filename(info.get("title") or item.title)
                        item.duration = item.duration or format_duration(info.get("duration"))
                        item.video_id = str(info.get("id") or item.video_id or "")
                        if detect_platform(item.url) == "YouTube" and item.video_id and YOUTUBE_ID_RE.fullmatch(item.video_id):
                            item.url = youtube_watch_url(item.video_id)
                        fmt_desc = describe_selected_format(info)
                        self.log.emit(f"画质选择 [{self.quality_key}]：{fmt_desc}")
                        # 若期望 720/1080 却只拿到很低分辨率，给出明确警告
                        sel_h = 0
                        for f in (info.get("requested_formats") or [info]):
                            try:
                                sel_h = max(sel_h, int(f.get("height") or 0))
                            except (TypeError, ValueError):
                                pass
                        want = 0
                        if "1080" in self.quality_key:
                            want = 1080
                        elif "720" in self.quality_key:
                            want = 720
                        elif "480" in self.quality_key:
                            want = 480
                        if want and sel_h and sel_h < min(want, 720) and sel_h <= 360:
                            self.log.emit(
                                f"警告：期望约 {want}p，实际仅 {sel_h}p。"
                                "请确认已安装 ffmpeg，并检查源视频是否提供该分辨率。"
                            )

                    ydl.download([item.url])

                saved = last_file["path"] or find_existing_or_partial(
                    target_dir, item.title, item.video_id
                )[0]
                # 合并后清理误指向中间分片
                if saved and re.search(r"\.f\d+\.(mp4|webm|m4a)(\.part)?$", saved, re.I):
                    final_path, complete = find_existing_or_partial(target_dir, item.title, item.video_id)
                    if final_path and complete:
                        saved = final_path

                item.filepath = saved
                item.status = "成功"
                success += 1
                self.item_update.emit(item)
                self.progress.emit({
                    "success": success, "skipped": skipped, "failed": failed, "resumed": resumed,
                })
                self.log.emit(f"成功：{saved or item.title}")
                time.sleep(0.05)
            except Exception as exc:
                if self.stop_event.is_set() or "用户停止" in str(exc):
                    item.status = "已停止（可续传）"
                    self.item_update.emit(item)
                    self.log.emit("任务已停止。未完成文件已保留，下次开始可断点续传。")
                    break
                failed += 1
                item.status = f"失败：{exc}"
                self.item_update.emit(item)
                self.progress.emit({
                    "success": success, "skipped": skipped, "failed": failed, "resumed": resumed,
                })
                self.log.emit(f"失败：{item.url} -> {exc}")

        self.log.emit(
            f"完成：成功 {success}，跳过 {skipped}，续传 {resumed}，失败 {failed}。"
        )
        self.done.emit()


class VideoBatchPage(QWidget):
    """独立的 YouTube / Facebook 批量下载板块。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.preview_items: list[VideoItem] = []
        self.build_ui()
        self.connect_signals()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        tip = QLabel(
            "粘贴链接后可直接点「开始下载」：会自动加载单视频/播放列表并开始下载，"
            "无需先解析。也可先点「解析预览」只看列表不下载。"
        )
        tip.setObjectName("subtitle")
        tip.setWordWrap(True)
        root.addWidget(tip)

        settings = QFrame()
        settings.setObjectName("compactPanel")
        grid = QGridLayout(settings)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        root.addWidget(settings)

        self.output_edit = QLineEdit(os.path.join(os.path.expanduser("~"), "Downloads", "视频批量下载"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_OPTIONS.keys()))
        self.quality_combo.setCurrentText("最佳质量")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(DOWNLOAD_MODES)
        self.mode_combo.setCurrentText(MODE_AUTO)
        self.mode_combo.setToolTip(
            "自动识别：真实播放列表(PL/UU等)整表下载；电台混播(RD)只下当前视频。\n"
            "仅单视频：忽略列表参数，只下载当前视频。\n"
            "展开播放列表/清单：强制展开 YouTube 列表与 FB 可识别清单。"
        )

        self.playlist_limit_spin = QSpinBox()
        self.playlist_limit_spin.setRange(0, 5000)
        self.playlist_limit_spin.setValue(0)
        self.playlist_limit_spin.setSpecialValueText("不限制")
        self.playlist_limit_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.playlist_limit_spin.setToolTip("0 表示不限制。可防止超长列表一次下太多。")

        self.skip_existing_check = QCheckBox("已完成则跳过")
        self.skip_existing_check.setChecked(True)
        self.resume_check = QCheckBox("断点续传")
        self.resume_check.setChecked(True)
        self.resume_check.setToolTip("保留 .part 临时文件，中断后再次开始可从断点继续。")
        self.split_platform_check = QCheckBox("按平台分文件夹")
        self.split_platform_check.setChecked(True)
        self.playlist_folder_check = QCheckBox("播放列表分子文件夹")
        self.playlist_folder_check.setChecked(True)

        self._add_field(grid, "下载目录", self.output_edit, 0, 0, 1, 4)
        choose_btn = QPushButton("选择")
        choose_btn.setObjectName("secondaryButton")
        choose_btn.clicked.connect(self.choose_output_dir)
        grid.addWidget(self._wrap_button(choose_btn), 0, 4)
        self._add_field(grid, "画质", self.quality_combo, 0, 5)

        self._add_field(grid, "下载模式", self.mode_combo, 1, 0, 1, 2)
        self._add_field(grid, "列表上限", self.playlist_limit_spin, 1, 2)

        options = QHBoxLayout()
        options.setSpacing(12)
        options.addWidget(self.resume_check)
        options.addWidget(self.skip_existing_check)
        options.addWidget(self.split_platform_check)
        options.addWidget(self.playlist_folder_check)
        options.addStretch()
        grid.addLayout(options, 1, 3, 1, 3)

        for col in range(6):
            grid.setColumnStretch(col, 1)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 5)

        left = Card("视频 / 播放列表链接（支持批量）")
        right_preview = Card("预览 / 进度")
        right_log = Card("日志")
        body.addWidget(left, 4)
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.addWidget(right_preview, 3)
        right_col.addWidget(right_log, 2)
        body.addLayout(right_col, 6)

        self.links_edit = QTextEdit()
        self.links_edit.setObjectName("pasteTextBox")
        self.links_edit.setPlaceholderText(
            "每行一个链接，可混合粘贴多个，例如：\n\n"
            "【YouTube 单视频】\n"
            "https://www.youtube.com/watch?v=xxxxxxxx\n"
            "https://youtu.be/xxxxxxxx\n"
            "https://www.youtube.com/watch?v=xxxxxxxx&list=RDxxxx&start_radio=1\n"
            "  （电台混播在自动模式下只下当前视频）\n\n"
            "【YouTube 播放列表】\n"
            "https://www.youtube.com/playlist?list=PLxxxxxxxx\n"
            "https://www.youtube.com/watch?v=xxxx&list=PLxxxxxxxx\n\n"
            "【Facebook】\n"
            "https://www.facebook.com/reel/xxxxxxxx\n"
            "https://www.facebook.com/watch/?v=xxxxxxxx\n"
            "https://fb.watch/xxxxxxxx/\n"
            "以及可公开访问的 Reels/视频清单链接\n\n"
            "直接点「开始下载」即可：播放列表会自动展开并逐个下载。\n"
            "「解析预览」可选，只用于提前查看列表内容。"
        )
        left.layout.addWidget(self.links_edit, 1)

        link_actions_top = QHBoxLayout()
        link_actions_top.setSpacing(8)
        link_actions_bottom = QHBoxLayout()
        link_actions_bottom.setSpacing(8)
        left.layout.addLayout(link_actions_top)
        left.layout.addLayout(link_actions_bottom)

        self.clipboard_btn = QPushButton("从剪贴板读取")
        self.clipboard_btn.setObjectName("secondaryButton")
        self.clear_links_btn = QPushButton("清空链接")
        self.clear_links_btn.setObjectName("ghostButton")
        self.preview_btn = QPushButton("解析预览")
        self.preview_btn.setObjectName("secondaryButton")
        self.start_btn = QPushButton("开始下载")
        self.start_btn.setObjectName("primaryButton")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.setEnabled(False)
        self.open_folder_btn = QPushButton("打开文件夹")
        self.open_folder_btn.setObjectName("secondaryButton")

        for btn in (self.clipboard_btn, self.clear_links_btn, self.preview_btn):
            link_actions_top.addWidget(btn)
        link_actions_top.addStretch()
        for btn in (self.start_btn, self.stop_btn, self.open_folder_btn):
            link_actions_bottom.addWidget(btn)
        link_actions_bottom.addStretch()

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "平台", "来源", "标题", "时长", "状态", "链接"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 220)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 150)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setAlternatingRowColors(True)
        right_preview.layout.addWidget(self.table)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        right_log.layout.addWidget(self.log_box)

        self.status_row = QLabel("等待开始 · 粘贴后可直接下载（自动加载列表）")
        self.status_row.setObjectName("status")
        root.addWidget(self.status_row)

        env_tip = "yt-dlp 已就绪" if yt_dlp is not None else "未安装 yt-dlp，请先 pip install yt-dlp"
        if yt_dlp is not None:
            ff = resolve_ffmpeg_path()
            if ff:
                env_tip += f" · ffmpeg 已就绪（高清合并）"
            else:
                env_tip += " · 未检测到 ffmpeg：1080p 可能失败或严重掉画质，请安装 ffmpeg"
        env_tip += " · 断点续传默认开启 · 开始下载时自动加载链接"
        self.log(env_tip)

    def _wrap_button(self, button: QPushButton) -> QFrame:
        field = QFrame()
        field.setObjectName("fieldBox")
        box = QVBoxLayout(field)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        caption = QLabel(" ")
        caption.setObjectName("fieldLabel")
        box.addWidget(caption)
        box.addWidget(button)
        return field

    def _add_field(self, grid, label, widget, row, col, row_span=1, col_span=1):
        field = QFrame()
        field.setObjectName("fieldBox")
        box = QVBoxLayout(field)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        box.addWidget(caption)
        box.addWidget(widget)
        grid.addWidget(field, row, col, row_span, col_span)
        return field

    def connect_signals(self):
        self.clipboard_btn.clicked.connect(self.load_clipboard)
        self.clear_links_btn.clicked.connect(self.links_edit.clear)
        self.preview_btn.clicked.connect(self.start_preview)
        self.start_btn.clicked.connect(self.start_download)
        self.stop_btn.clicked.connect(self.stop_task)
        self.open_folder_btn.clicked.connect(self.open_output_folder)

    def log(self, message: str):
        now = time.strftime("%H:%M:%S")
        self.log_box.append(f"[{now}] {message}")

    def choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择视频下载目录", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def open_output_folder(self):
        path = self.output_edit.text().strip()
        if not path:
            QMessageBox.information(self, APP_SECTION, "请先选择下载目录。")
            return
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def load_clipboard(self):
        text = QApplication.clipboard().text()
        if not text.strip():
            QMessageBox.information(self, APP_SECTION, "剪贴板为空。")
            return
        current = self.links_edit.toPlainText().strip()
        if current:
            self.links_edit.setPlainText(current + "\n" + text.strip())
        else:
            self.links_edit.setPlainText(text.strip())
        self.log("已从剪贴板读取内容。")

    def has_running_worker(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def set_running_state(self, running: bool):
        self.preview_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.clipboard_btn.setEnabled(not running)
        self.mode_combo.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def collect_urls(self) -> list[str]:
        return extract_urls(self.links_edit.toPlainText())

    def current_mode(self) -> str:
        return self.mode_combo.currentText()

    def _row_values(self, item: VideoItem) -> list:
        return [
            item.index,
            item.platform,
            item.source or "",
            item.title,
            item.duration,
            item.status,
            item.url,
        ]

    def fill_table(self, items: list[VideoItem]):
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            for col, value in enumerate(self._row_values(item)):
                cell = QTableWidgetItem("" if value is None else str(value))
                self._style_cell(cell, col, item)
                self.table.setItem(row, col, cell)

    def _style_cell(self, cell: QTableWidgetItem, col: int, item: VideoItem):
        if col == 1:
            if item.platform == "YouTube":
                cell.setForeground(QColor("#ef4444"))
            elif item.platform == "Facebook":
                cell.setForeground(QColor("#3b82f6"))
        if col == 5:
            status = str(item.status or "")
            if status.startswith("成功") or status == "待下载":
                cell.setForeground(QColor("#15803d"))
            elif "失败" in status or "解析失败" in status:
                cell.setForeground(QColor("#dc2626"))
            elif "跳过" in status or "续传" in status or "停止" in status:
                cell.setForeground(QColor("#ca8a04"))

    def upsert_table_item(self, item: VideoItem):
        for row in range(self.table.rowCount()):
            index_item = self.table.item(row, 0)
            if index_item and index_item.text() == str(item.index):
                for col, value in enumerate(self._row_values(item)):
                    cell = self.table.item(row, col)
                    if cell is None:
                        cell = QTableWidgetItem()
                        self.table.setItem(row, col, cell)
                    cell.setText("" if value is None else str(value))
                    self._style_cell(cell, col, item)
                return
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, value in enumerate(self._row_values(item)):
            cell = QTableWidgetItem("" if value is None else str(value))
            self._style_cell(cell, col, item)
            self.table.setItem(row, col, cell)

    def start_preview(self):
        if self.has_running_worker():
            QMessageBox.information(self, APP_SECTION, "当前任务还在运行，请结束后再预览。")
            return
        if yt_dlp is None:
            QMessageBox.warning(self, APP_SECTION, "未安装 yt-dlp。\n请先执行：pip install yt-dlp")
            return
        urls = self.collect_urls()
        if not urls:
            QMessageBox.information(self, APP_SECTION, "没有识别到有效链接。")
            return

        mode = self.current_mode()
        limit = self.playlist_limit_spin.value()
        self.preview_items = []
        self.table.setRowCount(0)
        self.set_running_state(True)
        self.status_row.setText(f"正在解析 {len(urls)} 个链接（{mode}）...")
        self.log(f"开始解析 {len(urls)} 个链接，模式：{mode}" + (f"，列表上限 {limit}" if limit else ""))

        worker = VideoPreviewWorker(urls, mode, limit)
        worker.log.connect(self.log)
        worker.failed.connect(self.show_error)
        worker.item_ready.connect(self.upsert_table_item)
        worker.done.connect(self.on_preview_done)
        worker.finished.connect(self.on_worker_finished)
        self.worker = worker
        worker.start()

    def on_preview_done(self, items: list[VideoItem]):
        self.preview_items = list(items)
        self.fill_table(self.preview_items)
        ok = sum(1 for i in items if i.status == "待下载")
        playlists = len({i.playlist_title for i in items if i.playlist_title})
        self.status_row.setText(f"预览完成：{ok}/{len(items)} 可下载" + (f"，含 {playlists} 个列表" if playlists else ""))
        self.log(f"预览完成：可下载 {ok}，共 {len(items)} 项" + (f"，播放列表 {playlists} 个" if playlists else "") + "。")

    def start_download(self):
        if self.has_running_worker():
            QMessageBox.information(self, APP_SECTION, "当前任务还在运行。")
            return
        if yt_dlp is None:
            QMessageBox.warning(self, APP_SECTION, "未安装 yt-dlp。\n请先执行：pip install yt-dlp")
            return

        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, APP_SECTION, "请先选择下载目录。")
            return

        urls = self.collect_urls()
        if not urls:
            QMessageBox.information(self, APP_SECTION, "请先粘贴视频或播放列表链接。")
            return

        mode = self.current_mode()
        limit = self.playlist_limit_spin.value()

        # 直接下载：自动加载列表，不依赖「解析预览」
        self.preview_items = []
        self.table.setRowCount(0)
        self.set_running_state(True)
        self.status_row.setText(f"正在自动加载并下载（{len(urls)} 个链接）...")
        self.log(f"开始：自动加载 {len(urls)} 个链接后下载，模式：{mode}")

        worker = VideoDownloadWorker(
            source_urls=urls,
            output_dir=output_dir,
            quality_key=self.quality_combo.currentText(),
            skip_existing=self.skip_existing_check.isChecked(),
            split_by_platform=self.split_platform_check.isChecked(),
            playlist_subfolder=self.playlist_folder_check.isChecked(),
            resume=self.resume_check.isChecked(),
            mode=mode,
            playlist_limit=limit,
        )
        worker.log.connect(self.log)
        worker.failed.connect(self.show_error)
        worker.item_update.connect(self.on_item_update)
        worker.progress.connect(self.on_progress)
        worker.done.connect(self.on_download_done)
        worker.finished.connect(self.on_worker_finished)
        self.worker = worker
        worker.start()

    def on_item_update(self, item: VideoItem):
        found = False
        for idx, old in enumerate(self.preview_items):
            if old.index == item.index:
                self.preview_items[idx] = item
                found = True
                break
        if not found:
            self.preview_items.append(item)
        self.upsert_table_item(item)

    def on_progress(self, stats: dict):
        self.status_row.setText(
            f"成功 {stats.get('success', 0)}，跳过 {stats.get('skipped', 0)}，"
            f"续传 {stats.get('resumed', 0)}，失败 {stats.get('failed', 0)}"
        )

    def on_download_done(self):
        self.status_row.setText("视频下载任务结束（未完成文件可断点续传）")

    def stop_task(self):
        if self.worker and hasattr(self.worker, "stop"):
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.status_row.setText("正在停止（已下载部分会保留以便续传）...")
            self.log("已请求停止。未完成文件会保留，再次开始可断点续传。")
        else:
            self.set_running_state(False)

    def on_worker_finished(self):
        if self.sender() is self.worker:
            self.worker = None
        self.set_running_state(False)

    def show_error(self, message: str):
        self.log(message)
        self.status_row.setText("出现错误")
        QMessageBox.warning(self, APP_SECTION, message)

    def request_close(self) -> bool:
        if not self.has_running_worker():
            return True
        if hasattr(self.worker, "stop"):
            self.stop_task()
        if not self.worker.wait(3000):
            return False
        return True
