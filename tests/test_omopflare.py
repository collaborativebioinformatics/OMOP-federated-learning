from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

import omopflare as of

BMI, SBP, T2DM = 3038553, 3004249, 201826
KG_M2, MMHG = 9531, 8876


@pytest.fixture
def spec() -> of.FeatureSpec:
    return of.FeatureSpec(
        features=(
            of.Feature("bmi", BMI, "measurement", unit_concept_id=KG_M2, plausible_range=(10.0, 80.0)),
            of.Feature("t2dm", T2DM, "condition_occurrence"),
        ),
        vocabulary_version="v5.0",
        lookback_days=365,
    )


@pytest.fixture
def site(tmp_path):
    (tmp_path / "person.csv").write_text("person_id,year_of_birth\n1,1970\n2,1980\n3,1990\n")
    (tmp_path / "measurement.csv").write_text(
        "person_id,measurement_concept_id,measurement_date,value_as_number,unit_concept_id\n"
        "1,3038553,2020-06-01,25.0,9531\n"  # inside the window
        "1,3038553,2020-01-01,22.0,9531\n"  # older, should lose to the later row
        "1,3038553,2021-01-01,99.0,9531\n"  # on the index date, must not leak
        "2,3038553,2020-06-01,27.0,8876\n"  # wrong unit, dropped
        "3,3038553,2020-06-01,500.0,9531\n"  # implausible, dropped
    )
    (tmp_path / "condition_occurrence.csv").write_text(
        "person_id,condition_concept_id,condition_start_date\n1,201826,2020-09-01\n"
    )
    return of.OmopSource(tmp_path)


@pytest.fixture
def index() -> pa.Table:
    dates = pa.array(["2021-01-01"] * 3).cast(pa.date32())
    return pa.table({"person_id": pa.array([1, 2, 3], pa.int64()), "index_date": dates})


def _matrix(site, spec, index) -> dict[int, np.ndarray]:
    out = {}
    for batch in of.extract(site, spec, index):
        ids, values = of.to_matrix(batch, spec)
        for person, row in zip(ids, values, strict=True):
            out[int(person)] = row
    return out


def test_latest_value_in_window_wins(site, spec, index):
    assert _matrix(site, spec, index)[1][0] == 25.0


def test_value_on_the_index_date_does_not_leak(site, spec, index):
    assert 99.0 not in _matrix(site, spec, index)[1]


def test_wrong_unit_is_dropped(site, spec, index):
    assert np.isnan(_matrix(site, spec, index)[2][0])


def test_implausible_value_is_dropped(site, spec, index):
    assert np.isnan(_matrix(site, spec, index)[3][0])


def test_presence_feature(site, spec, index):
    rows = _matrix(site, spec, index)
    assert rows[1][1] == 1.0
    assert np.isnan(rows[2][1])


def test_numeric_feature_requires_a_unit():
    with pytest.raises(ValueError, match="unit_concept_id"):
        of.Feature("bmi", BMI, "measurement")


def test_concept_id_must_be_a_positive_standard_concept():
    with pytest.raises(ValueError, match="positive standard concept"):
        of.Feature("unmapped", 0, "condition_occurrence")


def test_duplicate_feature_names_rejected():
    feature = of.Feature("x", T2DM, "condition_occurrence")
    with pytest.raises(ValueError, match="duplicate"):
        of.FeatureSpec(features=(feature, feature), vocabulary_version="v5.0", lookback_days=1)


def test_spec_round_trips(tmp_path, spec):
    path = tmp_path / "spec.json"
    spec.to_json(path)
    assert of.FeatureSpec.from_json(path) == spec


def test_missing_indicators_widen_the_matrix(spec):
    wide = of.FeatureSpec(
        features=spec.features,
        vocabulary_version=spec.vocabulary_version,
        lookback_days=spec.lookback_days,
        missing_indicators=True,
    )
    assert wide.column_names == ("bmi", "t2dm", "bmi_missing")


def test_site_stats_match_numpy():
    values = np.array([[1.0, 2.0], [3.0, np.nan], [5.0, 6.0]])
    stats = of.SiteStats.from_matrix(values)
    assert stats.n.tolist() == [3, 2]
    np.testing.assert_allclose(stats.mean, [3.0, 4.0])
    np.testing.assert_allclose(stats.std, [np.std([1, 3, 5]), np.std([2, 6])])


def test_combining_sites_equals_pooling():
    left, right = np.array([[1.0], [3.0]]), np.array([[5.0], [7.0]])
    combined = of.combine([of.SiteStats.from_matrix(left), of.SiteStats.from_matrix(right)])
    pooled = of.SiteStats.from_matrix(np.vstack([left, right]))
    np.testing.assert_allclose(combined.mean, pooled.mean)
    np.testing.assert_allclose(combined.std, pooled.std)


def test_small_cells_are_suppressed():
    stats = of.SiteStats.from_matrix(np.array([[1.0, np.nan]] * 3))
    assert stats.suppressed(min_cell_count=5).tolist() == [True, True]
    assert stats.redacted(min_cell_count=5).n.tolist() == [0, 0]


def test_prevalence_hidden_when_events_are_few():
    assert of.prevalence([0] * 100 + [1] * 2) is None
    assert of.prevalence([0] * 100 + [1] * 10) == pytest.approx(10 / 110)


def test_validate_flags_a_vocabulary_mismatch(site, spec):
    findings = of.validate(site, spec)
    assert any(f.check == "vocabulary_version" for f in findings)
