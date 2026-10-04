#!/usr/bin/env python3
"""Load graph samples and apply the normalization used by the area model.

Each sample contains node, edge, graph and bit-width tensors together with its
utilization label and wrapper name.
"""

import random
from pathlib import Path
from typing import List, Tuple, Optional

import torch
from torch.utils.data import Dataset

if __package__:
    from .config import DATA_DIR
else:
    from config import DATA_DIR

PLATFORM = "asap7"
GROUPS   = ("ff_fa", "arith_same", "arith_rand")

NODE_FEAT_DIM  = 9
GRAPH_FEAT_DIM = 17
BW_VEC_DIM     = 5
MAX_BITWIDTH   = 32.0

CELL_TYPE_COL = 8  # last column of x, skip normalization


class ModuleGraphDataset(Dataset):
    """
    Loads all graph .pt files for a given platform across all groups.

    Args:
        platform:    technology library name, e.g. "asap7"
        groups:      which data groups to include
        indices:     if given, only load this subset (used for train/val/test split)
        norm_stats:  normalization statistics dict (see compute_norm_stats).
                     If None, no normalization is applied.
    """

    def __init__(
        self,
        platform:   str              = PLATFORM,
        groups:     Tuple[str, ...]  = GROUPS,
        indices:    Optional[List[int]] = None,
        norm_stats: Optional[dict]   = None,
        data_root:  Path             = DATA_DIR,
        paths:      Optional[List[Path]] = None,
    ):
        super().__init__()

        self.norm_stats = norm_stats

        dataset_root = Path(data_root) / "05_dataset" / platform
        all_paths = []
        if paths is not None:
            all_paths = [Path(path) for path in paths]
        else:
            for group in groups:
                graphs_dir = dataset_root / group / "graphs"
                if not graphs_dir.exists():
                    raise FileNotFoundError(
                        f"Graphs directory not found: {graphs_dir}\n"
                        f"Run data_generation/05_build_dataset.py {group} first."
                    )
                all_paths.extend(sorted(graphs_dir.glob("*.pt")))

        self.all_paths = all_paths

        if indices is not None:
            self.paths = [all_paths[i] for i in indices]
        else:
            self.paths = all_paths

        print(f"[Dataset] platform={platform}, groups={groups}")
        print(f"[Dataset] total files={len(all_paths)}, using={len(self.paths)}")

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int) -> dict:
        data = torch.load(self.paths[idx], weights_only=True)

        x            = data["x"].float()
        edge_index   = data["edge_index"].long()
        graph_feat   = data["graph_feat"].float()
        bitwidth_vec = data["bitwidth_vec"].float()
        y            = data["y"].float()
        name         = data["name"]

        # divide by max bitwidth -> [0, 1]
        bitwidth_vec = bitwidth_vec / MAX_BITWIDTH

        if self.norm_stats is not None:
            x_mean = self.norm_stats["x_mean"]
            x_std  = self.norm_stats["x_std"]
            x = x.clone()
            for col in range(NODE_FEAT_DIM):
                if col == CELL_TYPE_COL:
                    continue
                std = x_std[col].item()
                if std < 1e-8:
                    x[:, col] = x[:, col] - x_mean[col]
                else:
                    x[:, col] = (x[:, col] - x_mean[col]) / std

            gf_mean = self.norm_stats["gf_mean"]
            gf_std  = self.norm_stats["gf_std"]
            graph_feat = graph_feat.clone()
            for i in range(GRAPH_FEAT_DIM):
                std = gf_std[i].item()
                if std < 1e-8:
                    graph_feat[i] = graph_feat[i] - gf_mean[i]
                else:
                    graph_feat[i] = (graph_feat[i] - gf_mean[i]) / std

        return {
            "x":            x,
            "edge_index":   edge_index,
            "graph_feat":   graph_feat,
            "bitwidth_vec": bitwidth_vec,
            "y":            y,
            "name":         name,
        }


def compute_norm_stats(dataset: ModuleGraphDataset) -> dict:
    """
    Compute per-column mean and std for x and graph_feat over the given dataset.
    Should be called on the training split only to avoid data leakage.
    """
    if not len(dataset):
        raise ValueError("cannot compute normalization statistics from an empty dataset")
    all_x_rows  = []
    all_gf_rows = []

    print(f"[NormStats] computing statistics over {len(dataset)} training samples...")

    for i in range(len(dataset)):
        raw = torch.load(dataset.paths[i], weights_only=True)
        all_x_rows.append(raw["x"].float())
        all_gf_rows.append(raw["graph_feat"].float().unsqueeze(0))

    all_x  = torch.cat(all_x_rows,  dim=0)
    all_gf = torch.cat(all_gf_rows, dim=0)

    stats = {
        "x_mean":  all_x.mean(dim=0),
        "x_std":   all_x.std(dim=0),
        "gf_mean": all_gf.mean(dim=0),
        "gf_std":  all_gf.std(dim=0),
    }

    print(f"[NormStats] done. total nodes seen: {all_x.shape[0]}")
    return stats


def split_indices(
    n_total:    int,
    train_frac: float = 0.7,
    val_frac:   float = 0.15,
    seed:       int   = 42,
) -> Tuple[List[int], List[int], List[int]]:
    """
    Randomly split n_total indices into train / val / test.
    test_frac = 1.0 - train_frac - val_frac.
    """
    assert train_frac + val_frac < 1.0, "train + val fractions must be < 1.0"

    g = torch.Generator()
    g.manual_seed(seed)
    perm = torch.randperm(n_total, generator=g).tolist()

    n_train = int(n_total * train_frac)
    n_val   = int(n_total * val_frac)

    train_idx = perm[:n_train]
    val_idx   = perm[n_train : n_train + n_val]
    test_idx  = perm[n_train + n_val :]

    print(f"[Split] total={n_total} -> train={len(train_idx)}, "
          f"val={len(val_idx)}, test={len(test_idx)}")

    return train_idx, val_idx, test_idx


def split_indices_by_origin(
    paths: List[Path],
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    seed: int = 42,
) -> Tuple[List[int], List[int], List[int]]:
    """Split samples by wrapper identity so one source cannot cross splits."""
    if not 0 < train_frac < 1 or not 0 <= val_frac < 1 \
            or train_frac + val_frac >= 1:
        raise ValueError("train/validation fractions must be non-negative and sum below 1")
    origins = {}
    for index, path in enumerate(paths):
        origins.setdefault(Path(path).stem, []).append(index)
    keys = sorted(origins)
    random.Random(seed).shuffle(keys)
    n_total = len(keys)
    n_train = int(n_total * train_frac)
    n_val = int(n_total * val_frac)
    if n_total >= 3:
        n_train = min(max(n_train, 1), n_total - 2)
        n_val = min(max(n_val, 1), n_total - n_train - 1)
    train_keys = set(keys[:n_train])
    val_keys = set(keys[n_train:n_train + n_val])
    test_keys = set(keys[n_train + n_val:])
    return (
        [index for key in train_keys for index in origins[key]],
        [index for key in val_keys for index in origins[key]],
        [index for key in test_keys for index in origins[key]],
    )


def collate_fn(batch: List[dict]) -> dict:
    """
    Custom collate for variable-size graphs using the block-diagonal convention.
    Node indices in edge_index are shifted per graph so they index into the
    concatenated x correctly. A batch_index tensor maps each node to its graph.
    """
    xs, edge_indices, graph_feats, bw_vecs, ys, names, batch_index = \
        [], [], [], [], [], [], []

    node_offset = 0
    for graph_idx, sample in enumerate(batch):
        x          = sample["x"]
        edge_index = sample["edge_index"]
        n_nodes    = x.shape[0]

        xs.append(x)
        edge_indices.append(edge_index + node_offset)
        graph_feats.append(sample["graph_feat"])
        bw_vecs.append(sample["bitwidth_vec"])
        ys.append(sample["y"])
        names.append(sample["name"])
        batch_index.append(torch.full((n_nodes,), graph_idx, dtype=torch.long))

        node_offset += n_nodes

    return {
        "x":            torch.cat(xs,           dim=0),
        "edge_index":   torch.cat(edge_indices, dim=1),
        "graph_feat":   torch.stack(graph_feats, dim=0),
        "bitwidth_vec": torch.stack(bw_vecs,     dim=0),
        "y":            torch.stack(ys,           dim=0),
        "batch_index":  torch.cat(batch_index,   dim=0),
        "names":        names,
    }


if __name__ == "__main__":
    from torch.utils.data import DataLoader

    full_ds = ModuleGraphDataset()
    train_idx, val_idx, test_idx = split_indices(len(full_ds))

    train_ds_raw = ModuleGraphDataset(indices=train_idx, norm_stats=None)
    stats = compute_norm_stats(train_ds_raw)

    train_ds = ModuleGraphDataset(indices=train_idx, norm_stats=stats)
    val_ds   = ModuleGraphDataset(indices=val_idx,   norm_stats=stats)
    test_ds  = ModuleGraphDataset(indices=test_idx,  norm_stats=stats)

    train_loader = DataLoader(train_ds, batch_size=32, shuffle=True,
                              collate_fn=collate_fn)

    batch = next(iter(train_loader))
    print("\n[Sanity check] one batch:")
    print(f"  x:            {batch['x'].shape}")
    print(f"  edge_index:   {batch['edge_index'].shape}")
    print(f"  graph_feat:   {batch['graph_feat'].shape}")
    print(f"  bitwidth_vec: {batch['bitwidth_vec'].shape}")
    print(f"  y:            {batch['y'].shape}")
    print(f"  batch_index:  {batch['batch_index'].shape}")
    print(f"  y range:      [{batch['y'].min():.3f}, {batch['y'].max():.3f}]")
    print(f"  names[:3]:    {batch['names'][:3]}")
