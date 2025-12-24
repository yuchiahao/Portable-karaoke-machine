# -*- coding: utf-8 -*-
"""
KTV 伴唱機（搜尋 / 播放 / 歌單 / 最愛）
====================================
重點：
1) 搜尋提供兩種模式：
   - 一般搜尋（歌名最準）：只用你輸入的歌名
   - 伴奏/去人聲模式：在歌名後加「伴奏/去人聲/KTV…」加權詞，偏找伴唱版本
2) 播放更穩：
   - 先嘗試拿「音畫同一條（progressive）」直連 URL 給 VLC 播（快）
   - 直連拿不到就改為「下載 + ffmpeg 合併 mp4」再播（穩）
3) Windows：請先安裝 VLC 播放器，並 pip install python-vlc、yt-dlp、ttkbootstrap
4) 若要啟用「下載合併備援」，需安裝 ffmpeg 並加入 PATH（ffmpeg -version 要有回應）

安裝：
  pip install ttkbootstrap yt-dlp python-vlc
  # 語音點歌（選用）
  pip install SpeechRecognition pyaudio
"""

import sys
import os
import json
import threading
import queue
import shutil
import tempfile
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple

# --- tkinter / ttkbootstrap ---
import tkinter as tk
from tkinter import messagebox

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *  # noqa
except ModuleNotFoundError:
    sys.stderr.write("缺少 ttkbootstrap： pip install ttkbootstrap\n")
    raise

# --- 影音/搜尋 ---
try:
    import yt_dlp
except ModuleNotFoundError:
    yt_dlp = None

# --- 語音辨識（選用） ---
try:
    import speech_recognition as sr
    HAS_SPEECH = True
except Exception:
    sr = None
    HAS_SPEECH = False

vlc = None  # 延後 import，避免 DLL 路徑問題

APP_TITLE = "KTV 伴唱機（一般/伴奏搜尋切換版）"
FAV_FILE = "favorites.json"


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
                data = json.load(open(self.path, "r", encoding="utf-8"))
                self.items = [VideoItem(**d) for d in data]
            except Exception:
                self.items = []
        else:
            self.items = []

    def save(self):
        try:
            json.dump(
                [asdict(x) for x in self.items],
                open(self.path, "w", encoding="utf-8"),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            print("Save favorites error:", e)

    def add(self, it: VideoItem):
        if not any(x.video_id == it.video_id for x in self.items):
            self.items.append(it)
            self.save()

    def remove_by_id(self, vid: str):
        self.items = [x for x in self.items if x.video_id != vid]
        self.save()


class Player:
    """
    VLC 播放器（更穩定版）：
    - 只把「真正 progressive 的 https/mp4（音畫同一條）」URL 給 VLC
    - 若是 mpd/m3u8/manifest 或 VLC 播不動，就自動改成下載 + ffmpeg 合併 mp4 再播
    """

    def __init__(self, container: tk.Frame):
        global vlc
        if yt_dlp is None:
            messagebox.showerror("缺少套件", "未安裝 yt_dlp： pip install yt-dlp")
            raise SystemExit

        if vlc is None:
            try:
                # Windows：同時嘗試 64/32-bit VLC 安裝路徑
                if sys.platform.startswith("win"):
                    candidates = [
                        r"C:\Program Files\VideoLAN\VLC",
                        r"C:\Program Files (x86)\VideoLAN\VLC",
                    ]
                    for p in candidates:
                        if os.path.isdir(p):
                            os.add_dll_directory(p)
                            break

                import vlc as vlc_mod
                vlc = vlc_mod
            except Exception:
                messagebox.showerror(
                    "缺少 VLC",
                    "請先安裝 VLC 播放器（建議 64-bit）\n並安裝 python-vlc：pip install python-vlc",
                )
                raise SystemExit

        try:
            # 讓網路串流更耐用（避免自適應串流造成 demux 問題）
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
            # 6 = Ended
            if self.player and self.player.get_state() == 6 and self.on_end_callback:
                self.on_end_callback()
        except Exception:
            pass
        self.container.after(1000, self._poll_end)

    # ---------- 取得可播放來源 ----------
    @staticmethod
    def _looks_like_manifest(url: str) -> bool:
        u = (url or "").lower()
        return (".m3u8" in u) or (".mpd" in u) or ("manifest" in u) or ("dash" in u) or ("hls" in u)

    def _get_direct_progressive_url(self, video_url: str) -> Optional[str]:
        """
        只挑「真正 progressive」給 VLC：
        - protocol: http/https
        - ext: mp4
        - acodec/vcodec 都不是 none
        - 避開 m3u8/mpd/manifest
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

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

            # 必須音畫同一條
            if acodec in ("none", "") or vcodec in ("none", ""):
                continue

            # 必須 http(s) 且 mp4（VLC 成功率最高）
            if protocol not in ("https", "http"):
                continue
            if ext != "mp4":
                continue

            # 避開 manifest
            if self._looks_like_manifest(url):
                continue

            # 依解析度/碼率大致排序（height/tbr）
            score = (f.get("height") or 0, f.get("tbr") or 0)
            candidates.append((score, url))

        if candidates:
            candidates.sort(reverse=True, key=lambda x: x[0])
            return candidates[0][1]

        # 如果找不到乾淨 progressive，就不要硬塞給 VLC（改走下載合併）
        fallback = info.get("url")
        if fallback and not self._looks_like_manifest(fallback):
            return fallback
        return None

    def _download_and_merge_mp4(self, video_url: str) -> str:
        """
        下載並合併成 mp4（需要 ffmpeg 在 PATH）
        """
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                "找不到 ffmpeg。\n"
                "請先安裝 ffmpeg 並加入 PATH（在終端機能執行 ffmpeg -version）。"
            )

        tmpdir = tempfile.mkdtemp(prefix="ktv_")
        outtmpl = os.path.join(tmpdir, "%(title).150s.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            path = ydl.prepare_filename(info)

        base, _ = os.path.splitext(path)
        mp4 = base + ".mp4"
        if os.path.exists(mp4):
            path = mp4

        self._temp_dirs.append(tmpdir)
        return path

    def _get_play_source(self, video_url: str) -> Tuple[str, str]:
        """
        回傳 (kind, src)
        kind: 'url' 或 'file'
        """
        direct = self._get_direct_progressive_url(video_url)
        if direct:
            return ("url", direct)

        local = self._download_and_merge_mp4(video_url)
        return ("file", local)

    # ---------- 播放控制 ----------
    def play(self, video_url: str):
        """
        先試直連 progressive；若 VLC 很快進入 Error/Stopped 就自動改用下載檔
        """
        try:
            kind, src = self._get_play_source(video_url)

            if kind == "url":
                media = self.instance.media_new(src)
            else:
                media = self.instance.media_new_path(src)

            self.player.set_media(media)
            self.player.play()

            # 給 VLC 一點時間起播；若很快失敗就改走下載
            self.container.after(1200, lambda: self._fallback_if_error(video_url, kind))

        except Exception as e:
            messagebox.showerror("播放失敗", str(e))

    def _fallback_if_error(self, video_url: str, kind_used: str):
        try:
            st = self.player.get_state()
            # 7 = Error, 5 = Stopped
            if kind_used == "url" and st in (5, 7):
                local = self._download_and_merge_mp4(video_url)
                media = self.instance.media_new_path(local)
                self.player.stop()
                self.player.set_media(media)
                self.player.play()
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


class KTVApp:
    def __init__(self, theme="flatly"):
        self.root = tb.Window(themename=theme)
        self.root.title(APP_TITLE)
        self.root.geometry("1600x1000")
        self.root.minsize(1400, 900)

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

    # ---------- UI ----------
    def _build_ui(self):
        top = tb.Frame(self.root, padding=8)
        top.pack(fill=X)

        self.keyword = tb.StringVar()

        tb.Label(top, text="關鍵字：").pack(side=LEFT)
        tb.Entry(top, textvariable=self.keyword, width=55).pack(side=LEFT, padx=(0, 8))

        self.karaoke_mode = tb.BooleanVar(value=False)  # 預設「一般搜尋」
        tb.Checkbutton(
            top,
            text="伴奏/去人聲模式（偏找KTV/伴奏）",
            variable=self.karaoke_mode,
            bootstyle="round-toggle",
        ).pack(side=LEFT, padx=(0, 8))

        tb.Button(top, text="搜尋 (Enter)", bootstyle=PRIMARY, command=self.on_search).pack(side=LEFT)
        tb.Button(top, text="切歌 (下一首)", bootstyle=WARNING, command=self.play_next).pack(side=LEFT, padx=6)
        tb.Button(top, text="播放/暫停", command=self.toggle_pause).pack(side=LEFT)
        # ✅ 介面不變：只在「播放/暫停」右邊加一顆語音辨識按鈕
        self.btn_voice = tb.Button(top, text="語音辨識", command=self.voice_search)
        self.btn_voice.pack(side=LEFT, padx=6)

        # 上半部：播放器
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

        # 下半部：Notebook
        self.nb = tb.Notebook(self.root)
        self.nb.pack(fill=BOTH, expand=YES, padx=8, pady=(0, 8))

        # 搜尋結果
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

        # 播放清單
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

        # 我的最愛
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

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }

        # 這裡是關鍵：一般搜尋只用歌名（最準）
        # 伴奏模式才加加權詞（偏找伴奏/去人聲/KTV）
        if karaoke_mode:
            boosters = " 伴奏 去人聲 無人聲 KTV 卡拉OK Karaoke instrumental backing track"
            search = f'ytsearch25:"{query}"{boosters}'
        else:
            search = f'ytsearch25:"{query}"'

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

    # ---------- 佇列 / 播放 ----------
    def add_selected_to_queue(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self.queue_items.append(it)
        self._refresh_queue()

    def play_selected_now(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self._play(it)

    def add_fav_to_queue(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self.queue_items.append(it)
        self._refresh_queue()

    def play_fav_now(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self._play(it)

    def play_selected_in_queue(self):
        it = self._get_selected(self.queue_view)
        if not it:
            return
        self._play(it)

    def remove_selected_in_queue(self):
        sel = self.queue_view.selection()
        if not sel:
            return
        vals = self.queue_view.item(sel[0], "values")
        vid = vals[3]
        self.queue_items = [x for x in self.queue_items if x.video_id != vid]
        self._refresh_queue()

    def clear_queue(self):
        self.queue_items = []
        self._refresh_queue()

    def play_next(self):
        if not self.queue_items:
            self._set_status("歌單為空")
            return
        nxt = self.queue_items.pop(0)
        self._refresh_queue()
        self._play(nxt)

    def toggle_pause(self):
        self.player.pause()

    # ✅ 新增：語音辨識（把語音轉文字 -> 塞入搜尋框 -> 自動搜尋）
    def voice_search(self):
        """介面不變：在『播放/暫停』右邊新增一顆語音辨識按鈕。
        功能：把語音轉文字 -> 塞到上方關鍵字輸入框 -> 自動觸發搜尋
        """
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

        # 避免連點
        try:
            self.btn_voice.configure(state=DISABLED)
        except Exception:
            pass

        self._set_status("語音辨識中…請開始說話（例：周杰倫 稻香）")
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_worker(self):
        recog = sr.Recognizer()
        recog.dynamic_energy_threshold = True

        try:
            with sr.Microphone() as source:
                # 降低環境噪音影響
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

        # 塞進搜尋框並自動搜尋
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

    def _play(self, item: VideoItem):
        self.current = item
        self.now_label.configure(text=f"{item.title} ({item.channel})")
        self.player.play(item.url)

    def _auto_play_next(self):
        if self.queue_items:
            nxt = self.queue_items.pop(0)
            self._refresh_queue()
            self._play(nxt)
        else:
            self.now_label.configure(text="--")

    # ---------- 最愛 ----------
    def add_selected_to_fav(self):
        it = self._get_selected(self.results)
        if not it:
            return
        self.store.add(it)
        self._refresh_fav()

    def remove_fav(self):
        it = self._get_selected(self.fav_view)
        if not it:
            return
        self.store.remove_by_id(it.video_id)
        self._refresh_fav()

    # ---------- Utils ----------
    def _get_selected(self, tv: tb.Treeview) -> Optional[VideoItem]:
        sel = tv.selection()
        if not sel:
            return None
        vals = tv.item(sel[0], "values")
        if len(vals) < 4:
            return None
        return VideoItem(title=vals[0], duration=vals[1], channel=vals[2], video_id=vals[3])

    def _refresh_queue(self):
        for row in self.queue_view.get_children():
            self.queue_view.delete(row)
        for it in self.queue_items:
            self.queue_view.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

    def _refresh_fav(self):
        for row in self.fav_view.get_children():
            self.fav_view.delete(row)
        for it in self.store.items:
            self.fav_view.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

    def _set_status(self, text: str):
        # 你也可以做成狀態列 Label；這裡先印到 console
        try:
            print(text)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = KTVApp(theme="flatly")
    app.run()
