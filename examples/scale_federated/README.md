# NVFlare at scale

Times a real NVFlare FedAvg job over a large OMOP cohort, so the throughput numbers cover the federated path and not just one client's extraction.

```bash
python ../scale_benchmark.py --multipliers 10000    # builds the cohort
python fl_run.py --cdm ../.scale/x10000 --sites 3 --rounds 5
```

The cohort is MIMIC-IV's real measurement rows replicated across more patients, so concept, unit, value and date cardinality stay realistic.
Each client takes a hash slice of `person_id`, so the sites never share a patient.

## Result

1,000,000 patients over 3,385,500,000 measurement rows, three clients, five rounds.

| Stage | Time |
| --- | --- |
| Extract one site, 333k patients | 14s |
| Five FedAvg rounds, wall clock | 42s |
| Per round | 8.4s |

Training itself is 0.02s per round per client at batch size 1024.
Everything else is extraction and NVFlare's own round trip, which is the honest shape of the cost: the model is tiny and the data is not.
