# -*- coding: utf-8 -*-
"""
KTV 伴唱機（展示版 Demo）
========================
這是「展示版」：用來在 GitHub 展示 UI 與流程，不提供實際播放/下載/抓取 YouTube 串流。

保留：
- UI（搜尋/清單/最愛/歌單）
- 搜尋流程（以假資料 demo）
- 雙擊/立即播放按鈕（改成提示）
- 語音辨識按鈕（改成提示）

移除/停用：
- yt-dlp 真搜尋
- VLC 播放
- ffmpeg 合併下載
- cookies / 登入流程
- SpeechRecognition / pyaudio 真語音辨識
"""

import os
import sys
import json
import threading
import queue
from dataclasses import dataclass, asdict
from typing import List, Optional

import tkinter as tk
from tkinter import messagebox

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *  # noqa
except ModuleNotFoundError:
    sys.stderr.write("缺少 ttkbootstrap： pip install ttkbootstrap\n")
    raise

APP_TITLE = "KTV 伴唱機（展示版 Demo）"
FAV_FILE = "favorites.json"


@dataclass
class VideoItem:
    title: str
    video_id: str
    duration: str = ""
    channel: str = ""

    @property
    def url(self) -> str:
        # 展示版仍保留 URL 形式（用來展示資料結構）
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


class DemoPlayer:
    """展示版播放器：不播放，只做提示與狀態展示。"""

    def __init__(self, container: tk.Frame):
        self.container = container
        self.on_end_callback = None

        # 放一個「展示用畫面」Label，讓介面看起來像播放器區域
        self.container.configure()
        self.hint = tb.Label(
            self.container,
            text="🎬 Demo Player（展示版不提供播放）\n\n"
                 "此區域用來展示你原本的 VLC 播放畫面位置。\n"
                 "商用版可提供實際播放功能。",
            justify="center",
        )
        self.hint.pack(expand=True)

    def set_on_end(self, callback):
        self.on_end_callback = callback

    def play(self, video_url: str):
        messagebox.showinfo(
            "展示版 Demo",
            "展示版不提供實際播放/抓取 YouTube 串流。\n\n"
            "✅ 你可以在 README 說明：商用版提供完整播放功能。",
        )

    def pause(self):
        messagebox.showinfo("展示版 Demo", "展示版不提供播放/暫停。")

    def stop(self):
        return

    def cleanup_temp(self):
        return


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

        # 介面不變：保留語音辨識按鈕，但展示版不真的錄音
        self.btn_voice = tb.Button(top, text="語音辨識", command=self.voice_search)
        self.btn_voice.pack(side=LEFT, padx=6)

        # 上半部：播放器區（展示版用 DemoPlayer）
        upper = tb.Frame(self.root)
        upper.pack(fill=BOTH, expand=YES, padx=8, pady=(6, 8))

        self.video_frame = tb.Frame(upper, bootstyle="dark", height=480)
        self.video_frame.pack(fill=BOTH, expand=YES)

        self.player = DemoPlayer(self.video_frame)
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
        self._set_status(f"搜尋中（展示版）：{q} …")
        self.search_q.put((q, bool(self.karaoke_mode.get())))

    def _search_worker(self):
        while True:
            query, karaoke = self.search_q.get()
            try:
                items = self._search_demo(query, karaoke)
                self.root.after(0, self._fill_results, items, query, karaoke)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("搜尋失敗", str(e)))

    def _search_demo(self, query: str, karaoke_mode: bool) -> List[VideoItem]:
        """
        展示版搜尋：回傳假資料（Mock）。
        目的：展示 UI 流程與資料結構，避免變成可免費使用的完整工具。
        """
        q = (query or "").strip()
        if not q:
            return []

        # 依模式稍微改一下展示結果（讓人看得出模式切換有效）
        tag = "KTV/伴奏" if karaoke_mode else "一般"
        base_channel = "DemoChannel"

        # 你可以把這裡換成更像真的資料（或固定歌庫）
        demo = [
            VideoItem(title=f"{q} - Demo Song 1 ({tag})", video_id="DEMO0001", duration="03:45", channel=base_channel),
            VideoItem(title=f"{q} - Demo Song 2 ({tag})", video_id="DEMO0002", duration="04:10", channel=base_channel),
            VideoItem(title=f"{q} - Demo Song 3 ({tag})", video_id="DEMO0003", duration="02:58", channel=base_channel),
            VideoItem(title=f"{q} - Demo Song 4 ({tag})", video_id="DEMO0004", duration="05:01", channel=base_channel),
            VideoItem(title=f"{q} - Demo Song 5 ({tag})", video_id="DEMO0005", duration="03:22", channel=base_channel),
        ]
        return demo

    def _fill_results(self, items: List[VideoItem], q: str, karaoke: bool):
        for row in self.results.get_children():
            self.results.delete(row)
        for it in items:
            self.results.insert("", END, values=(it.title, it.duration, it.channel, it.video_id))

        mode = "伴奏/去人聲模式" if karaoke else "一般搜尋"
        self._set_status(f"搜尋完成（展示版｜{mode}）：{q}（共 {len(items)} 首）")

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
            self._set_status("歌單為空（展示版）")
            return
        nxt = self.queue_items.pop(0)
        self._refresh_queue()
        self._play(nxt)

    def toggle_pause(self):
        # 展示版不播放，改提示即可（避免讓人覺得壞掉）
        self.player.pause()

    def voice_search(self):
        """
        展示版：保留按鈕但不做錄音。
        你也可以選擇：直接把示範文字塞進搜尋框，讓流程看起來更完整。
        """
        sample = "周杰倫 稻香"
        self.keyword.set(sample)
        self._set_status(f"語音辨識（展示版）：{sample}（用示範文字）")
        self.on_search()

    def _play(self, item: VideoItem):
        self.current = item
        self.now_label.configure(text=f"{item.title} ({item.channel})")
        self.player.play(item.url)

    def _auto_play_next(self):
        # 展示版不會真的播完，保留結構即可
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
        try:
            print(text)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = KTVApp(theme="flatly")
    app.run()
