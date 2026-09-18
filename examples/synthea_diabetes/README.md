# Federated diabetes risk on Synthea, where there is signal

UK Biobank's synthetic extract draws every field independently, so no model can beat chance on it.
Synthea models disease progression, and `cohort_2` was built with per-site population parameters, so its five sites differ.

`run.py` and `figures.py` live in [`../ukb_diabetes`](../ukb_diabetes) and take the sites as an argument.
`explore.py` does not, because it reads UK Biobank's own raw layout.

```bash
cd ../ukb_diabetes
python run.py --sites ../../synthea_cohorts/cohort_2/data/omop --job synthea_fedavg --tag synthea
python figures.py --results results_synthea.json --out ../synthea_diabetes/federated_vs_local.png \
  --title "Incident type 2 diabetes across Synthea sites"
```

![federated against local](federated_vs_local.png)

| model | AUROC | 95% CI |
| --- | --- | --- |
| site_d alone, 350 people | 0.433 | 0.343 to 0.523 |
| site_b alone | 0.541 | 0.447 to 0.635 |
| site_e alone | 0.563 | 0.465 to 0.659 |
| site_a alone | 0.578 | 0.493 to 0.666 |
| site_c alone | 0.606 | 0.518 to 0.694 |
| federated | 0.582 | 0.499 to 0.675 |
| pooled | 0.586 | 0.503 to 0.674 |

FedAvg recovers the pooled model, 0.582 against 0.586, without any site sharing a row.
The smallest site would have done worse than chance alone, and it is the one federation helps most.

With 23 positives held out these intervals overlap heavily.
They support the claim that federated matches pooled and beats the weakest site, not that any single pair differs significantly.
