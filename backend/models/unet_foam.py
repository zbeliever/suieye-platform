"""U-Net 模型架构 —— 专为泡沫/浮选图像分割设计。

参考实现：
- FrothModel (Nestert/FrothModel): U-Net + MobileNetV3 encoder, IoU~0.69-0.75
- GC-FSegNet: Nested U-Net + GCNet, mIoU=0.8945 (更复杂)
- 本实现：纯 U-Net (无预训练encoder依赖)，轻量可控，适合工业部署

关键设计决策：
1. 使用 DoubleConv (Conv-BN-ReLU×2) 作为基础块 —— 经典且稳定
2. 4 级编码器-解码器 (输入 256→128→64→32→64→128→256)
3. 跳跃连接 (skip connections) 保留高分辨率边界信息
4. Dice + BCE 联合损失 —— 解决类别不平衡(前景<<背景)
5. 支持任意输入尺寸（通过自适应池化/插值）
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    """双卷积块：Conv3x3 → BN → ReLU → Conv3x3 → BN → ReLU"""

    def __init__(self, in_channels: int, out_channels: int, mid_channels: int | None = None):
        super().__init__()
        if mid_channels is None:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.double_conv(x)


class Down(nn.Module):
    """下采样：MaxPool 2×2 → DoubleConv"""

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.maxpool_conv(x)


class Up(nn.Module):
    """上采样：Upsample 2× → Concat(skip) → DoubleConv"""

    def __init__(self, in_channels: int, out_channels: int, bilinear: bool = True):
        super().__init__()
        if bilinear:
            self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
            self.conv = DoubleConv(in_channels, out_channels, in_channels // 2)
        else:
            self.up = nn.ConvTranspose2d(in_channels, in_channels // 2, kernel_size=2, stride=2)
            self.conv = DoubleConv(in_channels, out_channels)

    def forward(self, x1: torch.Tensor, x2: torch.Tensor) -> torch.Tensor:
        """x1: 来自解码器(低分辨率), x2: 来自编码器的跳跃连接(高分辨率)"""
        x1 = self.up(x1)
        # 处理尺寸不匹配（当输入不是 2 的幂次时）
        diffY = x2.size()[2] - x1.size()[2]
        diffX = x2.size()[3] - x1.size()[3]
        x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2, diffY // 2, diffY - diffY //2])
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class UNetFoam(nn.Module):
    """U-Net 泡沫分割网络。

    架构：
        Encoder: 4级下采样 (64→128→256→512 channels)
        Bottleneck: 1024 channels
        Decoder: 4级上采样 (512→256→128→64 channels)
        Output: 1 channel (sigmoid → 前景概率图)

    输入: (B, 3, H, W) RGB 图像
    输出: (B, 1, H, W) 前景掩码概率
    """

    def __init__(self, n_channels: int = 3, n_classes: int = 1, bilinear: bool = True):
        super().__init__()
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.bilinear = bilinear

        # Encoder
        self.inc = DoubleConv(n_channels, 64)
        self.down1 = Down(64, 128)
        self.down2 = Down(128, 256)
        self.down3 = Down(256, 512)

        factor = 2 if bilinear else 1
        self.down4 = Down(512, 1024 // factor)

        # Decoder
        self.up1 = Up(1024, 512 // factor, bilinear)
        self.up2 = Up(512, 256 // factor, bilinear)
        self.up3 = Up(256, 128 // factor, bilinear)
        self.up4 = Up(128, 64, bilinear)

        # Output head
        self.outc = nn.Conv2d(64, n_classes, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Encoder with skip connections
        x1 = self.inc(x)       # 64ch
        x2 = self.down1(x1)    # 128ch
        x3 = self.down2(x2)    # 256ch
        x4 = self.down3(x3)    # 512ch
        x5 = self.down4(x4)    # 1024ch (bottleneck)

        # Decoder with skip connections
        x = self.up1(x5, x4)   # → 512ch
        x = self.up2(x, x3)    # → 256ch
        x = self.up3(x, x2)    # → 128ch
        x = self.up4(x, x1)    # → 64ch

        logits = self.outc(x)   # → 1ch (logits)
        return logits

    @property
    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ============================================================
# Loss Functions
# ============================================================

class DiceBCELoss(nn.Module):
    """Dice Loss + Binary Cross Entropy 联合损失。

    Dice Loss 对前景/背景不平衡鲁棒（泡沫图像中前景通常 <50%）。
    BCE 提供稳定的梯度信号。
    组合使用兼顾边界精度和整体收敛稳定性。

    References:
        - Milletari et al., "V-Net: Fully Convolutional Neural Networks for Volumetric Medical Image Segmentation", 2016
    """

    def __init__(self, dice_weight: float = 0.5, bce_weight: float = 0.5, smooth: float = 1e-6):
        super().__init__()
        self.dice_weight = dice_weight
        self.bce_weight = bce_weight
        self.smooth = smooth
        self.bce = nn.BCEWithLogitsLoss()

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        # BCE
        bce_loss = self.bce(pred, target)

        # Dice
        pred_sigmoid = torch.sigmoid(pred)
        pred_flat = pred_sigmoid.reshape(-1)
        target_flat = target.reshape(-1)

        intersection = (pred_flat * target_flat).sum()
        dice_score = (2. * intersection + self.smooth) / (
            pred_flat.sum() + target_flat.sum() + self.smooth
        )
        dice_loss = 1.0 - dice_score

        return self.dice_weight * dice_loss + self.bce_weight * bce_loss


class FocalLoss(nn.Module):
    """Focal Loss —— 关注难分类样本。

    对容易被正确分类的样本降低权重，聚焦于：
    - 边界像素（模糊区域）
    - 小气泡（像素数少）
    """

    def __init__(self, alpha: float = 0.25, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        bce = F.binary_cross_entropy_with_logits(pred, target, reduction="none")
        p_t = torch.exp(-bce)
        focal = self.alpha * (1 - p_t) ** self.gamma * bce
        return focal.mean()


# ============================================================
# Metrics
# ============================================================

def compute_iou(pred: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> float:
    """计算 IoU (Jaccard Index)。

    Args:
        pred: 模型输出 logits (B, 1, H, W)
        target: ground truth (B, 1, H, W), 值为 0 或 1
        threshold: sigmoid 阈值

    Returns:
        IoU 值 [0, 1]
    """
    pred_bin = (torch.sigmoid(pred) > threshold).float()
    intersection = (pred_bin * target).sum()
    union = pred_bin.sum() + target.sum() - intersection
    if union == 0:
        return 1.0  # both empty = perfect match
    return (intersection / union).item()


def compute_dice(pred: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> float:
    """计算 Dice Coefficient (F1 for segmentation)。"""
    pred_bin = (torch.sigmoid(pred) > threshold).float()
    intersection = (pred_bin * target).sum()
    total = pred_bin.sum() + target.sum()
    if total == 0:
        return 1.0
    return (2.0 * intersection / total).item()


def compute_boundary_iou(
    pred: torch.Tensor,
    target: torch.Tensor,
    threshold: float = 0.5,
    dilation_radius: int = 3,
) -> float:
    """计算 Boundary IoU —— 只评估边界区域的精度。

    更能反映视觉上的"边缘贴合度"，对泡沫分割尤其重要。
    """
    from scipy.ndimage import binary_dilation

    pred_bin = (torch.sigmoid(pred) > threshold).cpu().numpy().astype(np.uint8)[0, 0]
    target_bin = target.cpu().numpy().astype(np.uint8)[0, 0]

    # 提取边界（膨胀 - 原始）
    kernel = np.ones((dilation_radius * 2 + 1, dilation_radius * 2 + 1), dtype=np.uint8)
    pred_boundary = binary_dilation(pred_bin, kernel) & (~pred_bin.astype(bool))
    target_boundary = binary_dilation(target_bin, kernel) & (~target_bin.astype(bool))

    intersection = (pred_boundary & target_boundary).sum()
    union = pred_boundary.sum() + target_boundary.sum() - intersection
    if union == 0:
        return 1.0
    return float(intersection / union)


if __name__ == "__main__":
    # Quick smoke test
    import numpy as np

    print("=== U-Net Foam Model Smoke Test ===")
    model = UNetFoam(n_channels=3, n_classes=1)
    print(f"Model parameters: {model.n_params:,}")

    x = torch.randn(1, 3, 256, 256)
    y = model(x)
    print(f"Input shape:  {x.shape}")
    print(f"Output shape: {y.shape}")

    # Test loss
    target = torch.randint(0, 2, (1, 1, 256, 256)).float()
    loss_fn = DiceBCELoss()
    loss = loss_fn(y, target)
    print(f"DiceBCE Loss: {loss.item():.4f}")

    # Test metrics
    iou = compute_iou(y, target)
    dice = compute_dice(y, target)
    print(f"IoU:  {iou:.4f}")
    print(f"Dice: {dice:.4f}")
    print("All tests passed!")
