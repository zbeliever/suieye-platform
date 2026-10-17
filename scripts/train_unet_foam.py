"""U-Net 泡沫分割模型训练脚本。

用法：
  # 用合成数据快速验证
  python scripts/train_unet_foam.py --data synthetic --n_train 200 --epochs 50

  # 用真实数据训练
  python scripts/train_unet_foam.py --data /path/to/real/dataset --epochs 100

  # 从预训练权重继续训练
  python scripts/train_unet_foam.py --resume backend/models/weights/unet_foam_best.pth

输出：
  - best model: backend/models/weights/unet_foam_best.pth
  - last model: backend/models/weights/unet_foam_last.pth
  - training log: backend/models/weights/training_log.csv
  - loss curves: backend/models/weights/loss_curves.png
"""

import os
import sys
import argparse
import json
import time
from pathlib import Path

import numpy as np
import cv2
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.unet_foam import UNetFoam, DiceBCELoss, compute_iou, compute_dice


# ============================================================
# Dataset
# ============================================================

class FoamDataset(Dataset):
    """泡沫分割数据集。

    支持两种格式：
    1. 标准目录结构: root/images/*.png + root/masks/*.png
    2. 合成数据: 自动生成（--data synthetic）
    """

    def __init__(self, root: str, transform=None, img_size: tuple = (256, 256)):
        self.root = root
        self.transform = transform
        self.img_size = img_size

        self.image_dir = os.path.join(root, "images")
        self.mask_dir = os.path.join(root, "masks")

        if not os.path.isdir(self.image_dir):
            raise FileNotFoundError(f"Image directory not found: {self.image_dir}")

        # Find all images (support png, jpg, jpeg)
        self.images = sorted([
            f for f in os.listdir(self.image_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ])

        if len(self.images) == 0:
            raise ValueError(f"No images found in {self.image_dir}")

        print(f"[Dataset] Loaded {len(self.images)} images from {root}")

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, idx: int) -> tuple:
        img_name = self.images[idx]

        img_path = os.path.join(self.image_dir, img_name)
        mask_path = os.path.join(self.mask_dir, img_name)

        # Load image (BGR) → RGB
        image = cv2.imread(img_path)
        if image is None:
            raise FileNotFoundError(f"Cannot read image: {img_path}")
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # Load mask (grayscale)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise FileNotFoundError(f"Cannot read mask: {mask_path}")
        # Binarize: any non-zero → 1
        mask = (mask > 127).astype(np.float32)

        # Resize
        image = cv2.resize(image, (self.img_size[1], self.img_size[0]), interpolation=cv2.INTER_LINEAR)
        mask = cv2.resize(mask, (self.img_size[1], self.img_size[0]), interpolation=cv2.INTER_NEAREST)

        # To tensor: image [0,1], mask [0,1]
        image = torch.from_numpy(image.transpose(2, 0, 1)).float() / 255.0
        mask = torch.from_numpy(mask).unsqueeze(0).float()

        # Normalize with ImageNet stats
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        image = (image - mean) / std

        return image, mask


class SyntheticFoamDataset(Dataset):
    """在线生成合成泡沫数据（无需磁盘存储）。"""

    def __init__(self, n_samples: int, img_size: tuple = (256, 256), seed: int = 42):
        self.n_samples = n_samples
        self.img_size = img_size
        self.rng = np.random.RandomState(seed)

        # Pre-generate seeds for reproducibility
        self.seeds = self.rng.randint(0, 2**31, size=n_samples)

    def __len__(self) -> int:
        return self.n_samples

    def __getitem__(self, idx: int) -> tuple:
        from backend.data.synthetic_foam_generator import generate_synthetic_foam_image

        image_bgr, mask = generate_synthetic_foam_image(
            img_size=self.img_size,
            n_bubbles=(10, 45),
            bubble_size_range=(6, 55),
            gap_darkness=float(self.rng.uniform(0.2, 0.6)),
            blur_level=float(self.rng.uniform(0.5, 2.5)),
            seed=int(self.seeds[idx]),
        )

        # BGR → RGB
        image = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        mask_float = (mask > 127).astype(np.float32)

        # Resize (should already be correct, but ensure)
        image = cv2.resize(image, (self.img_size[1], self.img_size[0]), interpolation=cv2.INTER_LINEAR)
        mask_float = cv2.resize(mask_float, (self.img_size[1], self.img_size[0]), interpolation=cv2.INTER_NEAREST)

        # To tensor
        image_t = torch.from_numpy(image.transpose(2, 0, 1)).float() / 255.0
        mask_t = torch.from_numpy(mask_float).unsqueeze(0).float()

        # Normalize
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        image_t = (image_t - mean) / std

        return image_t, mask_t


# ============================================================
# Training Loop
# ============================================================

def train_one_epoch(model, loader, optimizer, criterion, device, epoch):
    """Train one epoch, return average loss and metrics."""
    model.train()
    total_loss = 0
    total_iou = 0
    total_dice = 0
    n_batches = 0

    for images, masks in loader:
        images = images.to(device)
        masks = masks.to(device)

        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, masks)
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        total_iou += compute_iou(outputs.detach(), masks)
        total_dice += compute_dice(outputs.detach(), masks)
        n_batches += 1

    return {
        "loss": total_loss / n_batches,
        "iou": total_iou / n_batches,
        "dice": total_dice / n_batches,
    }


@torch.no_grad()
def validate(model, loader, criterion, device):
    """Validate, return metrics."""
    model.eval()
    total_loss = 0
    total_iou = 0
    total_dice = 0
    n_batches = 0

    for images, masks in loader:
        images = images.to(device)
        masks = masks.to(device)

        outputs = model(images)
        loss = criterion(outputs, masks)

        total_loss += loss.item()
        total_iou += compute_iou(outputs, masks)
        total_dice += compute_dice(outputs, masks)
        n_batches += 1

    return {
        "loss": total_loss / n_batches,
        "iou": total_iou / n_batches,
        "dice": total_dice / n_batches,
    }


def train(args):
    """Main training function."""
    # Device
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    print(f"Using device: {device}")

    # === Data ===
    if args.data.lower() == "synthetic":
        print(f"Generating synthetic dataset on-the-fly...")
        train_dataset = SyntheticFoamDataset(args.n_train, img_size=(args.img_size, args.img_size), seed=42)
        val_dataset = SyntheticFoamDataset(args.n_val, img_size=(args.img_size, args.img_size), seed=123)
    else:
        train_dataset = FoamDataset(os.path.join(args.data, "train"), img_size=(args.img_size, args.img_size))
        val_dataset = FoamDataset(os.path.join(args.data, "val"), img_size=(args.img_size, args.img_size))

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)

    # === Model ===
    model = UNetFoam(n_channels=3, n_classes=1)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model: UNetFoam ({n_params:,} parameters)")

    if args.resume and os.path.exists(args.resume):
        print(f"Resuming from {args.resume}")
        state_dict = torch.load(args.resume, map_location=device, weights_only=True)
        model.load_state_dict(state_dict)

    model.to(device)

    # === Loss & Optimizer ===
    criterion = DiceBCELoss(dice_weight=0.5, bce_weight=0.5)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5
    )

    # === Training ===
    best_val_iou = 0.0
    history = {"train_loss": [], "train_iou": [], "val_loss": [], "val_iou": []}
    weights_dir = Path(PROJECT_ROOT) / "backend" / "models" / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nTraining for {args.epochs} epochs...")
    start_time = time.time()

    for epoch in range(1, args.epochs + 1):
        epoch_start = time.time()

        # Train
        train_metrics = train_one_epoch(model, train_loader, optimizer, criterion, device, epoch)

        # Validate
        val_metrics = validate(model, val_loader, criterion, device)

        # LR scheduler
        scheduler.step(val_metrics["loss"])

        # Log
        history["train_loss"].append(train_metrics["loss"])
        history["train_iou"].append(train_metrics["iou"])
        history["val_loss"].append(val_metrics["loss"])
        history["val_iou"].append(val_metrics["iou"])

        epoch_time = time.time() - epoch_start

        print(
            f"Epoch {epoch:3d}/{args.epochs} | "
            f"Time: {epoch_time:.1f}s | "
            f"Train Loss: {train_metrics['loss']:.4f} IoU: {train_metrics['iou']:.4f} | "
            f"Val Loss: {val_metrics['loss']:.4f} IoU: {val_metrics['iou']:.4f} Dice: {val_metrics['dice']:.4f}"
        )

        # Save best
        if val_metrics["iou"] > best_val_iou:
            best_val_iou = val_metrics["iou"]
            best_path = weights_dir / "unet_foam_best.pth"
            torch.save(model.state_dict(), best_path)
            print(f"  -> New best! Val IoU = {best_val_iou:.4f} (saved to {best_path})")

    # Save last
    last_path = weights_dir / "unet_foam_last.pth"
    torch.save(model.state_dict(), last_path)
    print(f"\nLast model saved to {last_path}")

    # Save training log
    import csv
    log_path = weights_dir / "training_log.csv"
    with open(log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["epoch", "train_loss", "train_iou", "val_loss", "val_iou"])
        for i in range(len(history["train_loss"])):
            writer.writerow([
                i + 1, history["train_loss"][i], history["train_iou"][i],
                history["val_loss"][i], history["val_iou"][i]
            ])
    print(f"Training log saved to {log_path}")

    # Plot loss curves
    _plot_curves(history, weights_dir / "loss_curves.png")

    total_time = time.time() - start_time
    print(f"\nTraining complete in {total_time/60:.1f} min")
    print(f"Best Val IoU: {best_val_iou:.4f}")


def _plot_curves(history, save_path):
    """Plot training/validation loss and IoU curves."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))

        axes[0].plot(history["train_loss"], label="Train Loss", color="#E24B4A")
        axes[0].plot(history["val_loss"], label="Val Loss", color="#378ADD")
        axes[0].set_title("Loss Curve")
        axes[0].set_xlabel("Epoch")
        axes[0].set_ylabel("Loss")
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        axes[1].plot(history["train_iou"], label="Train IoU", color="#639922")
        axes[1].plot(history["val_iou"], label="Val IoU", color="#BA7517")
        axes[1].set_title("IoU Curve")
        axes[1].set_xlabel("Epoch")
        axes[1].set_ylabel("IoU")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(str(save_path), dpi=150)
        plt.close()
        print(f"Loss curves saved to {save_path}")
    except ImportError:
        print("(matplotlib not available, skipping curve plot)")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train U-Net for foam segmentation")
    parser.add_argument("--data", type=str, default="synthetic",
                        help="Data path or 'synthetic' for generated data")
    parser.add_argument("--n-train", type=int, default=200, help="Number of training samples (if synthetic)")
    parser.add_argument("--n-val", type=int, default=50, help="Number of validation samples (if synthetic)")
    parser.add_argument("--img-size", type=int, default=256, help="Image size (square)")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size")
    parser.add_argument("--epochs", type=int, default=50, help="Number of training epochs")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate")
    parser.add_argument("--cpu", action="store_true", help="Force CPU even if CUDA available")
    parser.add_argument("--resume", type=str, default=None, help="Path to checkpoint to resume from")
    args = parser.parse_args()

    train(args)
