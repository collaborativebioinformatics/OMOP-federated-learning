"""Write the cohort tensor to ``hourly.h5ed`` and one shard per site to ``data/shards``.

A site is the ICU the patient was admitted to, and each shard becomes one NVFlare client.
Shards carry ``rows_train``/``rows_test`` so predictions can be mapped back onto the cohort tensor.
"""

from pathlib import Path

import ehrdata as ed
import numpy as np

from data import design_matrix, labels, load_cohort, median_per_variable, standardize, train_test_split

DATA = Path("data")
TEST_FRACTION = 0.25
SEED = 0

DATA.mkdir(parents=True, exist_ok=True)
edata = load_cohort(DATA / "physionet2012")
y = labels(edata)
X = design_matrix(edata)

edata.layers["median"] = median_per_variable(edata)
edata.obsm["X_flat"] = standardize(X)[0]
ed.io.write_h5ed(edata, DATA / "hourly.h5ed")

shards = DATA / "shards"
shards.mkdir(exist_ok=True)
sites = edata.obs["site"].to_numpy()
for i, site in enumerate(sorted(set(sites))):
    rows = np.flatnonzero(sites == site)
    train, test = train_test_split(rows.size, TEST_FRACTION, SEED + i)
    X_train, X_test = standardize(X[rows[train]], X[rows[test]])
    np.savez_compressed(
        shards / f"{site}.npz",
        X_train=X_train,
        y_train=y[rows[train]],
        X_test=X_test,
        y_test=y[rows[test]],
        rows_train=rows[train],
        rows_test=rows[test],
    )
    print(f"{site}: {train.size} train / {test.size} test, mortality {y[rows].mean():.3f}")

print(f"{edata.shape} tensor, n_t={edata.n_t}, {X.shape[1]} features, {y.mean():.3f} mortality -> {shards}")
