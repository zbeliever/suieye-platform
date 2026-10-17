# -*- coding: utf-8 -*-
"""凡口真录像回放段转码

从源 zip 提取 2019-08-22 的 23 段真录像（10:56~12:46，前 7 段静态图弃用），
转码为 640x480 h264 小体积 mp4，部署到 frontend/public/media/froth2019/。

用法: python scripts/transcode_replay_segments.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

import imageio_ffmpeg

ZIP_PATH = r"D:\QQ\2020csu浮选视频.zip\2020csu浮选视频.zip"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "media", "froth2019")

# 真录像段：20190822_105629 起（含）之后的 23 段
TRUE_START = "20190822105629.avi"


def fix_name(n: str) -> str:
    try:
        return n.encode("cp437").decode("gbk")
    except Exception:
        return n


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    z = zipfile.ZipFile(ZIP_PATH)
    infos = [i for i in z.infolist() if fix_name(i.filename).lower().endswith(".avi")]
    infos.sort(key=lambda i: fix_name(i.filename))
    names = [fix_name(i.filename) for i in infos]
    try:
        start = next(i for i, inf in enumerate(infos) if fix_name(inf.filename).split("/")[-1] == TRUE_START)
    except StopIteration:
        print("未找到起始段", TRUE_START)
        sys.exit(1)
    todo = infos[start:]
    print(f"共 {len(todo)} 段待转码", flush=True)

    ok, fail = 0, []
    for idx, info in enumerate(todo, 1):
        name = fix_name(info.filename).split("/")[-1]
        stem = os.path.splitext(name)[0]  # 20190822113630
        out_name = stem[-6:] + ".mp4"     # 113630.mp4
        out_path = os.path.normpath(os.path.join(OUT_DIR, out_name))
        if os.path.exists(out_path) and os.path.getsize(out_path) > 100_000:
            print(f"[{idx}/{len(todo)}] {out_name} 已存在，跳过", flush=True)
            ok += 1
            continue
        # 流式解压到临时文件（AVI 需要 seek，不能直接 pipe）
        tmp_avi = tempfile.NamedTemporaryFile(suffix=".avi", delete=False)
        tmp_avi.close()
        try:
            with z.open(info) as src, open(tmp_avi.name, "wb") as dst:
                shutil.copyfileobj(src, dst)
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", tmp_avi.name,
                "-vf", "scale=640:-2,fps=20",
                "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                "-an", "-movflags", "+faststart",
                out_path,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                fail.append((name, r.stderr[-300:]))
                print(f"[{idx}/{len(todo)}] {name} 失败: {r.stderr[-200:]}", flush=True)
            else:
                ok += 1
                mb = os.path.getsize(out_path) / 1048576
                print(f"[{idx}/{len(todo)}] {name} -> {out_name} {mb:.1f}MB", flush=True)
        finally:
            os.unlink(tmp_avi.name)

    print(f"完成: {ok} 成功, {len(fail)} 失败", flush=True)
    if fail:
        for n, e in fail:
            print("FAIL", n, e, flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
