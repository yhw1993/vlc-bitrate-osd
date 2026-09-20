#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VLC Bitrate OSD Monitor
=======================
通过 VLC HTTP 接口获取实时统计, 以半透明置顶窗口叠加显示视频流码率。

使用前提:
  1. 以 HTTP 接口模式启动 VLC:
     vlc --extraintf http --http-port 8080 --http-password vlc123

  2. 或者修改 VLC 设置:
     工具 > 首选项 > 显示设置: 全部 > 主界面 > HTTP
     设置端口和密码

  3. 运行本脚本:
     python vlc_bitrate_monitor.py
     python vlc_bitrate_monitor.py --host 127.0.0.1 --port 8080 --password vlc123

功能:
  - 半透明置顶窗口, 叠加在 VLC 视频窗口上
  - 实时显示: 即时码率、平均码率、峰值码率、输入码率
  - 显示: 分辨率、帧率、丢帧率、音频缓冲
  - 码率实时曲线图 (最近 60 个采样点)
  - 窗口可拖拽移动, 可调节透明度

作者: WorkBuddy
版本: 1.0
"""

import argparse
import math
import sys
import threading
import time
import urllib.request
import urllib.error
import base64
import xml.etree.ElementTree as ET
from collections import deque

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:
    print("错误: 需要 tkinter 模块。请安装 Python Tkinter 支持。")
    sys.exit(1)


# ============================================================
# VLC HTTP 接口客户端
# ============================================================
class VLCClient:
    """通过 VLC HTTP 接口获取统计数据"""

    def __init__(self, host="127.0.0.1", port=8080, password=""):
        self.base_url = f"http://{host}:{port}"
        self.host = host
        self.port = port
        self.password = password
        self._auth_header = None
        if password:
            cred = f":{password}".encode()
            self._auth_header = "Basic " + base64.b64encode(cred).decode()

    def get_status(self):
        """获取 VLC 状态 XML, 返回解析后的统计字典"""
        url = f"{self.base_url}/requests/status.xml"
        req = urllib.request.Request(url)
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                xml_data = resp.read()
        except urllib.error.URLError as e:
            raise ConnectionError(f"无法连接 VLC HTTP 接口: {e}")
        except Exception as e:
            raise ConnectionError(f"请求失败: {e}")

        root = ET.fromstring(xml_data)

        stats = {}
        stats_node = root.find("stats")
        if stats_node is not None:
            for child in stats_node:
                stats[child.tag] = child.text

        info = {}
        info_node = root.find(".//information/category[@name='meta']")
        if info_node is not None:
            for child in info_node:
                info[child.get("name", child.tag)] = child.text

        state_node = root.find("state")
        stats["_state"] = state_node.text if state_node is not None else "unknown"

        pos_node = root.find("position")
        stats["_position"] = float(pos_node.text) if pos_node is not None else 0

        length_node = root.find("length")
        stats["_length"] = float(length_node.text) if length_node is not None else 0

        return stats, info

    def is_connected(self):
        """检查是否连接到 VLC"""
        try:
            self.get_status()
            return True
        except Exception:
            return False


# ============================================================
# 码率统计计算
# ============================================================
class BitrateTracker:
    """跟踪和计算码率统计"""

    def __init__(self, max_samples=120):
        self.max_samples = max_samples
        self.history = deque(maxlen=max_samples)
        self.prev_demux_bytes = None
        self.prev_time = None

    def update(self, stats):
        """更新统计, 返回格式化数据"""
        # VLC 内置 inputbitrate/demuxbitrate 实测单位是 MB/s (值通常 <1000),
        # 统一在这里转换为 bytes/s, 之后所有计算都用统一单位
        raw_input_br = float(stats.get("inputbitrate", 0))
        raw_demux_br = float(stats.get("demuxbitrate", 0))
        if raw_input_br < 1000:
            raw_input_br *= 1048576  # MB/s -> B/s
        if raw_demux_br < 1000:
            raw_demux_br *= 1048576  # MB/s -> B/s

        result = {
            "connected": True,
            "state": stats.get("_state", "unknown"),
            "input_bitrate": raw_input_br,
            "demux_bitrate": raw_demux_br,
            "read_bytes": int(float(stats.get("readbytes", 0))),
            "demux_read_bytes": int(float(stats.get("demuxreadbytes", 0))),
            "displayed_pictures": int(float(stats.get("displayedpictures", 0))),
            "lost_pictures": int(float(stats.get("lostpictures", 0))),
            "played_abuffers": int(float(stats.get("playedabuffers", 0))),
            "lost_abuffers": int(float(stats.get("lostabuffers", 0))),
            "discontinuities": int(float(stats.get("discontinuities", 0))),
            "position": stats.get("_position", 0),
            "length": stats.get("_length", 0),
        }

        # 始终从字节差值计算即时码率 (VLC 内置值可能单位不一致)
        now = time.time()
        instant_br = 0
        state = result["state"]

        # 仅在播放状态下计算即时码率; 暂停/停止时码率为 0
        if state == "playing":
            if self.prev_demux_bytes is not None and self.prev_time is not None:
                dt = now - self.prev_time
                if dt > 0:
                    db = result["demux_read_bytes"] - self.prev_demux_bytes
                    if db > 0:
                        instant_br = db / dt

            # 如果差值为0 (本地文件已读入缓存) 且正在播放, 回退到 VLC 内置值
            # (demux_bitrate 已在上方统一转换为 bytes/s)
            # 注意: VLC 内置值是 EMA 平滑残值, 暂停时不会归零, 所以只在播放时使用
            if instant_br == 0 and result["demux_bitrate"] > 0:
                instant_br = result["demux_bitrate"]

        self.prev_demux_bytes = result["demux_read_bytes"]
        self.prev_time = now

        # 记录历史 (仅当有实际码率时, 暂停/停止不记录)
        if instant_br > 0:
            self.history.append(instant_br)

        # 计算平均和峰值
        if len(self.history) > 0:
            result["avg_bitrate"] = sum(self.history) / len(self.history)
            result["peak_bitrate"] = max(self.history)
        else:
            result["avg_bitrate"] = 0
            result["peak_bitrate"] = 0

        result["instant_bitrate"] = instant_br
        result["samples"] = len(self.history)

        # 丢帧率
        total = result["displayed_pictures"] + result["lost_pictures"]
        result["loss_rate"] = (result["lost_pictures"] / total * 100) if total > 0 else 0

        return result

    def reset(self):
        self.history.clear()
        self.prev_demux_bytes = None
        self.prev_time = None


# ============================================================
# 格式化工具
# ============================================================
def fmt_bitrate(bytes_per_sec):
    if bytes_per_sec <= 0:
        return "0 bps"
    bps = bytes_per_sec * 8
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    elif bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    elif bps >= 1_000:
        return f"{bps / 1_000:.1f} kbps"
    else:
        return f"{int(bps)} bps"


def fmt_bytes(b):
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.2f} GB"
    elif b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    elif b >= 1024:
        return f"{b / 1024:.1f} KB"
    else:
        return f"{int(b)} B"


def fmt_time(seconds):
    if seconds <= 0:
        return "00:00"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


# ============================================================
# OSD 浮窗界面
# ============================================================
class OSDFloatWindow:
    """半透明置顶 OSD 浮窗"""

    def __init__(self, vlc_client, update_interval=500, instance=0):
        self.client = vlc_client
        self.tracker = BitrateTracker()
        self.update_interval = update_interval
        self.instance = instance
        self.running = False
        self.connected = False
        self.alpha = 0.85
        self.show_graph = True

        self.root = tk.Tk()
        self.root.title(f"VLC Bitrate OSD :{vlc_client.port}")
        self._setup_window()
        self._setup_ui()
        self._bind_events()

    def _find_vlc_windows(self):
        """找到所有 VLC 视频窗口位置和大小 (返回 (x,y,w,h) 列表, 按面积降序)"""
        try:
            import ctypes
            from ctypes import wintypes

            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowRect = ctypes.windll.user32.GetWindowRect
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible

            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            results = []

            def callback(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        title = buff.value
                        if "VLC" in title and "media player" in title.lower():
                            rect = wintypes.RECT()
                            GetWindowRect(hwnd, ctypes.byref(rect))
                            results.append((rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
                return True

            EnumWindows(EnumWindowsProc(callback), 0)

            # 按面积降序排列
            results.sort(key=lambda r: r[2] * r[3], reverse=True)
            return results
        except Exception as e:
            sys.stderr.write(f"find_vlc_windows error: {e}\n")
        return []

    def _setup_window(self):
        """配置窗口属性"""
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes("-topmost", True)  # 置顶
        self.root.attributes("-alpha", self.alpha)  # 透明度
        self.root.configure(bg="#1a1a2e")

        # 初始位置: 优先放到对应 instance 的 VLC 视频窗口右上角 (OSD 叠加效果)
        win_w, win_h = 400, 320
        sw = self.root.winfo_screenwidth()
        vlc_windows = self._find_vlc_windows()

        if self.instance < len(vlc_windows):
            vx, vy, vw, vh = vlc_windows[self.instance]
            x = vx + vw - win_w - 20
            y = vy + 40
            sys.stderr.write(f"OSD[{self.instance}] positioned on VLC window: ({vx},{vy},{vw}x{vh}) -> ({x},{y})\n")
        else:
            # 找不到对应的第 N 个 VLC 窗口: 按 instance 号偏移, 避免多个 OSD 重叠
            x = sw - win_w - 20
            y = 40 + self.instance * (win_h + 30)
            if y + win_h > self.root.winfo_screenheight():
                x = sw - win_w - 20 - self.instance * 30
                y = 40
            sys.stderr.write(f"VLC window [{self.instance}] not found, using offset position: ({x},{y})\n")
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def _setup_ui(self):
        """创建界面元素"""
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        accent = "#00d4ff"
        warn = "#ff4444"
        ok = "#44ff44"

        mono = ("Consolas", 10)

        # 标题栏
        title_frame = tk.Frame(self.root, bg="#16213e", height=28)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)

        self.title_label = tk.Label(
            title_frame,
            text=f"  VLC 码率统计 OSD [:{self.client.port}]",
            bg="#16213e", fg=accent, font=("Microsoft YaHei UI", 9, "bold")
        )
        self.title_label.pack(side="left", padx=5)

        # 状态指示
        self.status_dot = tk.Label(
            title_frame, text="●", fg=warn, bg="#16213e", font=("Arial", 10)
        )
        self.status_dot.pack(side="right", padx=8)

        # 主内容区
        content = tk.Frame(self.root, bg=bg)
        content.pack(fill="both", expand=True, padx=8, pady=4)

        # 码率信息
        info_frame = tk.Frame(content, bg=bg)
        info_frame.pack(fill="x")

        self.lbl_instant = tk.Label(
            info_frame, text="即时: --", bg=bg, fg=accent, font=mono, anchor="w"
        )
        self.lbl_instant.pack(fill="x")

        self.lbl_avg = tk.Label(
            info_frame, text="平均: --", bg=bg, fg=fg, font=mono, anchor="w"
        )
        self.lbl_avg.pack(fill="x")

        self.lbl_peak = tk.Label(
            info_frame, text="峰值: --", bg=bg, fg=fg, font=mono, anchor="w"
        )
        self.lbl_peak.pack(fill="x")

        self.lbl_input = tk.Label(
            info_frame, text="输入: --", bg=bg, fg="#888", font=mono, anchor="w"
        )
        self.lbl_input.pack(fill="x")

        # 分隔线
        tk.Frame(content, bg="#333", height=1).pack(fill="x", pady=4)

        # 视频信息
        self.lbl_video = tk.Label(
            content, text="分辨率: -- | 帧率: -- fps", bg=bg, fg=fg, font=mono, anchor="w"
        )
        self.lbl_video.pack(fill="x")

        self.lbl_frames = tk.Label(
            content, text="丢帧: 0 (0.0%)", bg=bg, fg=ok, font=mono, anchor="w"
        )
        self.lbl_frames.pack(fill="x")

        self.lbl_audio = tk.Label(
            content, text="音频: 0/0 | 不连续: 0", bg=bg, fg=fg, font=mono, anchor="w"
        )
        self.lbl_audio.pack(fill="x")

        # 码率曲线图
        self.canvas = tk.Canvas(
            content, bg="#0d1117", highlightthickness=1,
            highlightbackground="#333", height=80
        )
        self.canvas.pack(fill="x", pady=4)

        # 底部状态
        self.lbl_status = tk.Label(
            content, text="正在连接 VLC...", bg=bg, fg="#888", font=mono, anchor="w"
        )
        self.lbl_status.pack(fill="x")

        self.lbl_data = tk.Label(
            content, text="已读: 0 | 解复用: 0", bg=bg, fg="#666", font=mono, anchor="w"
        )
        self.lbl_data.pack(fill="x")

    def _bind_events(self):
        """绑定拖拽和快捷键事件"""
        # 拖拽窗口
        self._drag_data = {"x": 0, "y": 0}

        def start_drag(e):
            # 点击窗口时强制获得键盘焦点 (无边框窗口默认无焦点)
            try:
                self.root.focus_set()
            except Exception:
                pass
            self._drag_data["x"] = e.x
            self._drag_data["y"] = e.y

        def on_drag(e):
            x = self.root.winfo_x() + (e.x - self._drag_data["x"])
            y = self.root.winfo_y() + (e.y - self._drag_data["y"])
            self.root.geometry(f"+{x}+{y}")

        self.root.bind("<ButtonPress-1>", start_drag)
        self.root.bind("<B1-Motion>", on_drag)

        # 快捷键: 使用 bind_all 全局绑定 (无边框 overrideredirect 窗口不接收焦点,
        # 普通 bind 到 root 上按了没反应, bind_all 不依赖窗口焦点)
        # 退出: Esc / Q / q
        self.root.bind_all("<Escape>", lambda e: self.stop())
        self.root.bind_all("<q>", lambda e: self.stop())
        self.root.bind_all("<Q>", lambda e: self.stop())

        # 显示/隐藏曲线图: G / g
        self.root.bind_all("<g>", lambda e: self.toggle_graph())
        self.root.bind_all("<G>", lambda e: self.toggle_graph())

        # 透明度: + / - / = / 小键盘
        self.root.bind_all("<plus>", lambda e: self.adjust_alpha(0.05))
        self.root.bind_all("<KP_Add>", lambda e: self.adjust_alpha(0.05))
        self.root.bind_all("<equal>", lambda e: self.adjust_alpha(0.05))
        self.root.bind_all("<Shift-equal>", lambda e: self.adjust_alpha(0.05))
        self.root.bind_all("<minus>", lambda e: self.adjust_alpha(-0.05))
        self.root.bind_all("<KP_Subtract>", lambda e: self.adjust_alpha(-0.05))

        # 窗口创建后强制获取焦点, 提高键盘响应可靠性
        try:
            self.root.focus_force()
        except Exception:
            pass

    def toggle_graph(self):
        self.show_graph = not self.show_graph
        if self.show_graph:
            self.canvas.pack(fill="x", pady=4)
        else:
            self.canvas.pack_forget()

    def adjust_alpha(self, delta):
        self.alpha = max(0.3, min(1.0, self.alpha + delta))
        self.root.attributes("-alpha", self.alpha)

    def draw_graph(self, history, peak):
        """绘制码率曲线图"""
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return

        # 背景网格
        for i in range(1, 4):
            y = h * i / 4
            self.canvas.create_line(0, y, w, y, fill="#1a2332", dash=(2, 4))

        if len(history) < 2 or peak <= 0:
            self.canvas.create_text(
                w // 2, h // 2, text="等待数据...",
                fill="#555", font=("Consolas", 8)
            )
            return

        # 绘制曲线
        n = len(history)
        points = []
        for i, v in enumerate(history):
            x = (i / (n - 1)) * w if n > 1 else 0
            y = h - (v / peak) * h * 0.9 - 4
            points.extend([x, y])

        # 填充区域
        if len(points) >= 4:
            fill_points = points + [w, h, 0, h]
            self.canvas.create_polygon(
                *fill_points, fill="#0d2818", outline=""
            )

        # 曲线
        self.canvas.create_line(
            *points, fill="#00d4ff", width=2, smooth=True
        )

        # 峰值标注
        self.canvas.create_text(
            w - 5, 10, text=f"Peak: {fmt_bitrate(peak)}",
            fill="#ff6b6b", font=("Consolas", 7), anchor="ne"
        )

    def update_display(self, data):
        """更新界面显示"""
        if data is None:
            self.status_dot.config(fg="#ff4444")
            self.lbl_status.config(text="未连接 - 请确认 VLC 已开启 HTTP 接口", fg="#ff4444")
            return

        self.status_dot.config(fg="#44ff44")

        # 码率信息
        self.lbl_instant.config(text=f"即时: {fmt_bitrate(data['instant_bitrate'])}")
        self.lbl_avg.config(text=f"平均: {fmt_bitrate(data['avg_bitrate'])}")
        self.lbl_peak.config(text=f"峰值: {fmt_bitrate(data['peak_bitrate'])}")
        self.lbl_input.config(text=f"输入: {fmt_bitrate(data['input_bitrate'])}")

        # 视频信息
        pos_str = fmt_time(data["position"] * data["length"]) if data["length"] > 0 else "--:--"
        self.lbl_video.config(text=f"位置: {pos_str} | 状态: {data['state']}")

        # 帧统计
        loss_color = "#44ff44"
        if data["loss_rate"] > 5:
            loss_color = "#ff4444"
        elif data["loss_rate"] > 1:
            loss_color = "#ffaa00"
        self.lbl_frames.config(
            text=f"显示: {data['displayed_pictures']} | 丢帧: {data['lost_pictures']} ({data['loss_rate']:.1f}%)",
            fg=loss_color
        )

        # 音频 (注意: 音频块与视频帧是不同的计量单位, 数量差异是正常的)
        self.lbl_audio.config(
            text=f"音频块: {data['played_abuffers']} 播 / {data['lost_abuffers']} 丢 | 不连续: {data['discontinuities']}"
        )

        # 数据量
        self.lbl_data.config(
            text=f"已读: {fmt_bytes(data['read_bytes'])} | 解复用: {fmt_bytes(data['demux_read_bytes'])}"
        )

        # 状态
        self.lbl_status.config(
            text=f"采样: {data['samples']} | 更新: {self.update_interval}ms | 拖拽移动 | Esc退出 G图 +/-透明",
            fg="#666"
        )

        # 绘制曲线图
        if self.show_graph:
            self.draw_graph(list(self.tracker.history), data["peak_bitrate"])

    def poll_loop(self):
        """后台轮询 VLC 统计数据"""
        while self.running:
            try:
                stats, info = self.client.get_status()
                data = self.tracker.update(stats)
                self.connected = True
                self.root.after(0, lambda d=data: self.update_display(d))
            except Exception as e:
                self.connected = False
                self.root.after(0, lambda: self.update_display(None))

            time.sleep(self.update_interval / 1000.0)

    def start(self):
        """启动 OSD 浮窗"""
        self.running = True
        thread = threading.Thread(target=self.poll_loop, daemon=True)
        thread.start()
        self.root.mainloop()

    def stop(self):
        """停止"""
        self.running = False
        try:
            # 清理全局快捷键绑定, 避免影响其他窗口
            self.root.unbind_all()
        except Exception:
            pass
        self.root.destroy()


# ============================================================
# 主程序入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="VLC 码率统计 OSD 浮窗",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用步骤:
  1. 启动 VLC 并开启 HTTP 接口:
     vlc --extraintf http --http-port 8080 --http-password vlc123

  2. 运行本脚本:
     python vlc_bitrate_monitor.py --password vlc123

  3. 在 VLC 中播放视频, OSD 浮窗会显示实时码率统计

快捷键:
  鼠标拖拽 - 移动窗口位置
  G        - 显示/隐藏码率曲线图
  + / -    - 调节窗口透明度
  Esc / Q  - 退出
        """
    )
    parser.add_argument("--host", default="127.0.0.1", help="VLC HTTP 接口地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="VLC HTTP 接口端口 (默认: 8080)")
    parser.add_argument("--password", default="", help="VLC HTTP 接口密码")
    parser.add_argument("--interval", type=int, default=500, help="更新间隔毫秒 (默认: 500)")
    parser.add_argument("--no-graph", action="store_true", help="不显示码率曲线图")
    parser.add_argument("--instance", type=int, default=0,
                        help="多实例模式: 指定定位到第几个 VLC 窗口 (0=第一个, 1=第二个, 默认: 0)")

    args = parser.parse_args()

    client = VLCClient(args.host, args.port, args.password)

    # 测试连接
    print(f"正在连接 VLC HTTP 接口 http://{args.host}:{args.port} ...")
    try:
        stats, info = client.get_status()
        print(f"连接成功! VLC 状态: {stats.get('_state', 'unknown')}")
    except Exception as e:
        print(f"警告: {e}")
        print("请确认 VLC 已以 HTTP 接口模式启动:")
        print(f'  vlc --extraintf http --http-port {args.port} --http-password {args.password or "vlc123"}')
        print("正在启动 OSD 浮窗 (将在 VLC 连接后显示数据)...\n")

    osd = OSDFloatWindow(client, args.interval, instance=args.instance)
    if args.no_graph:
        osd.show_graph = False
        osd.canvas.pack_forget()

    print(f"OSD 浮窗已启动 (instance={args.instance}, 端口 {args.port})。")
    print("快捷键: 鼠标拖拽移动 | G=曲线图 | +/-=透明度 | Esc/Q=退出")
    osd.start()


if __name__ == "__main__":
    main()
