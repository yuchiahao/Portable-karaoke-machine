# -*- coding: utf-8 -*-
"""
KTV 伴唱機（Tkinter 視窗播放 + 搜尋/點歌/收藏/切歌/佇列）
=================================================
功能
- 內嵌視窗播放（非瀏覽器），使用 VLC 引擎（python-vlc）
- 以 yt_dlp 搜尋 YouTube（不用額外 API 金鑰）
- 搜尋結果顯示在播放視窗下方（同一視窗，下半部）
- 點歌（加入佇列）、立刻播放、切歌（下一首）
- 加入/移除最愛，最愛資料儲存 JSON
- 顯示目前「正在播放」

安裝相依套件（Windows 範例）：
    pip install ttkbootstrap yt_dlp python-vlc requests
    # 另外請安裝 VLC 播放器（含 libvlc）：https://www.videolan.org/

執行：
    python ktv_player.py   # 或你的檔名

注意：
- tkinter 為 Python 內建；若 Linux 缺少： sudo apt-get install python3-tk
- 若 VLC 未安裝或 libvlc 找不到，會提示錯誤並結束。
"""

import sys
import os
import json
import threading
import queue
from dataclasses import dataclass, asdict
from typing import List, Optional

# --- tkinter / ttkbootstrap ---
try:
    import tkinter as tk
    from tkinter import messagebox
except ModuleNotFoundError:
    sys.stderr.write("找不到 tkinter，請先安裝對應系統套件（Windows/macOS 通常內建）。\n")
    sys.exit(1)

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *  # noqa
except ModuleNotFoundError:
    sys.stderr.write("缺少 ttkbootstrap： pip install ttkbootstrap\n")
    sys.exit(1)

# --- 影音/搜尋 ---
try:
    import yt_dlp
except ModuleNotFoundError:
    yt_dlp = None

vlc = None  # 先設 None，避免 import 時找錯 DLL 路徑

APP_TITLE = "KTV 伴唱機"
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
            json.dump([asdict(x) for x in self.items], open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
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
    """用 VLC 內嵌至 Tk Frame 播放 YouTube 串流（透過 yt_dlp 抽出串流 URL）。"""
    def __init__(self, container: tk.Frame):
        global vlc
        if yt_dlp is None:
            messagebox.showerror("缺少套件", "未安裝 yt_dlp： pip install yt_dlp")
            raise SystemExit
        if vlc is None:
            # 設定 VLC DLL 路徑
            try:
                os.add_dll_directory(r"C:\Program Files\VideoLAN\VLC")
                import vlc as vlc_mod
                vlc = vlc_mod
            except Exception:
                messagebox.showerror("缺少套件", "未安裝 python-vlc，並請先安裝 VLC： pip install python-vlc")
                raise SystemExit
        try:
            self.instance = vlc.Instance()
            self.player = self.instance.media_player_new()
        except Exception as e:
            messagebox.showerror("VLC 初始化失敗", str(e))
            raise SystemExit
        self.container = container
        self._attach_video()

        self.on_end_callback = None  # 新增：播放結束回呼
        self._check_end()            # 新增：啟動定時檢查

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

    def _check_end(self):
        # 每 1 秒檢查是否播放結束
        if self.player:
            state = self.player.get_state()
            # 6 = Ended, 0 = NothingSpecial
            if state == 6 and self.on_end_callback:
                self.on_end_callback()
        self.container.after(1000, self._check_end)

    def _get_stream_url(self, video_url: str) -> Optional[str]:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestvideo+bestaudio/best",  # 改成這樣
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            url = info.get("url")
            if url:
                return url
            fmts = info.get("formats") or []
            # 優先找有 acodec 的格式
            for f in reversed(fmts):
                if f.get("acodec") != "none":
                    return f["url"]
            return fmts[-1]["url"] if fmts else None

    def play(self, video_url: str):
        try:
            stream = self._get_stream_url(video_url)
            if not stream:
                raise RuntimeError("無法取得串流 URL")
            media = self.instance.media_new(stream)
            self.player.set_media(media)
            self.player.play()
        except Exception as e:
            messagebox.showerror("播放失敗", f"{e}")

    def pause(self):
        if self.player.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        try:
            self.player.stop()
        except Exception:
            pass

class KTVApp:
    def __init__(self, theme="flatly"):
        self.root = tb.Window(themename=theme)
        self.root.title(APP_TITLE)
        self.root.geometry("1600x1000")      # ← 這裡改成你要的大小
        self.root.minsize(1600, 1000)        # ← 最小也設一樣

        self.store = FavoriteStore(FAV_FILE)
        self.queue_items: List[VideoItem] = []
        self.current: Optional[VideoItem] = None

        self.search_q = queue.Queue()
        self.search_thread = threading.Thread(target=self._search_worker, daemon=True)
        self.search_thread.start()

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        top = tb.Frame(self.root, padding=8)
        top.pack(fill=X)

        self.keyword = tb.StringVar()
        tb.Entry(top, textvariable=self.keyword, width=60).pack(side=LEFT, padx=(0,6))
        tb.Button(top, text="搜尋 (Enter)", bootstyle=PRIMARY, command=self.on_search).pack(side=LEFT)     
        tb.Button(top, text="切歌 (下一首)", bootstyle=WARNING, command=self.play_next).pack(side=LEFT)
        tb.Button(top, text="播放/暫停", command=self.toggle_pause).pack(side=LEFT, padx=6)
        tb.Button(top, text="語音點歌", command=self.voice_search).pack(side=LEFT, padx=6)  # 新增語音點歌按鈕

        # 上半部：播放器 + 正在播放
        upper = tb.Frame(self.root)
        upper.pack(fill=BOTH, expand=YES, padx=8, pady=(6,8))

        self.video_frame = tb.Frame(upper, bootstyle="dark", height=480)  # 設定高度 480px
        self.video_frame.pack(fill=BOTH, expand=YES)
        self.player = Player(self.video_frame)
        self.player.set_on_end(self._auto_play_next)  # 新增：自動播下一首

        info = tb.Frame(upper)
        info.pack(fill=X, pady=(6,0))
        tb.Label(info, text="正在播放：").pack(side=LEFT)
        self.now_label = tb.Label(info, text="--")
        self.now_label.pack(side=LEFT)

        # 下半部：Notebook（搜尋結果 / 佇列 / 最愛）
        self.nb = tb.Notebook(self.root)
        self.nb.pack(fill=BOTH, expand=YES, padx=8, pady=(0,8))

        # 搜尋結果
        self.page_results = tb.Frame(self.nb)
        self.nb.add(self.page_results, text="搜尋結果")
        self.results = tb.Treeview(self.page_results, columns=("title","duration","channel","vid"), show="headings")
        for c,t,w in [("title","標題",600),("duration","時長",90),("channel","頻道",180),("vid","Video ID",160)]:
            self.results.heading(c, text=t)
            self.results.column(c, width=w, anchor=tk.W)
        self.results.pack(fill=BOTH, expand=YES, side=tk.LEFT)
        bar_r = tb.Frame(self.page_results)
        bar_r.pack(side=tk.LEFT, fill=Y, padx=(6,0))
        tb.Button(bar_r, text="加入歌單", bootstyle=SUCCESS, command=self.add_selected_to_queue).pack(fill=X)
        tb.Button(bar_r, text="立即播放", command=self.play_selected_now).pack(fill=X, pady=6)
        tb.Button(bar_r, text="收藏 (最愛)", command=self.add_selected_to_fav).pack(fill=X)
        self.results.bind("<Double-1>", lambda e: self.play_selected_now())

        # 播放佇列
        self.page_queue = tb.Frame(self.nb)
        self.nb.add(self.page_queue, text="播放清單")  # 原本是 "播放佇列"，改成 "播放清單"
        self.queue_view = tb.Treeview(self.page_queue, columns=("title","duration","channel","vid"), show="headings")
        for c,t,w in [("title","標題",600),("duration","時長",90),("channel","頻道",180),("vid","Video ID",160)]:
            self.queue_view.heading(c, text=t)
            self.queue_view.column(c, width=w, anchor=tk.W)
        self.queue_view.pack(fill=BOTH, expand=YES, side=tk.LEFT)
        bar_q = tb.Frame(self.page_queue)
        bar_q.pack(side=tk.LEFT, fill=Y, padx=(6,0))
        tb.Button(bar_q, text="播放選取", command=self.play_selected_in_queue).pack(fill=X)
        tb.Button(bar_q, text="從歌單移除", bootstyle=DANGER, command=self.remove_selected_in_queue).pack(fill=X, pady=6)
        tb.Button(bar_q, text="清空歌單", bootstyle=SECONDARY, command=self.clear_queue).pack(fill=X)

        # 我的最愛
        self.page_fav = tb.Frame(self.nb)
        self.nb.add(self.page_fav, text="我的最愛")
        self.fav_view = tb.Treeview(self.page_fav, columns=("title","duration","channel","vid"), show="headings")
        for c,t,w in [("title","標題",600),("duration","時長",90),("channel","頻道",180),("vid","Video ID",160)]:
            self.fav_view.heading(c, text=t)
            self.fav_view.column(c, width=w, anchor=tk.W)
        self.fav_view.pack(fill=BOTH, expand=YES, side=tk.LEFT)
        bar_f = tb.Frame(self.page_fav)
        bar_f.pack(side=tk.LEFT, fill=Y, padx=(6,0))
        tb.Button(bar_f, text="加入歌單", command=self.add_fav_to_queue).pack(fill=X)
        tb.Button(bar_f, text="立即播放", command=self.play_fav_now).pack(fill=X, pady=6)
        tb.Button(bar_f, text="移除最愛", bootstyle=DANGER, command=self.remove_fav).pack(fill=X)
        self.fav_view.bind("<Double-1>", lambda e: self.play_fav_now())

        self._refresh_fav()

        # 快捷鍵
        self.root.bind("<Return>", lambda e: self.on_search())

    # ---------- 搜尋 ----------
    def on_search(self):
        q = self.keyword.get().strip()
        if not q:
            self._set_status("請輸入關鍵字…")
            return
        self._set_status(f"搜尋中：{q} …")
        self.search_q.put(q)

    def _search_worker(self):
        while True:
            q = self.search_q.get()
            try:
                items = self._search_via_ytdlp(q)
                self.root.after(0, self._fill_results, items, q)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("搜尋失敗", str(e)))

    def _search_via_ytdlp(self, query: str) -> List[VideoItem]:
        if yt_dlp is None:
            raise RuntimeError("未安裝 yt_dlp： pip install yt_dlp")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }
        # 關鍵字加強：伴奏、純音樂、去人聲、KTV、卡拉OK、Karaoke、無人聲、字幕、歌詞
        q = f"ytsearch20:{query} 伴奏 純音樂 去人聲 KTV 卡拉OK Karaoke 無人聲 伴奏 字幕 歌詞"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(q, download=False)
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

    def _fill_results(self, items: List[VideoItem], q: str):
        for row in self.results.get_children():
            self.results.delete(row)
        for it in items:
            self.results.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))
        self._set_status(f"搜尋完成：{q}（共 {len(items)} 首）")

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
            self._set_status("佇列為空")
            return
        nxt = self.queue_items.pop(0)
        self._refresh_queue()
        self._play(nxt)

    def toggle_pause(self):
        self.player.pause()

    def _play(self, item: VideoItem):
        self.current = item
        self.now_label.configure(text=f"{item.title} ({item.channel})")
        self.player.play(item.url)

    def _auto_play_next(self):
        # 自動播放下一首
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

    def add_current_fav(self):
        if self.current:
            self.store.add(self.current)
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
        try:
            print(text)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()

    def voice_search(self):
        import speech_recognition as sr  # 新增
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            self._set_status("請開始說話…")
            audio = recognizer.listen(source, timeout=5)
        try:
            text = recognizer.recognize_google(audio, language="zh-TW")
            self.keyword.set(text)
            self._set_status(f"語音辨識：{text}")
            self.on_search()
        except Exception as e:
            messagebox.showerror("語音辨識失敗", str(e))

if __name__ == "__main__":
    app = KTVApp(theme="flatly")
    app.run()
