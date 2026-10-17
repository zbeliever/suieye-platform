"""
YOLO 模型下载工具 — 解决国内网络环境下载问题

使用方法:
  1. 确保网络通畅（关闭代理 / 使用手机热点 / VPN）
  2. 运行: python scripts/download_yolo_model.py
  3. 自动下载 yolov8n.pt 到项目根目录

备用方案（手动）:
  - 浏览器打开: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt
  - 保存为: suieye-platform/yolov8n.pt

镜像源（按优先级尝试）:
  1. GitHub (原始) - 需要畅通网络
  2. hf-mirror.com (HuggingFace 国内镜像)
  3. ModelScope (阿里云)
"""
import os
import sys
import shutil
import urllib.request
import time

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(PROJECT_ROOT, 'yolov8n.pt')
EXPECTED_SIZE = 6_200_000  # ~6.2 MB


def clear_proxy():
    """清除所有代理设置"""
    for k in list(os.environ.keys()):
        if 'proxy' in k.lower():
            del os.environ[k]
    os.environ['NO_PROXY'] = '*'
    os.environ['no_proxy'] = '*'


def download_with_retry(url, dest, max_retries=3, timeout=60):
    """带重试的下载函数"""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"  尝试 {attempt}/{max_retries}: {url[:80]}...")
            urllib.request.urlretrieve(url, dest)
            size = os.path.getsize(dest)
            if size < EXPECTED_SIZE * 0.5:  # 文件太小，可能不完整
                print(f"  ⚠️ 文件异常小 ({size} bytes)，删除重试")
                os.remove(dest)
                continue
            return True
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            if attempt < max_retries:
                wait = attempt * 5
                print(f"  等待 {wait}s 后重试...")
                time.sleep(wait)
    return False


def main():
    # 检查是否已存在
    if os.path.exists(MODEL_PATH):
        size = os.path.getsize(MODEL_PATH)
        if size >= EXPECTED_SIZE * 0.9:
            print(f"✅ 模型已存在: {MODEL_PATH} ({size:,} bytes)")
            # 复制到 Ultralytics 缓存
            cache_dir = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Ultralytics')
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, 'yolov8n.pt')
            if not os.path.exists(cache_path) or os.path.getsize(cache_path) != size:
                shutil.copy2(MODEL_PATH, cache_path)
                print(f"✅ 已复制到缓存: {cache_path}")
            return True
        else:
            print(f"⚠️ 已有文件损坏 ({size} bytes)，重新下载")
            os.remove(MODEL_PATH)

    clear_proxy()

    # 下载源列表（按优先级）
    sources = [
        ("GitHub (原始)", "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt"),
        ("HuggingFace 镜像", "https://hf-mirror.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt"),
        ("ModelScope", "https://www.modelscope.cn/models/ly265666676/YOLOv8_Online/resolve/master/yolov8n.pt"),
    ]

    print("=" * 60)
    print("  YOLOv8n 模型下载工具")
    print("=" * 60)
    print(f"目标路径: {MODEL_PATH}")
    print(f"预期大小: ~{EXPECTED_SIZE // 1024 // 1024} MB")
    print()

    for name, url in sources:
        print(f"\n📡 尝试 [{name}]")
        if download_with_retry(url, MODEL_PATH):
            size = os.path.getsize(MODEL_PATH)
            print(f"\n🎉 下载成功!")
            print(f"   路径: {MODEL_PATH}")
            print(f"   大小: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")

            # 验证模型可加载
            print("\n🔍 验证模型...")
            sys.path.insert(0, PROJECT_ROOT)
            try:
                from ultralytics import YOLO
                model = YOLO(MODEL_PATH)
                print(f"✅ 模型加载成功! 类别数: {len(model.names)}")
                print(f"   类别列表: {list(model.names.values())[:10]}...")
            except Exception as e:
                print(f"⚠️ 模型验证失败（文件可能损坏）: {e}")

            # 复制到缓存
            cache_dir = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Ultralytics')
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, 'yolov8n.pt')
            shutil.copy2(MODEL_PATH, cache_path)
            print(f"✅ 已复制到 Ultralytics 缓存: {cache_path}")
            return True

    print("\n❌ 所有下载源均失败!")
    print("\n可能的解决方案:")
    print("  1. 检查网络连接（尝试手机热点）")
    print("  2. 关闭代理/VPN 后重试")
    print("  3. 手动下载：浏览器打开下面的链接，保存为 yolov8n.pt")
    print(f"     {sources[0][1]}")
    print("  4. 将下载的文件放到项目根目录:")
    print(f"     {PROJECT_ROOT}\\yolov8n.pt")
    return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
