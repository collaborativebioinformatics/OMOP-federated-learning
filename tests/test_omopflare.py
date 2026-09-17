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


@pytest.fixture
def presence_spec() -> of.FeatureSpec:
    return of.FeatureSpec(
        features=(of.Feature("t2dm", T2DM, "condition_occurrence"),),
        vocabulary_version="v5.0",
        lookback_days=365,
    )


def test_sparse_layout_rejects_numeric_features(site, spec, index):
    batch = next(of.extract(site, spec, index))
    with pytest.raises(ValueError, match="presence features only"):
        of.to_matrix(batch, spec, layout="sparse")


def test_auto_layout_is_dense_for_numeric_specs(site, spec, index):
    batch = next(of.extract(site, spec, index))
    _, matrix = of.to_matrix(batch, spec, layout="auto")
    assert isinstance(matrix, np.ndarray)


def test_auto_layout_is_sparse_for_presence_specs(site, presence_spec, index):
    batch = next(of.extract(site, presence_spec, index))
    _, matrix = of.to_matrix(batch, presence_spec, layout="auto")
    assert matrix.shape == (3, 1)
    assert matrix.todense()[:, 0].tolist() == [1.0, 0.0, 0.0]


def test_unknown_layout_rejected(site, spec, index):
    batch = next(of.extract(site, spec, index))
    with pytest.raises(ValueError, match="unknown layout"):
        of.to_matrix(batch, spec, layout="ragged")


def test_sequence_tensor_is_sparse_with_nan_fill(site, spec, index):
    _ids, tensor = next(of.extract_sequence(site, spec, index, bins=4))
    assert tensor.shape == (3, 2, 4)
    assert np.isnan(tensor.fill_value)
    assert np.isnan(tensor.todense()[1, 0, 0])


def test_sequence_bins_by_distance_from_the_landmark(site, spec, index):
    # 2020-06-01 is 214 days before the landmark, so with two bins over 365 days it belongs to the older half.
    _, tensor = next(of.extract_sequence(site, spec, index, bins=2))
    dense = tensor.todense()
    assert dense[0, 0, 0] == 25.0
    assert np.isnan(dense[0, 0, 1])

    _, single = next(of.extract_sequence(site, spec, index, bins=1))
    assert single.todense()[0, 0, 0] == 25.0


def test_sequence_drops_wrong_units_and_implausible_values(site, spec, index):
    _, tensor = next(of.extract_sequence(site, spec, index, bins=4))
    dense = tensor.todense()
    assert np.isnan(dense[1, 0]).all()
    assert np.isnan(dense[2, 0]).all()


def test_sequence_rejects_zero_bins(site, spec, index):
    with pytest.raises(ValueError, match="bins must be positive"):
        next(of.extract_sequence(site, spec, index, bins=0))


def test_to_ehrdata_keeps_the_tensor_sparse(site, spec, index):
    import sparse

    ids, tensor = next(of.extract_sequence(site, spec, index, bins=5))
    edata = of.to_ehrdata(ids, tensor, spec)
    assert isinstance(edata.X, sparse.SparseArray)
    assert edata.X.nnz == tensor.nnz


def test_to_ehrdata_carries_the_spec(site, spec, index):
    ids, tensor = next(of.extract_sequence(site, spec, index, bins=5))
    edata = of.to_ehrdata(ids, tensor, spec)
    assert edata.shape == (3, 2, 5)
    assert list(edata.var.index) == ["bmi", "t2dm"]
    assert edata.tem["days_before_index"].tolist()[-1] == 0.0


def test_concept_counts_suppress_small_cells(site, spec):
    counts = of.concept_counts(site, spec.features, min_cell_count=5)
    assert counts["bmi"] == 0
    assert of.concept_counts(site, spec.features, min_cell_count=1)["bmi"] == 1


def test_propose_spec_keeps_features_every_site_has(spec):
    counts = [{"bmi": 40, "t2dm": 10}, {"bmi": 30, "t2dm": 12}]
    agreed = of.propose_spec(counts, spec.features, vocabulary_version="v5.0", lookback_days=365)
    assert [f.name for f in agreed.features] == ["bmi", "t2dm"]


def test_propose_spec_drops_a_feature_one_site_lacks(spec):
    counts = [{"bmi": 40, "t2dm": 10}, {"bmi": 0, "t2dm": 12}]
    agreed = of.propose_spec(counts, spec.features, vocabulary_version="v5.0", lookback_days=365)
    assert [f.name for f in agreed.features] == ["t2dm"]


def test_propose_spec_min_sites_relaxes_the_intersection(spec):
    counts = [{"bmi": 40, "t2dm": 10}, {"bmi": 0, "t2dm": 12}]
    agreed = of.propose_spec(counts, spec.features, vocabulary_version="v5.0", lookback_days=365, min_sites=1)
    assert [f.name for f in agreed.features] == ["bmi", "t2dm"]


def test_propose_spec_raises_when_nothing_qualifies(spec):
    with pytest.raises(ValueError, match="no candidate reached"):
        of.propose_spec([{"bmi": 0, "t2dm": 0}], spec.features, vocabulary_version="v5.0", lookback_days=365)


@pytest.fixture
def ohdsi_site(tmp_path):
    """A site in OHDSI export style: uppercase file names, uppercase columns, quoted empty numerics."""
    (tmp_path / "PERSON.csv").write_text("PERSON_ID,YEAR_OF_BIRTH\n1,1970\n2,1980\n")
    (tmp_path / "MEASUREMENT.csv").write_text(
        "PERSON_ID,MEASUREMENT_CONCEPT_ID,MEASUREMENT_DATE,VALUE_AS_NUMBER,UNIT_CONCEPT_ID\n"
        "1,3038553,2020-06-01,25.0,9531\n"
        "2,3038553,2020-06-01,,9531\n"
    )
    (tmp_path / "CONDITION_OCCURRENCE.csv").write_text(
        "PERSON_ID,CONDITION_CONCEPT_ID,CONDITION_START_DATE\n1,201826,2020-09-01\n"
    )
    return of.OmopSource(tmp_path)


def test_uppercase_tables_are_discovered(ohdsi_site):
    assert ohdsi_site.has("person")
    assert ohdsi_site.count("measurement") == 2


def test_uppercase_columns_extract(ohdsi_site, spec):
    index = ohdsi_site.sql("select person_id, date '2021-01-01' as index_date from person").arrow().read_all()
    rows = {}
    for batch in of.extract(ohdsi_site, spec, index):
        ids, values = of.to_matrix(batch, spec)
        rows.update({int(p): r for p, r in zip(ids, values, strict=True)})
    assert rows[1][0] == 25.0
    assert np.isnan(rows[2][0])
