from __future__ import annotations

from pathlib import Path

import numpy as np
from cohort import SITES_ROOT, SPEC, index_table, sites
from model import RiskMLP

import omopflare as of

HERE = Path(__file__).parent
JOB = "omop_t2dm_fedavg"
WORKSPACE = HERE / "workspace"
SPEC_PATH = HERE / "spec.json"


def report_sites() -> list[of.SiteStats]:
    """Validate every site against the spec and print what each may disclose.

    Returns:
        One redacted summary per site, ready to combine into a federated scaler.
    """
    summaries = []
    for name in sites():
        source = of.OmopSource(SITES_ROOT / name)
        findings = of.validate(source, SPEC)
        if failures := of.errors(findings):
            raise SystemExit("\n".join(str(f) for f in failures))
        index = index_table(source)
        labels = np.asarray(index.column("label"))
        stats = of.site_statistics(source, SPEC, index).redacted()
        rate = of.prevalence(labels)
        shown = "withheld" if rate is None else f"{rate:.1%}"
        print(f"{name}: {index.num_rows:5d} people, T2DM {shown}, observed per feature {stats.n.tolist()}")
        summaries.append(stats)
    return summaries


def main() -> None:
    SPEC.to_json(SPEC_PATH)
    summaries = report_sites()
    scaler = of.federated_scaler(summaries)
    print(f"\nfederated scaler mean {np.round(scaler.mean, 2).tolist()}")

    names = sites()
    recipe = of.fedavg_recipe(
        JOB,
        RiskMLP(SPEC.width),
        HERE / "client.py",
        names,
        num_rounds=10,
        train_args={name: f"--site {(SITES_ROOT / name).resolve()} --spec {SPEC_PATH.resolve()}" for name in names},
    )
    of.simulate(recipe, names, WORKSPACE)
    print(f"\nglobal model: {of.global_model_path(WORKSPACE, JOB)}")


if __name__ == "__main__":
    main()
