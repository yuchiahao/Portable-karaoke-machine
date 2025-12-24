# -*- coding: utf-8 -*-
"""
KTV 伴唱機（可交付安全版）
✅ UI 不變
✅ 可打包成 KTV.exe（PyInstaller onefile）
✅ 不包含任何個資：不讀 Chrome/Edge cookies DB、不內建 cookies
✅ 可選 cookies.txt：
   - 若使用者自行將 cookies.txt 放在 KTV.exe 同一層 → 自動套用（更穩）
   - 沒有 cookies.txt → 仍可使用（但部分影片可能受 YouTube 限制無法播放）

【專案結構（打包前）】
KTV/
  main.py
  favorites.json            (可沒有，程式會自動建立)
  vlc/
    libvlc.dll
    libvlccore.dll
    plugins/...
  ffmpeg/
    ffmpeg.exe
    ffprobe.exe

【打包指令（PowerShell）】
pyinstaller main.py --noconsole --onefile --name KTV --clean --add-data "vlc;vlc" --add-data "ffmpeg;ffmpeg" --add-data "favorites.json;."
"""

import os
import sys
import json
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

import tkinter as tk
from tkinter import messagebox

# UI
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *  # noqa: F401,F403
except ModuleNotFoundError:
    raise SystemExit("缺少 ttkbootstrap，請先安裝：pip install ttkbootstrap")

# YouTube
try:
    import yt_dlp
except ModuleNotFoundError:
    yt_dlp = None

# 語音（選用）
try:
    import speech_recognition as sr
    HAS_SPEECH = True
except Exception:
    sr = None
    HAS_SPEECH = False

vlc = None  # 延後 import，避免 DLL 搜尋路徑問題

APP_TITLE = "KTV 伴唱機（可交付安全版）"
FAV_FILE = "favorites.json"


# -----------------------------
# 打包後找資源
# -----------------------------
def resource_path(relative_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, relative_path)


def ensure_runtime_env():
    # --- VLC ---
    vlc_dir = resource_path("vlc")
    plugins_dir = os.path.join(vlc_dir, "plugins")
    if os.path.isdir(vlc_dir):
        os.environ["VLC_PLUGIN_PATH"] = plugins_dir
        os.environ["PATH"] = vlc_dir + os.pathsep + os.environ.get("PATH", "")
        if sys.platform.startswith("win"):
            try:
                os.add_dll_directory(vlc_dir)
            except Exception:
                pass

    # --- ffmpeg ---
    ffmpeg_dir = resource_path("ffmpeg")
    if os.path.isdir(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


def app_dir() -> str:
    """程式執行資料夾：打包後是 exe 同層；開發時是 main.py 同層"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


def cookie_file_path() -> Optional[str]:
    """
    ✅ 可交付安全策略：只讀取「使用者自己放的 cookies.txt」
    - 放在 KTV.exe 同一層即可被自動載入
    - 不放也能用（只是成功率看 YouTube 限制）
    """
    p = os.path.join(app_dir(), "cookies.txt")
    return p if os.path.exists(p) else None


# -----------------------------
# 資料結構
# -----------------------------
@dataclass
class VideoItem:
    title: str
    video_id: str
    duration: str = ""
    channel: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class FavoriteStore:
    def __init__(self, path: str):
        self.path = path
        self.items: List[VideoItem] = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.items = [VideoItem(**d) for d in data]
            except Exception:
                self.items = []
        else:
            self.items = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump([asdict(x) for x in self.items], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Save favorites error:", e)

    def add(self, it: VideoItem):
        if not any(x.video_id == it.video_id for x in self.items):
            self.items.append(it)
            self.save()

    def remove_by_id(self, vid: str):
        self.items = [x for x in self.items if x.video_id != vid]
        self.save()


# -----------------------------
# 播放器（可交付安全版）
# -----------------------------
class Player:
    """
    ✅ 可交付安全策略：
    - 只吃 cookies.txt（若存在）→ 不存在就不使用 cookies
    - 不讀瀏覽器 Cookie DB（避免資料庫鎖定/權限問題）
    - 先直連 progressive（快）
    - 失敗再下載合併（穩）
    """

    def __init__(self, container: tk.Frame):
        global vlc

        if yt_dlp is None:
            messagebox.showerror("缺少套件", "未安裝 yt_dlp： pip install yt-dlp")
            raise SystemExit

        ensure_runtime_env()

        if vlc is None:
            try:
                import vlc as vlc_mod
                vlc = vlc_mod
            except Exception:
                try:
                    if sys.platform.startswith("win"):
                        candidates = [
                            r"C:\Program Files\VideoLAN\VLC",
                            r"C:\Program Files (x86)\VideoLAN\VLC",
                        ]
                        for p in candidates:
                            if os.path.isdir(p):
                                try:
                                    os.add_dll_directory(p)
                                except Exception:
                                    pass
                                break
                    import vlc as vlc_mod2
                    vlc = vlc_mod2
                except Exception:
                    messagebox.showerror(
                        "缺少 VLC",
                        "找不到 VLC Runtime。\n"
                        "請：\n"
                        "1) 安裝 VLC 或\n"
                        "2) 在專案放入 vlc/（含 libvlc.dll、plugins）後再打包。",
                    )
                    raise SystemExit

        try:
            self.instance = vlc.Instance(
                "--network-caching=2500",
                "--file-caching=2500",
                "--http-reconnect",
                "--no-video-title-show",
            )
            self.player = self.instance.media_player_new()
        except Exception as e:
            messagebox.showerror("VLC 初始化失敗", str(e))
            raise SystemExit

        self.container = container
        self._attach_video()
        self.on_end_callback = None
        self._temp_dirs: List[str] = []
        self._poll_end()

    def _attach_video(self):
        self.container.update_idletasks()
        handle = self.container.winfo_id()
        if sys.platform.startswith("win"):
            self.player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self.player.set_nsobject(handle)
        else:
            self.player.set_xwindow(handle)

    def set_on_end(self, callback):
        self.on_end_callback = callback

    def _poll_end(self):
        try:
            if self.player and self.player.get_state() == 6 and self.on_end_callback:
                self.on_end_callback()
        except Exception:
            pass
        self.container.after(1000, self._poll_end)

    @staticmethod
    def _looks_like_manifest(url: str) -> bool:
        u = (url or "").lower()
        return (".m3u8" in u) or (".mpd" in u) or ("manifest" in u) or ("dash" in u) or ("hls" in u)

    def _cookie_opts(self) -> Dict[str, Any]:
        """若 cookies.txt 存在就使用，不存在就空字典"""
        ck = cookie_file_path()
        return {"cookiefile": ck} if ck else {}

    def _extract_info(self, base_opts: Dict[str, Any], url: str, download: bool):
        opts = dict(base_opts)
        opts.update(self._cookie_opts())
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)

    def _get_direct_progressive_url(self, video_url: str) -> Optional[str]:
        base_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            info = self._extract_info(base_opts, video_url, download=False)
        except Exception:
            return None

        fmts = info.get("formats") or []
        candidates = []
        for f in fmts:
            url = f.get("url")
            if not url:
                continue

            protocol = (f.get("protocol") or "").lower()
            ext = (f.get("ext") or "").lower()
            acodec = (f.get("acodec") or "").lower()
            vcodec = (f.get("vcodec") or "").lower()

            # 只要含音含影的 progressive
            if acodec in ("none", "") or vcodec in ("none", ""):
                continue
            if self._looks_like_manifest(url):
                continue
            if protocol not in ("https", "http"):
                continue
            if ext not in ("mp4", "webm"):
                continue

            score = (f.get("height") or 0, f.get("tbr") or 0)
            candidates.append((ext, score, url))

        if not candidates:
            return None

        candidates.sort(reverse=True, key=lambda x: (x[0] == "mp4", x[1]))
        return candidates[0][2]

    def _download_best_with_retries(self, video_url: str) -> Optional[str]:
        ffmpeg_exe = resource_path(r"ffmpeg\ffmpeg.exe") if sys.platform.startswith("win") else resource_path("ffmpeg/ffmpeg")
        has_local_ffmpeg = os.path.exists(ffmpeg_exe)
        has_path_ffmpeg = shutil.which("ffmpeg") is not None
        if not (has_local_ffmpeg or has_path_ffmpeg):
            return None

        tmpdir = tempfile.mkdtemp(prefix="ktv_")
        outtmpl = os.path.join(tmpdir, "%(title).150s.%(ext)s")

        format_trials = [
            {"format": "bv*+ba/best", "merge_output_format": "mp4"},
            {"format": "best", "merge_output_format": "mp4"},
            {"format": "bestaudio/best"},
        ]

        cookie_opts = self._cookie_opts()

        for trial in format_trials:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "outtmpl": outtmpl,
                **trial,
                **cookie_opts,
            }
            if has_local_ffmpeg:
                ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg_exe)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    path = ydl.prepare_filename(info)

                base, _ = os.path.splitext(path)
                mp4 = base + ".mp4"
                if os.path.exists(mp4):
                    path = mp4

                if os.path.exists(path):
                    self._temp_dirs.append(tmpdir)
                    return path

                files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
                files = [f for f in files if os.path.isfile(f)]
                if files:
                    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
                    self._temp_dirs.append(tmpdir)
                    return files[0]

            except Exception:
                continue

        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        return None

    def play(self, video_url: str, status_cb=None):
        def set_status(msg: str):
            if callable(status_cb):
                try:
                    status_cb(msg)
                except Exception:
                    pass

        # 1) 直連（快）
        set_status("準備播放（嘗試直連）…")
        direct = self._get_direct_progressive_url(video_url)
        if direct:
            try:
                media = self.instance.media_new(direct)
                self.player.set_media(media)
                self.player.play()
                self.container.after(1200, lambda: self._fallback_if_error(video_url, status_cb))
                return
            except Exception:
                pass

        # 2) 下載/合併（穩）
        set_status("直連不可用，改用下載合併（較穩）…")
        local = self._download_best_with_retries(video_url)
        if not local:
            set_status("此影片目前無法播放，請換一首")
            messagebox.showwarning(
                "無法播放",
                "目前無法取得此影片可用串流。\n\n"
                "若你是交付給別人的版本：這通常是 YouTube 限制。\n"
                "可請使用者自行匯出 cookies.txt，並放在 KTV.exe 同一個資料夾以提高成功率（可選）。"
            )
            return

        try:
            media = self.instance.media_new_path(local)
            self.player.stop()
            self.player.set_media(media)
            self.player.play()
            set_status("播放中（本地檔）")
        except Exception as e:
            set_status("播放失敗")
            messagebox.showerror("播放失敗", str(e))

    def _fallback_if_error(self, video_url: str, status_cb=None):
        def set_status(msg: str):
            if callable(status_cb):
                try:
                    status_cb(msg)
                except Exception:
                    pass

        try:
            st = self.player.get_state()
            if st in (5, 7):  # stopped / error
                set_status("直連播放失敗，改用下載合併…")
                local = self._download_best_with_retries(video_url)
                if not local:
                    set_status("此影片目前無法播放，請換一首")
                    return
                media = self.instance.media_new_path(local)
                self.player.stop()
                self.player.set_media(media)
                self.player.play()
                set_status("播放中（本地檔）")
        except Exception:
            pass

    def pause(self):
        try:
            if self.player.is_playing():
                self.player.pause()
            else:
                self.player.play()
        except Exception:
            pass

    def stop(self):
        try:
            self.player.stop()
        except Exception:
            pass

    def cleanup_temp(self):
        for d in self._temp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._temp_dirs = []


# -----------------------------
# 主程式 UI（介面不變）
# -----------------------------
class KTVApp:
    def __init__(self, theme="flatly"):
        ensure_runtime_env()

        self.root = tb.Window(themename=theme)
        self.root.title(APP_TITLE)
        self.root.geometry("1600x1000")
        self.root.minsize(1400, 900)

        if not os.path.exists(FAV_FILE):
            try:
                with open(FAV_FILE, "w", encoding="utf-8") as f:
                    f.write("[]")
            except Exception:
                pass

        self.store = FavoriteStore(FAV_FILE)
        self.queue_items: List[VideoItem] = []
        self.current: Optional[VideoItem] = None

        self.search_q = queue.Queue()
        self.search_thread = threading.Thread(target=self._search_worker, daemon=True)
        self.search_thread.start()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            if hasattr(self, "player") and self.player:
                self.player.stop()
                self.player.cleanup_temp()
        except Exception:
            pass
        self.root.destroy()

    def _build_ui(self):
        top = tb.Frame(self.root, padding=8)
        top.pack(fill=X)

        self.keyword = tb.StringVar()
        self.status_text = tb.StringVar(value="就緒")

        tb.Label(top, text="關鍵字：").pack(side=LEFT)
        tb.Entry(top, textvariable=self.keyword, width=55).pack(side=LEFT, padx=(0, 8))

        self.karaoke_mode = tb.BooleanVar(value=False)
        tb.Checkbutton(
            top,
            text="伴奏/去人聲模式（偏找KTV/伴奏）",
            variable=self.karaoke_mode,
            bootstyle="round-toggle",
        ).pack(side=LEFT, padx=(0, 8))

        tb.Button(top, text="搜尋 (Enter)", bootstyle=PRIMARY, command=self.on_search).pack(side=LEFT)
        tb.Button(top, text="切歌 (下一首)", bootstyle=WARNING, command=self.play_next).pack(side=LEFT, padx=6)
        tb.Button(top, text="播放/暫停", command=self.toggle_pause).pack(side=LEFT, padx=6)

        self.btn_voice = tb.Button(top, text="語音辨識", command=self.voice_search)
        self.btn_voice.pack(side=LEFT, padx=6)

        tb.Label(top, textvariable=self.status_text).pack(side=LEFT, padx=10)

        upper = tb.Frame(self.root)
        upper.pack(fill=BOTH, expand=YES, padx=8, pady=(6, 8))

        self.video_frame = tb.Frame(upper, bootstyle="dark", height=480)
        self.video_frame.pack(fill=BOTH, expand=YES)

        self.player = Player(self.video_frame)
        self.player.set_on_end(self._auto_play_next)

        info = tb.Frame(upper)
        info.pack(fill=X, pady=(6, 0))
        tb.Label(info, text="正在播放：").pack(side=LEFT)
        self.now_label = tb.Label(info, text="--")
        self.now_label.pack(side=LEFT)

        self.nb = tb.Notebook(self.root)
        self.nb.pack(fill=BOTH, expand=YES, padx=8, pady=(0, 8))

        # --- 搜尋結果 ---
        self.page_results = tb.Frame(self.nb)
        self.nb.add(self.page_results, text="搜尋結果")
        self.results = tb.Treeview(self.page_results, columns=("title", "duration", "channel", "vid"), show="headings")
        for c, t, w in [
            ("title", "標題", 640),
            ("duration", "時長", 90),
            ("channel", "頻道", 220),
            ("vid", "Video ID", 170),
        ]:
            self.results.heading(c, text=t)
            self.results.column(c, width=w, anchor=tk.W)
        self.results.pack(fill=BOTH, expand=YES, side=tk.LEFT)

        bar_r = tb.Frame(self.page_results)
        bar_r.pack(side=tk.LEFT, fill=Y, padx=(6, 0))
        tb.Button(bar_r, text="加入歌單", bootstyle=SUCCESS, command=self.add_selected_to_queue).pack(fill=X)
        tb.Button(bar_r, text="立即播放", command=self.play_selected_now).pack(fill=X, pady=6)
        tb.Button(bar_r, text="收藏 (最愛)", command=self.add_selected_to_fav).pack(fill=X)
        self.results.bind("<Double-1>", lambda e: self.play_selected_now())

        # --- 播放清單 ---
        self.page_queue = tb.Frame(self.nb)
        self.nb.add(self.page_queue, text="播放清單")
        self.queue_view = tb.Treeview(self.page_queue, columns=("title", "duration", "channel", "vid"), show="headings")
        for c, t, w in [
            ("title", "標題", 640),
            ("duration", "時長", 90),
            ("channel", "頻道", 220),
            ("vid", "Video ID", 170),
        ]:
            self.queue_view.heading(c, text=t)
            self.queue_view.column(c, width=w, anchor=tk.W)
        self.queue_view.pack(fill=BOTH, expand=YES, side=tk.LEFT)

        bar_q = tb.Frame(self.page_queue)
        bar_q.pack(side=tk.LEFT, fill=Y, padx=(6, 0))
        tb.Button(bar_q, text="播放選取", command=self.play_selected_in_queue).pack(fill=X)
        tb.Button(bar_q, text="從歌單移除", bootstyle=DANGER, command=self.remove_selected_in_queue).pack(fill=X, pady=6)
        tb.Button(bar_q, text="清空歌單", bootstyle=SECONDARY, command=self.clear_queue).pack(fill=X)

        # --- 我的最愛 ---
        self.page_fav = tb.Frame(self.nb)
        self.nb.add(self.page_fav, text="我的最愛")
        self.fav_view = tb.Treeview(self.page_fav, columns=("title", "duration", "channel", "vid"), show="headings")
        for c, t, w in [
            ("title", "標題", 640),
            ("duration", "時長", 90),
            ("channel", "頻道", 220),
            ("vid", "Video ID", 170),
        ]:
            self.fav_view.heading(c, text=t)
            self.fav_view.column(c, width=w, anchor=tk.W)
        self.fav_view.pack(fill=BOTH, expand=YES, side=tk.LEFT)

        bar_f = tb.Frame(self.page_fav)
        bar_f.pack(side=tk.LEFT, fill=Y, padx=(6, 0))
        tb.Button(bar_f, text="加入歌單", command=self.add_fav_to_queue).pack(fill=X)
        tb.Button(bar_f, text="立即播放", command=self.play_fav_now).pack(fill=X, pady=6)
        tb.Button(bar_f, text="移除最愛", bootstyle=DANGER, command=self.remove_fav).pack(fill=X)
        self.fav_view.bind("<Double-1>", lambda e: self.play_fav_now())

        self._refresh_fav()
        self.root.bind("<Return>", lambda e: self.on_search())

    # ---------- 搜尋 ----------
    def on_search(self):
        q = self.keyword.get().strip()
        if not q:
            return
        self._set_status(f"搜尋中：{q} …")
        self.search_q.put((q, bool(self.karaoke_mode.get())))

    def _search_worker(self):
        while True:
            query, karaoke = self.search_q.get()
            try:
                items = self._search_via_ytdlp(query, karaoke)
                self.root.after(0, self._fill_results, items, query, karaoke)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("搜尋失敗", str(e)))

    def _search_via_ytdlp(self, query: str, karaoke_mode: bool) -> List[VideoItem]:
        if yt_dlp is None:
            raise RuntimeError("未安裝 yt_dlp： pip install yt-dlp")

        base_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }

        if karaoke_mode:
            boosters = " 伴奏 去人聲 無人聲 KTV 卡拉OK Karaoke instrumental backing track"
            search = f'ytsearch25:"{query}"{boosters}'
        else:
            search = f'ytsearch25:"{query}"'

        # ✅ 搜尋也只吃 cookies.txt（若存在）；不存在就無 cookies
        ck = cookie_file_path()
        opts = dict(base_opts)
        if ck:
            opts["cookiefile"] = ck

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search, download=False)

        items: List[VideoItem] = []
        for e in (info.get("entries") or []):
            vid = e.get("id")
            title = e.get("title") or "(無標題)"
            dur = e.get("duration")
            if isinstance(dur, int):
                m, s = divmod(dur, 60)
                dur_text = f"{m:02d}:{s:02d}"
            else:
                dur_text = str(dur or "")
            ch = e.get("channel") or e.get("uploader") or ""
            if vid:
                items.append(VideoItem(title=title, video_id=vid, duration=dur_text, channel=ch))
        return items

    def _fill_results(self, items: List[VideoItem], q: str, karaoke: bool):
        for row in self.results.get_children():
            self.results.delete(row)
        for it in items:
            self.results.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

        mode = "伴奏/去人聲模式" if karaoke else "一般搜尋"
        self._set_status(f"搜尋完成（{mode}）：{q}（共 {len(items)} 首）")

    # ---------- 播放 ----------
    def _play(self, item: VideoItem):
        self.current = item
        self.now_label.configure(text=f"{item.title} ({item.channel})")
        self.player.play(item.url, status_cb=self._set_status)

    def play_selected_now(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self._play(it)

    def play_selected_in_queue(self):
        it = self._get_selected(self.queue_view)
        if not it:
            return
        self._play(it)

    def play_fav_now(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self._play(it)

    def play_next(self):
        if not self.queue_items:
            self._set_status("歌單為空")
            return
        nxt = self.queue_items.pop(0)
        self._refresh_queue()
        self._play(nxt)

    def _auto_play_next(self):
        if self.queue_items:
            nxt = self.queue_items.pop(0)
            self._refresh_queue()
            self._play(nxt)
        else:
            self.now_label.configure(text="--")
            self._set_status("播放結束")

    def toggle_pause(self):
        self.player.pause()

    # ---------- 歌單 ----------
    def add_selected_to_queue(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self.queue_items.append(it)
        self._refresh_queue()
        self._set_status("已加入播放清單")

    def remove_selected_in_queue(self):
        sel = self.queue_view.selection()
        if not sel:
            return
        vals = self.queue_view.item(sel[0], "values")
        vid = vals[3]
        self.queue_items = [x for x in self.queue_items if x.video_id != vid]
        self._refresh_queue()
        self._set_status("已從播放清單移除")

    def clear_queue(self):
        self.queue_items = []
        self._refresh_queue()
        self._set_status("已清空播放清單")

    def _refresh_queue(self):
        for row in self.queue_view.get_children():
            self.queue_view.delete(row)
        for it in self.queue_items:
            self.queue_view.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

    # ---------- 最愛 ----------
    def add_selected_to_fav(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self.store.add(it)
        self._refresh_fav()
        self._set_status("已加入最愛")

    def remove_fav(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self.store.remove_by_id(it.video_id)
        self._refresh_fav()
        self._set_status("已移除最愛")

    def add_fav_to_queue(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self.queue_items.append(it)
        self._refresh_queue()
        self._set_status("最愛已加入播放清單")

    def _refresh_fav(self):
        for row in self.fav_view.get_children():
            self.fav_view.delete(row)
        for it in self.store.items:
            self.fav_view.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

    # ---------- 語音搜尋 ----------
    def voice_search(self):
        if not HAS_SPEECH:
            messagebox.showwarning(
                "缺少語音辨識套件",
                "尚未安裝語音辨識套件，請先安裝：\n\n"
                "pip install SpeechRecognition pyaudio\n\n"
                "若 Windows 安裝 pyaudio 失敗：\n"
                "pip install pipwin\n"
                "pipwin install pyaudio",
            )
            return

        try:
            self.btn_voice.configure(state=DISABLED)
        except Exception:
            pass

        self._set_status("語音辨識中…請開始說話")
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_worker(self):
        recog = sr.Recognizer()
        recog.dynamic_energy_threshold = True

        try:
            with sr.Microphone() as source:
                recog.adjust_for_ambient_noise(source, duration=0.6)
                audio = recog.listen(source, timeout=6, phrase_time_limit=7)
        except Exception as e:
            self.root.after(0, lambda: self._voice_done_error(f"麥克風讀取失敗：{e}"))
            return

        try:
            text = recog.recognize_google(audio, language="zh-TW")
        except sr.UnknownValueError:
            self.root.after(0, lambda: self._voice_done_error("我沒有聽清楚，再試一次好嗎？"))
            return
        except sr.RequestError as e:
            self.root.after(0, lambda: self._voice_done_error(f"語音服務連線失敗：{e}"))
            return
        except Exception as e:
            self.root.after(0, lambda: self._voice_done_error(f"語音辨識失敗：{e}"))
            return

        self.root.after(0, lambda: self._voice_done_ok(text))

    def _voice_done_ok(self, text: str):
        try:
            self.btn_voice.configure(state=NORMAL)
        except Exception:
            pass

        text = (text or "").strip()
        if not text:
            self._set_status("語音辨識結果為空")
            return

        self.keyword.set(text)
        self._set_status(f"語音辨識：{text}（自動搜尋）")
        self.on_search()

    def _voice_done_error(self, msg: str):
        try:
            self.btn_voice.configure(state=NORMAL)
        except Exception:
            pass
        self._set_status(msg)
        messagebox.showwarning("語音辨識", msg)

    # ---------- 共用 ----------
    def _get_selected(self, tv: tb.Treeview) -> Optional[VideoItem]:
        sel = tv.selection()
        if not sel:
            return None
        vals = tv.item(sel[0], "values")
        if len(vals) < 4:
            return None
        return VideoItem(title=vals[0], duration=vals[1], channel=vals[2], video_id=vals[3])

    def _set_status(self, text: str):
        try:
            self.status_text.set(text)
        except Exception:
            pass
        try:
            print(text)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = KTVApp(theme="flatly")
    app.run()
