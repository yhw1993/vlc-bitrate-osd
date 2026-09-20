#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VLC 双流时间戳同步脚本
=======================
让两个 VLC 播放器 (一个播 RTSP 网络流, 一个播本地文件) 画面时间戳对齐。

背景:
  - VLC A: 本地视频文件 (完全可控, 可暂停/seek)
  - VLC B: RTSP 网络流 (外部设备, 不可控, 有网络+缓冲延迟)
  两个播放器同时播放时, 因为启动时刻、网络延迟、缓冲不同, 时间戳会漂移。

原理 (类似 NTP 校时):
  1. 以 B (RTSP 流) 为主时钟
  2. 等 B 播放到 offset 秒 (已过网络缓冲期) 后, 让 A seek 到同一时间点并播放
  3. 之后每轮询间隔对比两个 position, 若漂移超过阈值, 暂停领先的播放器一小段再恢复
  4. 循环维持同步

用法:
  python vlc_sync.py --a 127.0.0.1:8080 --b 127.0.0.1:8081 --password vlc123 --offset 3
     - --a / --b : 两个 VLC 的 HTTP 接口 (host:port), --a 默认是从机(本地文件), --b 默认是主机(RTSP)
     - --offset   : 等主机播放到第 N 秒后才对齐从机 (建议 >= 3, 覆盖 RTSP 缓冲期)
     - --master   : 指定哪个是主时钟 (a 或 b, 默认 b=RTSP流)
     - --drift    : 允许的漂移阈值秒 (默认 0.3)
     - --interval : 纠偏轮询间隔毫秒 (默认 500)
     - --once     : 只对齐一次然后退出 (不持续纠偏)
"""

import argparse
import base64
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


# ============================================================
# VLC HTTP 客户端
# ============================================================
class VLCClient:
    """连接单个 VLC HTTP 接口"""

    def __init__(self, host="127.0.0.1", port=8080, password=""):
        self.base_url = f"http://{host}:{port}"
        self.host = host
        self.port = port
        self._auth_header = None
        if password:
            cred = f":{password}".encode()
            self._auth_header = "Basic " + base64.b64encode(cred).decode()

    def _request(self, path):
        url = self.base_url + path
        req = urllib.request.Request(url)
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read()

    def get_status(self):
        """获取状态, 返回 (state, position_seconds, length_seconds, playing)"""
        data = self._request("/requests/status.xml")
        root = ET.fromstring(data)
        state = (root.findtext("state") or "unknown").strip()
        pos = float(root.findtext("position") or 0)
        length = float(root.findtext("length") or 0)
        return {
            "state": state,
            "position": pos * length if length > 0 else 0,  # 绝对秒
            "length": length,
            "playing": state == "playing",
        }

    def play(self):
        """强制播放"""
        self._request("/requests/status.xml?command=pl_play")

    def pause(self):
        """强制暂停 (in_pause 是暂停, 不是切换)"""
        try:
            self._request("/requests/status.xml?command=in_pause")
        except Exception:
            # 某些版本用 pl_pause (切换) 兜底
            self._request("/requests/status.xml?command=pl_pause")

    def seek(self, seconds):
        """跳转到指定秒 (绝对秒; RTSP live 流可能不支持)
        注意: VLC 3.0 的 HTTP seek 命令传小数秒会触发 bug (如 seek(13.52) 会跳到 52s),
        所以统一取整到整数秒, 剩余误差由纠偏逻辑微调。"""
        val = int(round(seconds))
        self._request(f"/requests/status.xml?command=seek&val={val}")

    def is_seekable(self):
        """检测是否可 seek: 有固定时长即可 seek (live 流 length 通常为 0)"""
        st = self.get_status()
        return st["length"] > 0


# ============================================================
# 同步引擎
# ============================================================
class VLCSync:
    def __init__(self, client_a, client_b, master="b", offset=3.0,
                 drift=0.5, interval=0.5, once=False, verbose=True):
        """
        client_a: 从机 (默认本地文件, 可控)
        client_b: 主机 (默认 RTSP 流, 不可控)
        master:   "a" 或 "b", 指定谁做主时钟
        """
        self.a = client_a
        self.b = client_b
        self.master = master
        self.offset = offset
        self.drift = drift
        self.interval = interval
        self.once = once
        self.verbose = verbose

        # 主/从引用
        if master == "a":
            self.master_client, self.slave_client = self.a, self.b
            self.master_name, self.slave_name = "A", "B"
        else:
            self.master_client, self.slave_client = self.b, self.a
            self.master_name, self.slave_name = "B", "A"

    def log(self, msg):
        if self.verbose:
            print(f"[sync] {msg}", flush=True)

    def get_abs_pos(self, client):
        """获取绝对播放位置(秒)"""
        try:
            st = client.get_status()
            return st["position"], st["playing"]
        except Exception as e:
            return None, False

    def wait_master_reach(self, target_sec, timeout=120):
        """等待主机播放到 target_sec 秒 (确保已过 RTSP 缓冲期)"""
        self.log(f"等待主时钟 {self.master_name} 播放到 {target_sec:.1f}s (覆盖网络缓冲)...")
        t0 = time.time()
        while time.time() - t0 < timeout:
            pos, _ = self.get_abs_pos(self.master_client)
            if pos is not None and pos >= target_sec - 0.2:
                self.log(f"主时钟已到 {pos:.2f}s, 开始对齐")
                return True
            time.sleep(0.3)
        self.log("等待超时, 继续尝试对齐")
        return False

    def align_once(self):
        """执行一次对齐: 从机 seek 到主机当前位置后同时播放"""
        # 1. 暂停从机
        self.slave_client.pause()
        time.sleep(0.2)

        # 2. 读取主机当前位置
        mpos, _ = self.get_abs_pos(self.master_client)
        if mpos is None:
            self.log("无法读取主时钟位置")
            return False

        # 3. 从机 seek 到主机位置 (如不可 seek, 尝试暂停等待)
        try:
            self.slave_client.seek(mpos)
            self.log(f"从机 {self.slave_name} seek 到 {mpos:.2f}s")
        except Exception as e:
            self.log(f"从机 seek 失败({e}), 尝试直接用当前时间点播放")
        time.sleep(0.3)

        # 4. 同时恢复播放 (先主后从, 间隔极小)
        if not self.master_client.get_status()["playing"]:
            self.master_client.play()
        self.slave_client.play()

        # 5. 读回验证实际同步误差
        time.sleep(0.5)
        mpos2, _ = self.get_abs_pos(self.master_client)
        spos2, _ = self.get_abs_pos(self.slave_client)
        if mpos2 is not None and spos2 is not None:
            self.log(f"对齐验证: 主={mpos2:.2f}s 从={spos2:.2f}s 误差={abs(mpos2 - spos2):.2f}s")
        return True

    def correct_once(self):
        """检查并纠正一次漂移"""
        mpos, mplaying = self.get_abs_pos(self.master_client)
        spos, splaying = self.get_abs_pos(self.slave_client)

        if mpos is None or spos is None or not mplaying or not splaying:
            return

        diff = mpos - spos  # 正: 从机落后; 负: 从机超前
        if abs(diff) <= self.drift:
            return  # 在阈值内, 无需干预

        if diff > 0:
            # 从机落后 -> 快进: seek 到主机位置 (从机是本地文件, 可 seek)
            self.log(f"从机落后 {diff:.2f}s, seek 快进到主位置 {mpos:.2f}s")
            try:
                self.slave_client.pause()
                self.slave_client.seek(mpos)
                time.sleep(0.2)
                self.slave_client.play()
            except Exception as e:
                self.log(f"从机 seek 失败: {e}")
        else:
            # 从机超前 -> 暂停从机一小段等主机追上 (暂停期间主机继续走, 偏差缩小)
            lag = -diff - self.drift
            self.log(f"从机超前 {-diff:.2f}s, 暂停从机 {lag:.2f}s 等主机")
            self.slave_client.pause()
            time.sleep(min(lag, 3.0))  # 单次最多停 3 秒, 防止长暂停
            self.slave_client.play()

    def run(self):
        """主流程: 等待主时钟过缓冲 -> 对齐 -> 持续纠偏"""
        self.log("=== VLC 双流同步 ===")
        self.log(f"主时钟: {self.master_name} ({self.master_client.host}:{self.master_client.port})")
        self.log(f"从机:   {self.slave_name} ({self.slave_client.host}:{self.slave_client.port})")
        self.log(f"对齐偏移: {self.offset}s | 漂移阈值: {self.drift}s | 轮询: {self.interval*1000:.0f}ms")

        # 检查两个 VLC 是否在线
        for name, c in (("A", self.a), ("B", self.b)):
            try:
                st = c.get_status()
                self.log(f"VLC {name} 在线: state={st['state']}, pos={st['position']:.1f}s")
            except Exception as e:
                self.log(f"VLC {name} 无法连接: {e}")
                self.log("请确认两个 VLC 都已启动并开启 HTTP 接口")
                return

        # 主机可 seek 性提示
        if not self.master_client.is_seekable():
            self.log(f"主时钟 {self.master_name} 是 live 流(无固定时长), 只能通过暂停/恢复对齐")
        if not self.slave_client.is_seekable():
            self.log(f"警告: 从机 {self.slave_name} 也不可 seek, 对齐精度受限")

        # 1. 确保主机在播放
        if not self.master_client.get_status()["playing"]:
            self.log(f"主时钟 {self.master_name} 未播放, 先恢复播放")
            self.master_client.play()

        # 2. 等主机过缓冲期
        if self.offset > 0:
            self.wait_master_reach(self.offset)

        # 3. 执行首次对齐
        self.align_once()

        if self.once:
            self.log("--once 模式: 对齐完成, 退出")
            return

        # 4. 持续纠偏
        self.log("持续纠偏中 (Ctrl+C 退出)...")
        try:
            while True:
                self.correct_once()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            self.log("已停止同步")


# ============================================================
# 主程序
# ============================================================
def parse_endpoint(s):
    """解析 host:port"""
    if ":" in s:
        host, port = s.rsplit(":", 1)
        return host, int(port)
    return s, 8080


def main():
    parser = argparse.ArgumentParser(
        description="VLC 双流时间戳同步 (RTSP 网络流 + 本地文件对齐)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
典型场景:
  产品设备通过 RTSP 发送二次编码的流, 同时设备本地存有原始文件。
  两个 VLC 各播一路, 用本脚本对齐画面时间戳。

用法示例:
  1. 启动 VLC A (本地文件, 端口 8080):
     vlc --extraintf http --http-port 8080 --http-password vlc123 local.mp4

  2. 启动 VLC B (RTSP 流, 端口 8081):
     vlc --extraintf http --http-port 8081 --http-password vlc123 rtsp://device/stream

  3. 运行同步 (B 是主时钟/RTSP, A 是从机/本地文件):
     python vlc_sync.py --a 127.0.0.1:8080 --b 127.0.0.1:8081 --password vlc123 --offset 3
        """
    )
    parser.add_argument("--a", default="127.0.0.1:8080", help="VLC A HTTP 接口 (默认 127.0.0.1:8080)")
    parser.add_argument("--b", default="127.0.0.1:8081", help="VLC B HTTP 接口 (默认 127.0.0.1:8081)")
    parser.add_argument("--password", default="", help="HTTP 密码")
    parser.add_argument("--master", choices=["a", "b"], default="b",
                        help="主时钟: a 或 b (默认 b=RTSP流, a=本地文件跟随)")
    parser.add_argument("--offset", type=float, default=3.0,
                        help="等主时钟播放到第 N 秒后再对齐从机, 覆盖缓冲期 (默认 3)")
    parser.add_argument("--drift", type=float, default=0.5,
                        help="允许的漂移阈值秒 (默认 0.5, 覆盖 seek 关键帧对齐误差)")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="纠偏轮询间隔秒 (默认 0.5)")
    parser.add_argument("--once", action="store_true", help="只对齐一次后退出")

    args = parser.parse_args()

    ha, pa = parse_endpoint(args.a)
    hb, pb = parse_endpoint(args.b)

    client_a = VLCClient(ha, pa, args.password)
    client_b = VLCClient(hb, pb, args.password)

    sync = VLCSync(client_a, client_b, master=args.master,
                   offset=args.offset, drift=args.drift,
                   interval=args.interval, once=args.once)
    sync.run()


if __name__ == "__main__":
    main()
