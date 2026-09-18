from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import nvflare.client as flare
import torch
from fl_model import RiskNet
from torch import nn

import omopflare as of


def cohort(cdm: Path, spec: of.FeatureSpec, site: int, sites: int):
    """Extract this site's slice of the cohort.

    Args:
        cdm: Directory of OMOP tables shared by every site.
        spec: The frozen feature schema.
        site: Which hash slice this client owns.
        sites: How many slices the cohort is cut into.

    Returns:
        The loader, the row count and the seconds spent extracting.
    """
    source = of.OmopSource(cdm)
    index = f"""
        select person_id, max(measurement_date) + interval '1' day as index_date
        from measurement where abs(hash(person_id)) % {sites} = {site}
        group by person_id
    """
    start = time.perf_counter()
    ids, matrix = of.feature_matrix(source, spec, index)
    scaler = of.SiteStats.from_matrix(matrix)
    features = np.nan_to_num(of.standardize(matrix, scaler))
    elapsed = time.perf_counter() - start

    labels = (np.random.default_rng(site).random(len(ids)) < 0.15).astype(np.float64)
    dataset = of.CohortDataset(features, labels)
    return of.dataloader(dataset, batch_size=1024, shuffle=True), len(ids), elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdm", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--site", type=int, required=True)
    parser.add_argument("--sites", type=int, required=True)
    args = parser.parse_args()

    spec = of.FeatureSpec.from_json(args.spec)
    loader, rows, extract_seconds = cohort(args.cdm, spec, args.site, args.sites)
    print(f"site {args.site}: {rows:,} patients extracted in {extract_seconds:.2f}s", flush=True)

    model = RiskNet(spec.width)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)

    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        model.train()
        start, steps = time.perf_counter(), 0
        for features, labels in loader:
            optimizer.zero_grad()
            criterion(model(features), labels).backward()
            optimizer.step()
            steps += 1
        print(f"site {args.site}: round trained in {time.perf_counter() - start:.2f}s", flush=True)
        flare.send(
            flare.FLModel(
                params={key: value.cpu() for key, value in model.state_dict().items()},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


if __name__ == "__main__":
    main()
