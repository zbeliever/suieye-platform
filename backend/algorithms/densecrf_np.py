"""DenseCRF（全连接条件随机场）的纯 numpy/scipy 实现。

为什么不用 pydensecrf（2026-10 调研结论）：
- Windows + Python 3.13 没有任何可用二进制：
  * PyPI `pydensecrf` 仅提供 sdist（需本机编译）
  * PyPI `pydensecrf2` 仅提供 macOS cp39 wheel
  * conda-forge win-64 构建停在 py312（2024-05 后停更）
- 本机无 MSVC / MinGW，源码编译不可行
- 上游 1.0rc3 (2020) 后停止维护

实现：Krähenbühl & Koltun, NIPS 2011
"Efficient Inference in Fully Connected CRFs with Gaussian Edge Potentials"
的 mean-field 近似推理：
- 平滑核（空间高斯）用 cv2.GaussianBlur（归一化 = row-stochastic）
- 外观核（双边）用 bilateral grid（Chen et al. SIGGRAPH 2007）加速：
  splat → blur → slice，与原版 permutohedral lattice 同族的快速高维高斯滤波

API 刻意仿照 pydensecrf（DenseCRF2D / setUnaryEnergy / addPairwiseGaussian /
addPairwiseBilateral / inference / unary_from_softmax / unary_from_labels），
扁平顺序同样是 (C, H, W) 的 C-order（index = y*W + x）。
若未来 pydensecrf 发布 py313 win wheel，调用方可零改动切换。

注意：numpy 数组判断用 `is not None`，禁止 `or`（已知坑 2）。
"""

import numpy as np
import cv2
from scipy.ndimage import gaussian_filter

__all__ = [
    "DenseCRF2D",
    "unary_from_softmax",
    "unary_from_labels",
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _softmax(x: np.ndarray, axis: int = 0) -> np.ndarray:
    """数值稳定的 softmax。"""
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def unary_from_softmax(probs: np.ndarray, clip: float | None = None) -> np.ndarray:
    """softmax/sigmoid 概率 → 负对数 unary 能量。

    Args:
        probs: (C, H, W) / (C, N) 多类概率；或 (H, W) 二值 sigmoid 概率
               （自动扩展为 [1-p, p] 两类）。
        clip: 可选的概率截断下限（如 1e-5），None 时内部用 1e-8 兜底。

    Returns:
        U: 与输入扁平顺序一致的 (C, N) float32 负对数能量
    """
    p = np.asarray(probs, dtype=np.float32)
    if p.ndim == 2:  # 二值 sigmoid → 两类
        p = np.stack([1.0 - p, p], axis=0)
    lo = clip if clip is not None else 1e-8
    hi = 1.0 - lo if clip is not None else 1.0
    p = np.clip(p, lo, hi)
    u = -np.log(p)
    if u.ndim == 3:
        u = u.reshape(u.shape[0], -1)
    return u.astype(np.float32, copy=False)


def unary_from_labels(
    labels: np.ndarray, nlabels: int, gt_prob: float, zero_unsure: bool = True
) -> np.ndarray:
    """硬标签 → unary 能量（pydensecrf.utils.unary_from_labels 语义）。

    标签为 l 的像素：U(l) = -log(gt_prob)，其余类：-log((1-gt_prob)/(C-1))。
    zero_unsure=True 时，越界标签（<0 或 >=nlabels）的像素设为均匀能量 0。

    Args:
        labels: (H, W) int 标签图（0 = 背景在调用方约定，这里只看数值）
        nlabels: 类别数（>= 2）
        gt_prob: 标签置信度，建议 0.5~0.9
        zero_unsure: 越界标签是否视为"不确定"（均匀能量）

    Returns:
        U: (nlabels, N) float32
    """
    labels = np.asarray(labels)
    assert nlabels >= 2, "nlabels 必须 >= 2"
    gt_prob = float(np.clip(gt_prob, 1.0 / nlabels + 1e-6, 1.0 - 1e-6))
    other = -np.log((1.0 - gt_prob) / (nlabels - 1))

    u = np.full((nlabels, *labels.shape), other, dtype=np.float32)
    for l in range(nlabels):
        u[l][labels == l] = -np.log(gt_prob)

    if zero_unsure:
        unsure = (labels < 0) | (labels >= nlabels)
        if unsure.any():
            u[:, unsure] = 0.0

    return u.reshape(nlabels, -1)


# ---------------------------------------------------------------------------
# 双边网格（bilateral grid）
# ---------------------------------------------------------------------------

class _BilateralGrid:
    """Splat-Blur-Slice 双边网格，近似 5D 高斯双边核。

    k(i,j) = exp(-|p_i - p_j|²/2σs² - |I_i - I_j|²/2σr²)

    网格间距 = 核 σ（此时网格高斯模糊 σ=1）。网格过大时自动放大间距，
    牺牲精度换内存（上限 MAX_CELLS）。

    位置在构造时固定 → mean-field 多次迭代间复用 splat/slice 索引。
    """

    MAX_CELLS = 30_000_000  # 5D 网格单元数上限（float32 × 每通道 ~120MB）

    def __init__(self, image: np.ndarray, sxy: float, srgb: float):
        h, w = image.shape[:2]
        self.n = h * w
        self.height, self.width = h, w

        img = image.reshape(-1, image.shape[2]).astype(np.float32)
        xs = np.tile(np.arange(w, dtype=np.float32), h)
        ys = np.repeat(np.arange(h, dtype=np.float32), w)

        dx = max(1.0, float(sxy))
        dr = max(1.0, float(srgb))

        # 自适应放大间距，防内存爆炸
        while True:
            nx = int(w / dx) + 2
            ny = int(h / dx) + 2
            nc = int(256.0 / dr) + 2
            if nx * ny * nc**3 <= self.MAX_CELLS:
                break
            if nx * ny >= nc**3:
                dx *= 1.5
            else:
                dr *= 1.5
        self.dims = (nx, ny, nc, nc, nc)
        self.size = nx * ny * nc**3

        # 各轴网格模糊 σ（核 σ / 间距；间距=σ 时为 1.0）
        self.sigma = (sxy / dx, sxy / dx, srgb / dr, srgb / dr, srgb / dr)

        # 像素 → 5D 网格坐标
        pos = np.empty((self.n, 5), dtype=np.float32)
        pos[:, 0] = xs / dx
        pos[:, 1] = ys / dx
        pos[:, 2] = img[:, 0] / dr
        pos[:, 3] = img[:, 1] / dr
        pos[:, 4] = img[:, 2] / dr

        lo = np.floor(pos).astype(np.int32)
        fr = (pos - lo).astype(np.float32)  # 各轴插值权重 (N, 5)

        strides = np.array(
            [ny * nc**3, nc**3, nc * nc, nc, 1], dtype=np.int64
        )
        base = (lo.astype(np.int64) * strides).sum(axis=1)  # (N,)

        # 32 个角点的扁平索引与三线性权重
        n_corners = 1 << 5
        self.corners_idx = np.empty((n_corners, self.n), dtype=np.int64)
        self.corners_w = np.empty((n_corners, self.n), dtype=np.float32)
        for k in range(n_corners):
            idx = base.copy()
            wt = np.ones(self.n, dtype=np.float32)
            for ax in range(5):
                if (k >> ax) & 1:
                    idx = idx + strides[ax]
                    wt *= fr[:, ax]
                else:
                    wt *= (1.0 - fr[:, ax])
            self.corners_idx[k] = idx
            self.corners_w[k] = wt

    def _splat(self, values: np.ndarray) -> np.ndarray:
        """(N,) 值 → 5D 网格（三线性 splat）。"""
        grid = np.zeros(self.size, dtype=np.float32)
        for k in range(self.corners_idx.shape[0]):
            grid += np.bincount(
                self.corners_idx[k],
                weights=self.corners_w[k] * values,
                minlength=self.size,
            )
        return grid.reshape(self.dims)

    def _blur(self, grid: np.ndarray) -> np.ndarray:
        """5D 高斯模糊（可分离）。"""
        return gaussian_filter(grid, sigma=self.sigma, mode="constant")

    def _slice(self, grid: np.ndarray) -> np.ndarray:
        """5D 网格 → (N,) 值（三线性 slice）。"""
        g = grid.reshape(-1)
        out = np.zeros(self.n, dtype=np.float32)
        for k in range(self.corners_idx.shape[0]):
            out += g[self.corners_idx[k]] * self.corners_w[k]
        return out

    def make_message_op(self):
        """返回归一化双边核消息算子 op(values) ≈ W @ values / W @ 1。

        归一化后为 row-stochastic（Σ_j w_ij = 1），对应 pydensecrf 的
        NORMALIZE_SYMMETRIC 默认行为。
        """
        # 权重通道与迭代无关，预计算一次
        w_slice = self._slice(self._blur(self._splat(np.ones(self.n, np.float32))))
        w_slice = np.maximum(w_slice, 1e-8)

        def op(values: np.ndarray) -> np.ndarray:
            v = np.asarray(values, dtype=np.float32).reshape(-1)
            return self._slice(self._blur(self._splat(v))) / w_slice

        return op


# ---------------------------------------------------------------------------
# DenseCRF 主体
# ---------------------------------------------------------------------------

class DenseCRF2D:
    """2D 全连接 CRF（pydensecrf.densecrf.DenseCRF2D 的 numpy 替代）。

    典型用法（与 pydensecrf 逐行对应）::

        import numpy as np
        from backend.algorithms.densecrf_np import DenseCRF2D, unary_from_softmax

        probs = np.stack([1 - p, p])          # (2, H, W)，U-Net sigmoid 输出
        U = unary_from_softmax(probs)          # (2, N)
        crf = DenseCRF2D(W, H, 2)
        crf.setUnaryEnergy(U)
        crf.addPairwiseGaussian(sxy=3, compat=3)
        crf.addPairwiseBilateral(sxy=80, srgb=13, rgbim=img_rgb, compat=10)
        Q = crf.inference(5)                   # (2, N)
        refined = (Q[1] > 0.5).reshape(H, W)

    推理细节：mean-field 更新为
        Q_i(l) ∝ exp(-U_i(l) + Σ_k compat_k · μ̃_k,i(l))
    其中 μ̃_k 为归一化核（row-stochastic）下 Q 的核均值；Potts 兼容项的
    常数部分在 softmax 下消去。二类时 μ̃(0) = 1 - μ̃(1)，只需一次滤波。
    """

    def __init__(self, width: int, height: int, nlabels: int):
        assert nlabels >= 2, "nlabels 必须 >= 2"
        self.width = int(width)
        self.height = int(height)
        self.nlabels = int(nlabels)
        self.N = self.width * self.height
        self._unary: np.ndarray | None = None
        self._gaussians: list[tuple[float, float]] = []  # (sxy, compat)
        self._bilaterals: list[tuple[object, float]] = []  # (message_op, compat)

    # ---- unary ----

    def setUnaryEnergy(self, U: np.ndarray):
        """设置 unary 能量 (C, N) 或 (C, H, W)，负对数概率。"""
        U = np.asarray(U, dtype=np.float32)
        if U.ndim == 3:
            U = U.reshape(U.shape[0], -1)
        expected = (self.nlabels, self.N)
        if U.shape != expected:
            raise ValueError(f"unary 形状 {U.shape} != {expected}")
        U = np.nan_to_num(U, nan=1e4, posinf=1e4, neginf=-1e4)
        self._unary = U

    # ---- pairwise ----

    def addPairwiseGaussian(self, sxy: float = 3, compat: float = 3):
        """平滑核：k(i,j)=exp(-|p_i-p_j|²/2σs²)，Potts 兼容度。"""
        self._gaussians.append((float(sxy), float(compat)))

    def addPairwiseBilateral(
        self, sxy: float = 80, srgb: float = 13, rgbim: np.ndarray = None, compat: float = 10
    ):
        """外观核：k(i,j)=exp(-|p_i-p_j|²/2σs²-|I_i-I_j|²/2σr²)。

        rgbim: (H, W, 3) uint8 引导图（BGR/RGB 均可，核对各通道对称）。
        """
        if rgbim is None:
            raise ValueError("addPairwiseBilateral 需要 rgbim")
        im = np.asarray(rgbim)
        if im.shape[:2] != (self.height, self.width):
            raise ValueError(f"引导图形状 {im.shape[:2]} != {(self.height, self.width)}")
        grid = _BilateralGrid(im, sxy, srgb)
        self._bilaterals.append((grid.make_message_op(), float(compat)))

    # ---- inference ----

    def inference(self, n_iter: int = 5) -> np.ndarray:
        """mean-field 推理。

        Args:
            n_iter: 迭代次数（pydensecrf 常用 5~10）

        Returns:
            Q: (nlabels, N) float32，N = H*W（行优先，index = y*W + x）
        """
        if self._unary is None:
            raise RuntimeError("先调用 setUnaryEnergy")
        n_iter = max(1, int(n_iter))

        C = self.nlabels
        U = self._unary.reshape(C, self.height, self.width)
        Q = _softmax(-U, axis=0)

        for _ in range(n_iter):
            logits = -U.copy()

            # 平滑核消息（cv2.GaussianBlur 归一化 → row-stochastic）
            for sxy, compat in self._gaussians:
                if C == 2:
                    mu1 = cv2.GaussianBlur(Q[1], (0, 0), sxy,
                                            borderType=cv2.BORDER_REFLECT_101)
                    logits[1] += compat * mu1
                    logits[0] += compat * (1.0 - mu1)
                else:
                    for c in range(C):
                        logits[c] += compat * cv2.GaussianBlur(
                            Q[c], (0, 0), sxy, borderType=cv2.BORDER_REFLECT_101
                        )

            # 外观核消息（双边网格）
            for op, compat in self._bilaterals:
                if C == 2:
                    mu1 = op(Q[1]).reshape(self.height, self.width)
                    logits[1] += compat * mu1
                    logits[0] += compat * (1.0 - mu1)
                else:
                    for c in range(C):
                        logits[c] += compat * op(Q[c]).reshape(
                            self.height, self.width
                        )

            Q = _softmax(logits, axis=0)

        return Q.reshape(C, self.N)
