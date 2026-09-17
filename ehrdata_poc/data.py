from __future__ import annotations

from pathlib import Path

import ehrdata as ed
import numpy as np
import pandas as pd
from ehrdata import EHRData

LABEL = "In-hospital_death"
ICU_TYPES = {1: "coronary-care", 2: "cardiac-surgery", 3: "medical-icu", 4: "surgical-icu"}


def load_cohort(data_path: Path) -> EHRData:
    """Load the PhysioNet 2012 ICU cohort as a (patients, variables, hours) tensor.

    Annotates ``obs`` with the in-hospital mortality label and the ICU the patient was admitted to.
    """
    edata = ed.dt.physionet2012(data_path=data_path)
    died = pd.to_numeric(edata.obs[LABEL]).to_numpy()
    edata.obs["died"] = pd.Categorical(np.where(died == 1, "died", "survived"))
    icu = pd.to_numeric(edata.obs["ICUType"]).astype(int)
    edata.obs["site"] = pd.Categorical([ICU_TYPES[i] for i in icu])
    edata.var_names = pd.Index(edata.var["Parameter"].astype(str))
    return edata


def labels(edata: EHRData) -> np.ndarray:
    return (edata.obs["died"].to_numpy() == "died").astype(np.int64)


def design_matrix(edata: EHRData) -> np.ndarray:
    """Flatten the hourly tensor, carrying the last observation forward before filling gaps.

    Leading gaps have nothing to carry forward, so they fall back to the variable's population median.
    """
    X = np.asarray(edata.X, dtype=np.float32)
    flat = _forward_fill(X).reshape(len(X), -1)
    medians = np.nan_to_num(np.nanmedian(flat, axis=0), nan=0.0)
    return np.where(np.isnan(flat), medians, flat)


def _forward_fill(X: np.ndarray) -> np.ndarray:
    seen = ~np.isnan(X)
    hours = np.arange(X.shape[2])
    last = np.where(seen, hours, -1)
    np.maximum.accumulate(last, axis=2, out=last)
    filled = np.take_along_axis(X, np.clip(last, 0, None), axis=2)
    return np.where(last < 0, np.nan, filled)


def zscore_tensor(edata: EHRData) -> np.ndarray:
    """Centre and scale each variable across patients and hours, keeping the tensor 3D.

    Raw units span three orders of magnitude, so an unscaled DTW distance is dominated by whichever
    high-variance variable happens to be observed rather than by trajectory shape.
    """
    X = np.asarray(edata.X, dtype=np.float64)
    mean = np.nanmean(X, axis=(0, 2), keepdims=True)
    std = np.nanstd(X, axis=(0, 2), keepdims=True)
    return (X - mean) / np.where(std == 0, 1.0, std)


def median_per_variable(edata: EHRData) -> np.ndarray:
    """Median of each variable over the stay, for the ehrapy functions that only accept 2D input.

    Returned as a 2D layer rather than a new object, so the tensor keeps all 48 hours.
    """
    X = np.asarray(edata.X, dtype=np.float32)
    summary = np.nanmedian(X, axis=2)
    return np.where(np.isnan(summary), np.nanmedian(summary, axis=0), summary).astype(np.float32)


def standardize(train: np.ndarray, *others: np.ndarray) -> tuple[np.ndarray, ...]:
    """Centre and scale on the training rows only, so no site sees another's statistics."""
    mean, std = train.mean(axis=0), train.std(axis=0)
    std[std == 0] = 1.0
    return tuple((a - mean) / std for a in (train, *others))


def train_test_split(n: int, test_fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    idx = np.random.default_rng(seed).permutation(n)
    cut = round(n * (1 - test_fraction))
    return idx[:cut], idx[cut:]


def load_shard(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as shard:
        return dict(shard)
