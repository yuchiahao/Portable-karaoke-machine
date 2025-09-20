# Portable-karaoke-machine
Portable Karaoke Machine Description: Voice or type song selection, song switching PC/Laptop version Laptop + microphone + network travel around the world
🎤 KTV 伴唱機 (Python Karaoke Player)




📖 系統介紹

本專案是一款 桌面端 KTV 伴唱機應用程式，使用 Python 開發，支援搜尋 YouTube KTV 影片、播放、點歌佇列、收藏，以及語音點歌。
介面以 Tkinter + ttkbootstrap 設計，並結合 VLC 播放引擎 (python-vlc)，提供即時流暢的播放體驗。

✨ 功能特色

🎬 內嵌視窗播放 – 使用 VLC 引擎，無需瀏覽器即可播放 YouTube

🔍 YouTube 即時搜尋 – yt_dlp 抓取串流，免 API 金鑰

🎶 播放控制 – 立即播放 / 加入佇列 / 切歌 / 自動播放下一首

📑 播放清單管理 – 新增、刪除、清空歌單

❤️ 我的最愛 – JSON 儲存收藏歌曲，支援快速播放

🎤 語音點歌 – Google Speech API (zh-TW) 中文語音辨識

🖥 現代化介面 – ttkbootstrap 主題化設計，清晰直覺

🛠 系統架構
介面層         → Tkinter + ttkbootstrap
影音播放層     → VLC 播放引擎 (python-vlc)
影音來源       → YouTube (yt_dlp)
資料儲存       → JSON (favorites.json)
語音辨識       → SpeechRecognition + Google Speech API

🔄 系統流程圖
flowchart TD
    A[輸入關鍵字/語音點歌] --> B[yt_dlp 搜尋 YouTube]
    B --> C[顯示搜尋結果]
    C -->|立即播放| D[VLC 播放影片]
    C -->|加入歌單| E[播放清單 Queue]
    E -->|切歌/自動播放| D
    D -->|加入最愛| F[JSON 收藏清單]
    F -->|點擊最愛| D


(這段使用 Mermaid
 語法，GitHub README 支援直接渲染流程圖)

🚀 安裝與執行
1. 安裝必要套件
pip install ttkbootstrap yt_dlp python-vlc requests speechrecognition


⚠️ 另外需安裝 VLC 播放器
，並確保安裝目錄有 libvlc.dll。
Windows 預設安裝路徑： C:\Program Files\VideoLAN\VLC

2. 執行程式
python ktv_player.py

🎮 使用方式

在上方輸入框輸入關鍵字或點擊 語音點歌

在搜尋結果中選擇歌曲 → 立即播放 或 加入歌單

可切換至 播放清單 或 我的最愛 分頁管理歌曲

使用 切歌 (下一首) 或 播放/暫停 進行控制

📂 專案結構
📁 KTV-Player
 ├─ ktv_player.py       # 主程式
 ├─ favorites.json      # 最愛歌曲儲存檔
 ├─ README.md           # 說明文件
 └─ docs/
     ├─ screenshot.png  # 系統截圖
     └─ demo.gif        # 使用流程示範

🌍 English Summary

KTV Karaoke Player (Python Desktop App)

A Python-based karaoke system with a modern GUI built using Tkinter + ttkbootstrap. It integrates VLC (python-vlc) for embedded YouTube playback and yt-dlp for searching and streaming. Features include queue management, favorites, and voice-controlled search (zh-TW).

📸 預覽

搜尋頁面


播放清單 & 收藏



<img width="2054" height="1557" alt="image" src="https://github.com/user-attachments/assets/94a79672-00d7-4489-bd88-05e84765fa25" />
