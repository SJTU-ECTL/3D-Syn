#!/usr/bin/env python3
"""Train the GraphSAGE utilization model from an offline generated dataset.

The command consumes ``05_dataset/<technology>/splits.json`` rather than
creating a new random split.  This keeps the fixed seed and source-grouping
contract established by ``data_generation/05_build_dataset.py``.

Example:
    python area_model/train.py \
      --technology asap7 \
      --data-root path/to/work \
      --resources-dir path/to/work/00_resources \
      --output-dir path/to/checkpoints/asap7 \
      --epochs 300 --batch-size 32 --seed 42

The output directory contains ``best_model.pt``, ``last_model.pt``,
``norm_stats.pt``, ``config.json``, ``history.json``, ``metrics.json``, a copy
of the split contract and ``manifest.json``.  The manifest hashes every file
required by ``predict_util.py`` so a newly trained checkpoint can be validated
by the same formal inference interface as a released checkpoint.
"""

import argparse
import hashlib
import json
import math
import random
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional
import torch.optim as optim
from torch.utils.data import DataLoader

if __package__:
    from .config import (
        BW_VEC_DIM,
        CELL_TYPE_COL,
        FEATURE_SCHEMA_VERSION,
        GRAPH_FEAT_DIM,
        GRAPH_FEAT_KEYS,
        MAX_BITWIDTH,
        MODEL_FORMAT_VERSION,
        NODE_FEAT_DIM,
        NODE_FEAT_KEYS,
        TRAINING_TECHNOLOGIES,
    )
    from .dataset import ModuleGraphDataset, collate_fn, compute_norm_stats
    from .model import UtilizationGNN, compute_loss
else:
    from config import (
        BW_VEC_DIM,
        CELL_TYPE_COL,
        FEATURE_SCHEMA_VERSION,
        GRAPH_FEAT_DIM,
        GRAPH_FEAT_KEYS,
        MAX_BITWIDTH,
        MODEL_FORMAT_VERSION,
        NODE_FEAT_DIM,
        NODE_FEAT_KEYS,
        TRAINING_TECHNOLOGIES,
    )
    from dataset import ModuleGraphDataset, collate_fn, compute_norm_stats
    from model import UtilizationGNN, compute_loss


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the area utilization GNN.")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        required=True, help="dataset process")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="root containing 05_dataset")
    parser.add_argument("--resources-dir", type=Path, required=True,
                        help="training cell vocabulary and port schema")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="checkpoint output directory")
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--n-layers", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--lambda-degree", type=float, default=0.1)
    parser.add_argument("--lambda-area", type=float, default=0.1)
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=0,
                        help="DataLoader workers (default: 0)")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--no-aux-loss", action="store_true",
                        help="train only with utilization MSE")
    parser.add_argument("--force", action="store_true",
                        help="allow replacement of checkpoint files in output-dir")
    return parser.parse_args()


def set_reproducible_seed(seed: int) -> None:
    """Seed Python, NumPy and PyTorch and request deterministic kernels."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def select_device(choice: str) -> torch.device:
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("--device cuda was requested but CUDA is unavailable")
        return torch.device("cuda")
    if choice == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_split_paths(data_root: Path, technology: str) -> tuple[Path, dict]:
    dataset_root = data_root / "05_dataset" / technology
    split_path = dataset_root / "splits.json"
    if not split_path.is_file():
        raise FileNotFoundError(f"split manifest not found: {split_path}")
    manifest = json.loads(split_path.read_text(encoding="utf-8"))
    if manifest.get("technology") != technology:
        raise ValueError("split manifest technology does not match --technology")
    if manifest.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("split manifest feature schema is incompatible")
    splits = manifest.get("splits", {})
    origins = manifest.get("origins", {})
    required = ("train", "validation", "test")
    if any(not splits.get(name) for name in required):
        raise ValueError("train, validation and test splits must all be non-empty")
    origin_sets = [set(origins.get(name, [])) for name in required]
    if any(origin_sets[left] & origin_sets[right]
           for left in range(3) for right in range(left + 1, 3)):
        raise ValueError("source-wrapper leakage detected between dataset splits")
    paths = {
        name: [(dataset_root / relative).resolve() for relative in splits[name]]
        for name in required
    }
    missing = [path for values in paths.values() for path in values if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"split references missing graph: {missing[0]}")
    return split_path, paths


def move_batch_to_device(batch: dict, device: torch.device) -> dict:
    return {key: value.to(device) if isinstance(value, torch.Tensor) else value
            for key, value in batch.items()}


class IndexedDataset(torch.utils.data.Dataset):
    """Return local indices with normalized samples for raw-feature lookup."""

    def __init__(self, dataset: ModuleGraphDataset):
        self.dataset = dataset

    def __len__(self) -> int:
        return len(self.dataset)

    def __getitem__(self, index: int):
        return index, self.dataset[index]


def make_collate_with_raw(raw_dataset: ModuleGraphDataset):
    """Add unnormalized node features used by the two auxiliary losses."""
    def collate_with_raw(indexed_samples):
        indices = [item[0] for item in indexed_samples]
        samples = [item[1] for item in indexed_samples]
        raw_x = [torch.load(raw_dataset.paths[index], weights_only=True)["x"].float()
                 for index in indices]
        result = collate_fn(samples)
        result["x_raw"] = torch.cat(raw_x, dim=0)
        return result
    return collate_with_raw


def train_one_epoch(model: UtilizationGNN, loader: DataLoader,
                    optimizer: torch.optim.Optimizer, device: torch.device,
                    lambda_degree: float, lambda_area: float,
                    use_aux_loss: bool) -> dict:
    model.train()
    totals = {"total": 0.0, "util": 0.0, "degree": 0.0, "area": 0.0}
    for batch in loader:
        batch = move_batch_to_device(batch, device)
        predictions = model(
            x=batch["x"],
            edge_index=batch["edge_index"],
            graph_feat=batch["graph_feat"],
            bitwidth_vec=batch["bitwidth_vec"],
            batch_index=batch["batch_index"],
        )
        if use_aux_loss:
            losses = compute_loss(
                predictions, batch["y"], batch["x_raw"], batch["batch_index"],
                lambda_degree=lambda_degree, lambda_area=lambda_area,
            )
        else:
            utilization_loss = functional.mse_loss(
                predictions["util_pred"], batch["y"]
            )
            zero = utilization_loss.new_tensor(0.0)
            losses = {"total": utilization_loss, "util": utilization_loss,
                      "degree": zero, "area": zero}
        optimizer.zero_grad()
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        for key in totals:
            totals[key] += losses[key].item()
    return {key: value / len(loader) for key, value in totals.items()}


@torch.no_grad()
def evaluate(model: UtilizationGNN, loader: DataLoader,
             device: torch.device) -> dict:
    model.eval()
    predictions, targets = [], []
    for batch in loader:
        batch = move_batch_to_device(batch, device)
        output = model(
            x=batch["x"],
            edge_index=batch["edge_index"],
            graph_feat=batch["graph_feat"],
            bitwidth_vec=batch["bitwidth_vec"],
            batch_index=batch["batch_index"],
        )
        predictions.append(output["util_pred"].cpu())
        targets.append(batch["y"].cpu())
    predicted = torch.cat(predictions)
    expected = torch.cat(targets)
    errors = (predicted - expected).abs()
    return {
        "mse": functional.mse_loss(predicted, expected).item(),
        "mae": errors.mean().item(),
        "max_absolute_error": errors.max().item(),
        "predictions": predicted,
        "targets": expected,
    }


def file_metadata(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def prepare_output(output_dir: Path, force: bool) -> None:
    """Reject accidental overwrite while allowing explicit checkpoint refresh."""
    checkpoint_names = (
        "best_model.pt", "last_model.pt", "norm_stats.pt", "config.json",
        "history.json", "metrics.json", "manifest.json", "splits.json",
    )
    existing = [output_dir / name for name in checkpoint_names
                if (output_dir / name).exists()]
    if existing and not force:
        raise FileExistsError(
            f"checkpoint output already exists; pass --force to replace: {existing[0]}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()
    positive_values = (args.epochs, args.batch_size, args.hidden_dim,
                       args.n_layers, args.patience)
    if any(value <= 0 for value in positive_values) or args.workers < 0:
        raise ValueError("epochs, batch/model sizes and patience must be positive")
    if args.lr <= 0 or args.weight_decay < 0 or not 0 <= args.dropout < 1:
        raise ValueError("invalid optimizer or dropout setting")
    data_root = args.data_root.expanduser().resolve()
    resources_dir = args.resources_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    vocabulary = resources_dir / "cell_type_vocab.json"
    port_schema = resources_dir / "port_schema.json"
    if not vocabulary.is_file() or not port_schema.is_file():
        raise FileNotFoundError(
            "resources-dir must contain cell_type_vocab.json and port_schema.json"
        )
    prepare_output(output_dir, args.force)
    set_reproducible_seed(args.seed)
    device = select_device(args.device)
    split_path, split_paths = load_split_paths(data_root, args.technology)
    split_manifest = json.loads(split_path.read_text(encoding="utf-8"))
    if split_manifest.get("seed") != args.seed:
        raise ValueError(
            f"training seed {args.seed} differs from split seed "
            f"{split_manifest.get('seed')}"
        )

    raw_train = ModuleGraphDataset(
        platform=args.technology, data_root=data_root,
        paths=split_paths["train"], norm_stats=None,
    )
    norm_stats = compute_norm_stats(raw_train)
    if any(not torch.isfinite(value).all() for value in norm_stats.values()):
        raise ValueError("normalization statistics contain NaN or Inf")
    torch.save(norm_stats, output_dir / "norm_stats.pt")

    train_dataset = ModuleGraphDataset(
        platform=args.technology, data_root=data_root,
        paths=split_paths["train"], norm_stats=norm_stats,
    )
    validation_dataset = ModuleGraphDataset(
        platform=args.technology, data_root=data_root,
        paths=split_paths["validation"], norm_stats=norm_stats,
    )
    test_dataset = ModuleGraphDataset(
        platform=args.technology, data_root=data_root,
        paths=split_paths["test"], norm_stats=norm_stats,
    )
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(
        IndexedDataset(train_dataset), batch_size=args.batch_size, shuffle=True,
        generator=generator, collate_fn=make_collate_with_raw(raw_train),
        num_workers=args.workers,
    )
    validation_loader = DataLoader(
        validation_dataset, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate_fn, num_workers=args.workers,
    )
    test_loader = DataLoader(
        test_dataset, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate_fn, num_workers=args.workers,
    )

    model = UtilizationGNN(
        node_feat_dim=NODE_FEAT_DIM,
        graph_feat_dim=GRAPH_FEAT_DIM,
        bw_vec_dim=BW_VEC_DIM,
        hidden_dim=args.hidden_dim,
        n_layers=args.n_layers,
        mlp_dims=(128, 64),
        dropout=args.dropout,
    ).to(device)
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    optimizer = optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5,
        patience=max(1, args.patience // 2), min_lr=1e-6,
    )
    config = {
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "technology": args.technology,
        "node_feat_dim": NODE_FEAT_DIM,
        "graph_feat_dim": GRAPH_FEAT_DIM,
        "bw_vec_dim": BW_VEC_DIM,
        "mlp_dims": [128, 64],
        "epochs": args.epochs,
        "lr": args.lr,
        "weight_decay": args.weight_decay,
        "batch_size": args.batch_size,
        "hidden_dim": args.hidden_dim,
        "n_layers": args.n_layers,
        "dropout": args.dropout,
        "lambda_degree": args.lambda_degree,
        "lambda_area": args.lambda_area,
        "patience": args.patience,
        "seed": args.seed,
        "no_aux_loss": args.no_aux_loss,
        "n_params": parameter_count,
        "n_train": len(train_dataset),
        "n_val": len(validation_dataset),
        "n_test": len(test_dataset),
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copyfile(split_path, output_dir / "splits.json")
    print(f"[Device] {device}")
    print(f"[Data] train={len(train_dataset)} validation={len(validation_dataset)} "
          f"test={len(test_dataset)}")
    print(f"[Model] parameters={parameter_count}")

    history = []
    best_validation_loss = math.inf
    stale_epochs = 0
    for epoch in range(1, args.epochs + 1):
        started = time.monotonic()
        train_losses = train_one_epoch(
            model, train_loader, optimizer, device,
            args.lambda_degree, args.lambda_area, not args.no_aux_loss,
        )
        validation = evaluate(model, validation_loader, device)
        scheduler.step(validation["mse"])
        record = {
            "epoch": epoch,
            "train_total": train_losses["total"],
            "train_util": train_losses["util"],
            "train_degree": train_losses["degree"],
            "train_area": train_losses["area"],
            "validation_mse": validation["mse"],
            "validation_mae": validation["mae"],
            "lr": optimizer.param_groups[0]["lr"],
            "elapsed_seconds": time.monotonic() - started,
        }
        if any(not math.isfinite(value) for key, value in record.items()
               if key != "epoch"):
            raise RuntimeError(f"non-finite training metric at epoch {epoch}")
        history.append(record)
        print(f"Epoch {epoch:4d}/{args.epochs} train={record['train_total']:.6f} "
              f"val_mse={record['validation_mse']:.6f} "
              f"val_mae={record['validation_mae']:.6f}")
        if validation["mse"] < best_validation_loss:
            best_validation_loss = validation["mse"]
            stale_epochs = 0
            torch.save(model.state_dict(), output_dir / "best_model.pt")
        else:
            stale_epochs += 1
        if stale_epochs >= args.patience:
            print(f"[EarlyStopping] no validation improvement for {args.patience} epochs")
            break

    torch.save(model.state_dict(), output_dir / "last_model.pt")
    (output_dir / "history.json").write_text(
        json.dumps(history, indent=2) + "\n", encoding="utf-8"
    )
    model.load_state_dict(torch.load(
        output_dir / "best_model.pt", weights_only=True, map_location=device
    ))
    test = evaluate(model, test_loader, device)
    metrics = {
        "best_validation_mse": best_validation_loss,
        "test_mse": test["mse"],
        "test_mae": test["mae"],
        "max_absolute_error": test["max_absolute_error"],
        "epochs_completed": len(history),
        "first_train_loss": history[0]["train_total"],
        "last_train_loss": history[-1]["train_total"],
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )

    checkpoint_files = {
        name: file_metadata(output_dir / name)
        for name in ("best_model.pt", "norm_stats.pt", "config.json")
    }
    resource_files = {
        "cell_type_vocab.json": file_metadata(vocabulary),
        "port_schema.json": file_metadata(port_schema),
    }
    tier = TRAINING_TECHNOLOGIES[args.technology]["tier"]
    manifest = {
        "manifest_version": 1,
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "platform": args.technology,
        "technology": args.technology,
        "inference_technology": tier,
        "model_class": "UtilizationGNN",
        "pytorch_min_version": "2.0",
        "features": {
            "node_feat_dim": NODE_FEAT_DIM,
            "graph_feat_dim": GRAPH_FEAT_DIM,
            "bitwidth_vec_dim": BW_VEC_DIM,
            "max_bitwidth": MAX_BITWIDTH,
            "cell_type_column": CELL_TYPE_COL,
            "node_feature_keys": list(NODE_FEAT_KEYS),
            "graph_feature_keys": list(GRAPH_FEAT_KEYS),
        },
        "checkpoint_files": checkpoint_files,
        "resources": resource_files,
        "training_summary": {
            "seed": args.seed,
            "train_samples": len(train_dataset),
            "validation_samples": len(validation_dataset),
            "test_samples": len(test_dataset),
            "split_manifest_sha256": file_metadata(output_dir / "splits.json")["sha256"],
        },
        "evaluation": {
            "validation_mse": best_validation_loss,
            "test_mse": test["mse"],
            "test_mae": test["mae"],
            "max_absolute_error": test["max_absolute_error"],
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[Done] checkpoint -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
