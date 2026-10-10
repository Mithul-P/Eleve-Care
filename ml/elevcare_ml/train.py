"""
Train the failure-probability and RUL models from simulated run-to-failure data.

Run:  python -m elevcare_ml.train            (writes ml/artifacts/models.joblib)

Why these algorithms
--------------------
* Failure probability: HistGradientBoostingClassifier
    - gradient-boosted trees handle non-linear thresholds (e.g. "kurtosis
      above ~5 matters, below doesn't") on small tabular data without scaling;
    - **monotonic constraints** encode physics: the probability of failure may
      only go *up* when any degradation indicator goes up. This removes
      unphysical wiggles and makes the model defensible in front of judges;
    - it's fast enough to retrain on start-up in a few seconds.
* RUL: HistGradientBoostingRegressor on log(1 + RUL days), again monotonic
  (RUL may only go *down* as indicators worsen). The log target reflects that
  the uncertainty in RUL is proportional to its size (100 +/- 30 days vs
  10 +/- 3 days), similar in spirit to a log-normal survival model.
* Explanations: permutation importance on held-out trajectories (model
  agnostic, honest) combined with physics thresholds (physics.THRESHOLDS).

Evaluation is split *by trajectory* so no unit appears in both train and
test - otherwise the numbers would be optimistic.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, mean_absolute_error, roc_auc_score

from . import physics as P
from . import simulator as S
from .features import PER_READING_KEYS, life_increment_from_features, reading_features, window_features_batch
from .signal import synthesize, waveform_features

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "artifacts"
DT_HOURS = 6.0
MAX_DAYS = 4500


WAVE_KEYS = ["vib_rms", "vib_peak", "vib_crest", "vib_kurtosis", "fault_band_ratio", "dominant_freq_hz"]


def simulate_trajectories(ctype: str, n_traj: int, rng: np.random.Generator):
    """
    Run n_traj units from (near) new to failure with a reading every 6 h.

    Pass 1 records the hidden state and the cheap sensor features for every
    step. Waveforms are expensive, so they are synthesised later only for the
    steps that the sampled training windows actually use (see build_dataset).
    """
    prof = S.random_profiles(ctype, n_traj, rng)
    state = S.initial_state(prof, rng, damage=rng.uniform(0.0, 0.15, n_traj))
    max_steps = int(MAX_DAYS * 24 / DT_HOURS)
    cheap_keys = [k for k in PER_READING_KEYS if k not in WAVE_KEYS]
    hist = {k: [] for k in cheap_keys}
    amp_hist, fatigue_hist = [], []
    fatigue_obs = np.zeros(n_traj)
    fail_step = np.full(n_traj, -1)
    for step in range(max_steps):
        trips = S.advance(prof, state, DT_HOURS, rng)
        obs = S.observe(prof, state, (step * DT_HOURS) % 24, rng, with_waveform=False)
        rf = reading_features(ctype, obs)
        # Life fraction is computed from *observed* data exactly as the live
        # ingestion pipeline does (trips x load factor from tension sensors,
        # plus the winding temperature for the machine's Arrhenius term).
        fatigue_obs = fatigue_obs + life_increment_from_features(ctype, trips, rf)
        for k in cheap_keys:
            hist[k].append(rf[k])
        amp_hist.append(obs["vib_amplitude"])
        fatigue_hist.append(fatigue_obs.copy())
        newly = (state.damage >= 1.0) & (fail_step < 0)
        fail_step[newly] = step
        if np.all(fail_step >= 0):
            break
    hist = {k: np.array(v) for k, v in hist.items()}  # (steps, n_traj)
    return hist, np.array(amp_hist), np.array(fatigue_hist), fail_step


def build_dataset(ctype: str, n_traj: int, rows_per_traj: int, rng: np.random.Generator):
    hist, amps, fatigue, fail_step = simulate_trajectories(ctype, n_traj, rng)
    steps_per_day = int(24 / DT_HOURS)
    trend_steps = 7 * steps_per_day
    n_steps = amps.shape[0]
    for k in WAVE_KEYS:
        hist[k] = np.full((n_steps, n_traj), np.nan)

    X, y_rul, groups = [], [], []
    for j in range(n_traj):
        f = fail_step[j]
        if f < trend_steps + 10:
            continue
        lo = trend_steps
        near_end = rng.integers(max(lo, f - 90 * steps_per_day), f + 1, rows_per_traj // 2)
        anywhere = rng.integers(lo, f + 1, rows_per_traj - rows_per_traj // 2)
        samples = np.concatenate([near_end, anywhere])

        # Synthesise waveforms only for the steps inside sampled windows.
        needed = np.zeros(n_steps, dtype=bool)
        for s in samples:
            needed[s - trend_steps: s + 1] = True
        idx = np.where(needed)[0]
        wf = waveform_features(ctype, synthesize(ctype, amps[idx, j], rng))
        for k in WAVE_KEYS:
            hist[k][idx, j] = wf[k]

        # All sampled windows of this unit in one vectorised call (identical
        # numbers to the per-window `window_features` used live).
        per = {k: hist[k][:, j] for k in PER_READING_KEYS}
        X.append(window_features_batch(DT_HOURS / 24.0, per, fatigue[:, j], samples))
        y_rul.append((f - samples) * DT_HOURS / 24.0)
        groups.append(np.full(len(samples), j))
    return np.vstack(X), np.concatenate(y_rul), np.concatenate(groups)


def train_component(ctype: str, n_traj: int, rows_per_traj: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    t0 = time.time()
    X, rul, groups = build_dataset(ctype, n_traj, rows_per_traj, rng)
    y = (rul <= P.FAILURE_HORIZON_DAYS).astype(int)

    uniq = np.unique(groups)
    test_units = rng.choice(uniq, size=max(2, len(uniq) // 5), replace=False)
    test = np.isin(groups, test_units)

    n_feat = len(P.FEATURE_NAMES)
    clf_kwargs = dict(max_iter=250, learning_rate=0.06, max_leaf_nodes=16, min_samples_leaf=40,
                      l2_regularization=1.0, random_state=seed)
    reg_kwargs = dict(max_iter=300, learning_rate=0.06, max_leaf_nodes=16, min_samples_leaf=40,
                      l2_regularization=1.0, random_state=seed)
    clf = HistGradientBoostingClassifier(monotonic_cst=[1] * n_feat, **clf_kwargs)
    reg = HistGradientBoostingRegressor(monotonic_cst=[-1] * n_feat, **reg_kwargs)
    clf.fit(X[~test], y[~test])
    reg.fit(X[~test], np.log1p(rul[~test]))

    p = clf.predict_proba(X[test])[:, 1]
    rul_pred = np.expm1(reg.predict(X[test]))
    near_1y = rul[test] <= 365
    near_90d = rul[test] <= 90
    near_30d = rul[test] <= 30
    near_7d = rul[test] <= 7
    metrics = {
        "rows_train": int((~test).sum()),
        "rows_test": int(test.sum()),
        "units": int(len(uniq)),
        "roc_auc": float(roc_auc_score(y[test], p)) if len(np.unique(y[test])) > 1 else None,
        "brier": float(brier_score_loss(y[test], p)),
        "rul_mae_days_within_1y": float(mean_absolute_error(rul[test][near_1y], rul_pred[near_1y])) if near_1y.any() else None,
        "rul_mae_days_within_90d": (float(mean_absolute_error(rul[test][near_90d], rul_pred[near_90d]))
                                    if near_90d.any() else None),
        "rul_mae_days_within_30d": (float(mean_absolute_error(rul[test][near_30d], rul_pred[near_30d]))
                                    if near_30d.any() else None),
        "rul_mae_days_within_7d": (float(mean_absolute_error(rul[test][near_7d], rul_pred[near_7d]))
                                   if near_7d.any() else None),
        "median_unit_life_days": float(np.median([rul[groups == g].max() for g in uniq])),
    }

    # Permutation importance on held-out units -> used to rank explanations.
    sub = np.where(test)[0]
    if len(sub) > 1500:
        sub = rng.choice(sub, 1500, replace=False)
    pi = permutation_importance(clf, X[sub], y[sub], scoring="roc_auc", n_repeats=3, random_state=seed)
    imp = np.maximum(pi.importances_mean, 0.0)
    imp = imp / imp.sum() if imp.sum() > 0 else np.full(n_feat, 1.0 / n_feat)

    # Refit on all data for deployment once evaluation numbers are recorded.
    clf_full = HistGradientBoostingClassifier(monotonic_cst=[1] * n_feat, **clf_kwargs).fit(X, y)
    reg_full = HistGradientBoostingRegressor(monotonic_cst=[-1] * n_feat, **reg_kwargs).fit(X, np.log1p(rul))
    metrics["train_seconds"] = round(time.time() - t0, 1)
    return {
        "classifier": clf_full,
        "regressor": reg_full,
        "importances": {k: float(v) for k, v in zip(P.FEATURE_NAMES, imp)},
        "metrics": metrics,
    }


def train_all(out_dir: Path = ARTIFACT_DIR, n_traj: int = 60, rows_per_traj: int = 240,
              seed: int = 7, verbose: bool = True, n_jobs: int = -1) -> Path:
    """
    Train all four component models. The four types are independent, so they
    are trained in parallel processes (n_jobs=-1 -> one per core; 1 -> serial).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    trained_at = datetime.now(timezone.utc)
    bundle = {
        "version": f"hgb-mono-{trained_at:%Y%m%d%H%M}",
        "trained_at": trained_at.isoformat(),
        "feature_names": list(P.FEATURE_NAMES),
        "horizon_days": P.FAILURE_HORIZON_DAYS,
        "physics_models": P.PHYSICS_MODEL,
        "models": {},
    }
    jobs = [(ctype, n_traj, rows_per_traj, seed + i) for i, ctype in enumerate(P.COMPONENT_TYPES)]
    if n_jobs < 1:
        n_jobs = min(len(jobs), os.cpu_count() or 1)
    if n_jobs == 1:
        results = [train_component(*j) for j in jobs]
    else:
        from joblib import Parallel, delayed

        results = Parallel(n_jobs=min(len(jobs), n_jobs))(
            delayed(train_component)(*j) for j in jobs)
    for (ctype, *_), res in zip(jobs, results):
        bundle["models"][ctype] = res
        if verbose:
            print(f"[train] {ctype:9s} {json.dumps(res['metrics'])}")
    path = out_dir / "models.joblib"
    joblib.dump(bundle, path)
    (out_dir / "metrics.json").write_text(json.dumps(
        {"version": bundle["version"], "trained_at": bundle["trained_at"],
         "metrics": {k: v["metrics"] for k, v in bundle["models"].items()},
         "importances": {k: v["importances"] for k, v in bundle["models"].items()}}, indent=2))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ARTIFACT_DIR)
    ap.add_argument("--units", type=int, default=60, help="simulated units per component type")
    ap.add_argument("--rows", type=int, default=240, help="training rows sampled per unit")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=-1, help="parallel processes (1 = serial)")
    args = ap.parse_args()
    path = train_all(args.out, args.units, args.rows, args.seed, n_jobs=args.jobs)
    print(f"[train] saved {path}")


if __name__ == "__main__":
    main()
