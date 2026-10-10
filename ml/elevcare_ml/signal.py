"""
Vibration waveform synthesis and signal-processing features.

Synthesis: each component type has a characteristic vibration signature
whose amplitude grows with its (hidden) damage level. Feature extraction is
what a real condition-monitoring system does with an accelerometer trace:

* RMS         - overall vibration energy (ISO 10816 style severity)
* Peak        - largest absolute excursion
* Crest       - peak / RMS; rises with *localised* defects before RMS does
* Kurtosis    - "peakiness"; 3 for Gaussian noise, >>3 for repetitive impacts
                (the textbook early indicator of bearing spalls)
* Fault-band energy ratio - share of FFT energy in the component's defect
                frequency band (e.g. bearing resonance, groove-pass frequency)

All functions are vectorised over the first axis so the same code runs for a
single live reading and for 100k-row training sets.
"""

from __future__ import annotations

import numpy as np

from .physics import FAULT_BANDS_HZ, SAMPLE_RATE_HZ, WAVEFORM_SAMPLES

_T = np.arange(WAVEFORM_SAMPLES) / SAMPLE_RATE_HZ
_FREQS = np.fft.rfftfreq(WAVEFORM_SAMPLES, d=1.0 / SAMPLE_RATE_HZ)
_WINDOW = np.hanning(WAVEFORM_SAMPLES)

NOISE_G = 0.03          # broadband running noise (guide rails, airflow)
BPFO_HZ = 87.0          # bearing outer-race defect frequency
RESONANCE_HZ = 320.0    # structural resonance excited by bearing impacts
RING_DECAY_S = 0.0012


def synthesize(component_type: str, amplitude: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """
    Build accelerometer waveforms (g) of shape (n, WAVEFORM_SAMPLES).
    `amplitude` (n,) is the strength of the component's fault signature.
    """
    amplitude = np.atleast_1d(np.asarray(amplitude, dtype=float))
    n = amplitude.shape[0]
    t = _T[None, :]
    a = amplitude[:, None]
    phase = rng.uniform(0, 2 * np.pi, size=(n, 1))
    wave = rng.normal(0.0, NOISE_G, size=(n, WAVEFORM_SAMPLES))

    if component_type == "rope":
        # Low-frequency rope/car sway (~8-11 Hz) - grows with unequal tension.
        f = rng.uniform(8.0, 11.0, size=(n, 1))
        wave += a * np.sin(2 * np.pi * f * t + phase)
    elif component_type == "sheave":
        # Groove-pass frequency and its harmonic from a worn/uneven groove.
        wave += a * np.sin(2 * np.pi * 35.0 * t + phase)
        wave += 0.5 * a * np.sin(2 * np.pi * 50.0 * t + 2 * phase)
    elif component_type == "bearing":
        # Periodic impacts at BPFO, each ringing the housing resonance.
        # This is what drives kurtosis and crest factor up.
        wave += 0.02 * np.sin(2 * np.pi * 25.0 * t + phase)
        period = 1.0 / BPFO_HZ
        offset = rng.uniform(0, period, size=(n, 1))
        since_impact = np.mod(t - offset, period)
        ringing = np.exp(-since_impact / RING_DECAY_S) * np.sin(2 * np.pi * RESONANCE_HZ * since_impact)
        wave += a * ringing
    elif component_type == "traction":
        # Twice line frequency (100 Hz on a 50 Hz grid): magnetic pull
        # imbalance / rotor eccentricity signature of the motor.
        wave += a * np.sin(2 * np.pi * 100.0 * t + phase)
        wave += 0.3 * a * np.sin(2 * np.pi * 200.0 * t + phase)
    else:
        raise ValueError(f"unknown component type {component_type}")
    return wave


def to_frame(waves, samples: int = WAVEFORM_SAMPLES) -> np.ndarray:
    """
    Bring any waveform of 32-4096 samples (one trace or a batch) to the model's
    fixed frame length by linear resampling, so real gateways may send whatever
    frame length their accelerometer buffers. Exactly `samples` long → unchanged.
    """
    w = np.atleast_2d(np.asarray(waves, dtype=float))
    n = w.shape[1]
    if n == samples:
        return w
    if n < 32 or n > 4096:
        raise ValueError(f"waveform must have 32-4096 samples, got {n}")
    src = np.linspace(0.0, 1.0, n)
    dst = np.linspace(0.0, 1.0, samples)
    return np.vstack([np.interp(dst, src, row) for row in w])


def waveform_features(component_type: str, waves: np.ndarray) -> dict[str, np.ndarray]:
    """
    Statistical + FFT features for waveforms of shape (n, samples) or (samples,).
    Written as a handful of large array ops (no per-row Python) so that the
    same function is cheap for one live frame and for 250k training rows.
    Any frame length in 32-4096 is resampled to WAVEFORM_SAMPLES first.
    """
    w = to_frame(waves)
    centered = w - w.mean(axis=1, keepdims=True)
    sq = centered * centered
    rms = np.sqrt(sq.mean(axis=1))
    peak = np.abs(centered).max(axis=1)
    safe_rms = np.maximum(rms, 1e-9)
    crest = peak / safe_rms
    kurtosis = (sq * sq).mean(axis=1) / (safe_rms * safe_rms) ** 2  # Pearson (normal = 3)

    spec = np.fft.rfft(centered * _WINDOW[None, :], axis=1)
    power = spec.real * spec.real + spec.imag * spec.imag
    power[:, 0] = 0.0
    total = np.maximum(power.sum(axis=1), 1e-12)
    lo, hi = FAULT_BANDS_HZ[component_type]
    band = (_FREQS >= lo) & (_FREQS <= hi)
    band_ratio = power[:, band].sum(axis=1) / total
    dominant = _FREQS[np.argmax(power, axis=1)]

    return {
        "vib_rms": rms,
        "vib_peak": peak,
        "vib_crest": crest,
        "vib_kurtosis": kurtosis,
        "fault_band_ratio": band_ratio,
        "dominant_freq_hz": dominant,
    }


def spectrum(waveform: list[float] | np.ndarray) -> tuple[list[float], list[float]]:
    """Single-sided amplitude spectrum for display (freqs Hz, amplitude g)."""
    w = to_frame(waveform)[0]
    w = w - w.mean()
    amp = np.abs(np.fft.rfft(w * _WINDOW)) * 2.0 / _WINDOW.sum()
    return _FREQS.tolist(), amp.tolist()


# ---------------------------------------------------------------------------
# Waveform analysis (diagnostics beyond the model features)
# ---------------------------------------------------------------------------
# Characteristic frequencies per component type and where they show up.
#   spectrum : the raw FFT of the acceleration signal
#   envelope : the FFT of the signal *envelope* (Hilbert demodulation). Bearing
#              defects are periodic impacts that ring a high-frequency
#              resonance; their repetition rate (BPFO) is invisible in the raw
#              spectrum but appears cleanly in the envelope spectrum - the
#              standard technique in vibration-based bearing diagnostics.
CHARACTERISTIC_HZ: dict[str, list[dict]] = {
    "rope": [
        {"name": "Rope/car sway", "hz": 9.5, "tol_hz": 2.5, "domain": "spectrum", "healthy_g": 0.07,
         "meaning": "car sway from slack or unequally tensioned ropes"},
    ],
    "sheave": [
        {"name": "Groove-pass 1x", "hz": 35.0, "tol_hz": 4.0, "domain": "spectrum", "healthy_g": 0.06,
         "meaning": "rope passing an uneven / worn groove once per revolution"},
        {"name": "Groove-pass harmonic", "hz": 50.0, "tol_hz": 4.0, "domain": "spectrum", "healthy_g": 0.03,
         "meaning": "harmonic of the groove-pass frequency - groove profile no longer round"},
    ],
    "bearing": [
        {"name": "BPFO (outer-race defect)", "hz": BPFO_HZ, "tol_hz": 4.0, "domain": "envelope", "healthy_g": 0.012,
         "meaning": "rolling elements striking a spall on the outer race"},
        {"name": "2x BPFO", "hz": 2 * BPFO_HZ, "tol_hz": 4.0, "domain": "envelope", "healthy_g": 0.01,
         "meaning": "harmonic of the defect rate - the spall is growing"},
        {"name": "Housing resonance", "hz": RESONANCE_HZ, "tol_hz": 12.0, "domain": "spectrum", "healthy_g": 0.012,
         "meaning": "structural resonance excited by each impact"},
    ],
    "traction": [
        {"name": "2x line frequency", "hz": 100.0, "tol_hz": 4.0, "domain": "spectrum", "healthy_g": 0.06,
         "meaning": "magnetic pull imbalance / rotor eccentricity in the motor"},
        {"name": "4x line frequency", "hz": 200.0, "tol_hz": 4.0, "domain": "spectrum", "healthy_g": 0.03,
         "meaning": "harmonic - drive or winding asymmetry"},
    ],
}

DETECT_SNR_DB = 6.0   # a line must stand >= 6 dB above the local noise floor to count as detected


def _analytic_envelope(x: np.ndarray) -> np.ndarray:
    """Envelope via the analytic signal (FFT-based Hilbert transform, numpy only)."""
    n = x.shape[-1]
    spec = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[1:n // 2] = 2.0
        h[n // 2] = 1.0
    else:
        h[1:(n + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(spec * h))


def _amplitude_spectrum(x: np.ndarray) -> np.ndarray:
    x = x - x.mean()
    return np.abs(np.fft.rfft(x * _WINDOW)) * 2.0 / _WINDOW.sum()


def _peaks(freqs: np.ndarray, amp: np.ndarray, k: int = 5) -> list[dict]:
    a = amp.copy()
    a[0] = 0.0
    local = (a[1:-1] > a[:-2]) & (a[1:-1] >= a[2:])
    idx = np.where(local)[0] + 1
    idx = idx[np.argsort(a[idx])[::-1][:k]]
    return [{"freq_hz": round(float(freqs[i]), 1), "amplitude_g": round(float(a[i]), 5)} for i in idx]


def _line(freqs: np.ndarray, amp: np.ndarray, hz: float, tol: float) -> tuple[float, float, float]:
    """(amplitude at the line, local noise floor, SNR dB) for a characteristic frequency."""
    band = (freqs >= hz - tol) & (freqs <= hz + tol)
    if not band.any():
        return 0.0, 0.0, 0.0
    line = float(amp[band].max())
    side = (freqs >= hz - 6 * tol) & (freqs <= hz + 6 * tol) & ~band & (freqs > 0)
    floor = float(np.median(amp[side])) if side.any() else float(np.median(amp[1:]))
    snr = 20.0 * np.log10(max(line, 1e-9) / max(floor, 1e-9))
    return line, floor, float(snr)


def analyze_waveform(component_type: str, waveform, sample_rate_hz: int = SAMPLE_RATE_HZ) -> dict:
    """
    Full diagnostic read of one accelerometer trace:
      * time-domain statistics (RMS, peak, crest, kurtosis, skewness)
      * amplitude spectrum + top spectral peaks
      * envelope spectrum (Hilbert demodulation) for impact repetition rates
      * a table of the component's characteristic frequencies with measured
        amplitude, local noise floor, SNR and a detected flag
      * band-energy split and a plain-language diagnosis
    """
    from . import physics as P  # local import keeps signal.py importable on its own

    x = np.asarray(waveform, dtype=float)
    n = x.shape[0]
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    window = np.hanning(n)
    xc = x - x.mean()
    rms = float(np.sqrt(np.mean(xc ** 2)))
    peak = float(np.max(np.abs(xc)))
    safe = max(rms, 1e-9)
    stats = {
        "rms_g": round(rms, 5),
        "peak_g": round(peak, 5),
        "peak_to_peak_g": round(float(xc.max() - xc.min()), 5),
        "crest_factor": round(peak / safe, 3),
        "kurtosis": round(float(np.mean(xc ** 4) / safe ** 4), 3),
        "skewness": round(float(np.mean(xc ** 3) / safe ** 3), 3),
        "duration_ms": round(n / sample_rate_hz * 1000.0, 1),
        "samples": int(n),
    }

    amp = np.abs(np.fft.rfft(xc * window)) * 2.0 / window.sum()
    env = _analytic_envelope(xc)
    env_amp = np.abs(np.fft.rfft((env - env.mean()) * window)) * 2.0 / window.sum()

    power = amp ** 2
    power[0] = 0.0
    total = max(float(power.sum()), 1e-12)
    bands = {"low_0_50hz": (0, 50), "mid_50_200hz": (50, 200), "high_200_500hz": (200, sample_rate_hz / 2)}
    band_energy = {k: round(float(power[(freqs > lo) & (freqs <= hi)].sum() / total * 100.0), 1)
                   for k, (lo, hi) in bands.items()}
    lo, hi = P.FAULT_BANDS_HZ[component_type]
    defect_band_pct = round(float(power[(freqs >= lo) & (freqs <= hi)].sum() / total * 100.0), 1)

    lines = []
    for spec in CHARACTERISTIC_HZ[component_type]:
        src = env_amp if spec["domain"] == "envelope" else amp
        a, floor, snr = _line(freqs, src, spec["hz"], spec["tol_hz"])
        detected = bool(snr >= DETECT_SNR_DB and a > 3 * NOISE_G / np.sqrt(n))
        lines.append({**spec, "amplitude_g": round(a, 5), "noise_floor_g": round(floor, 5),
                      "snr_db": round(snr, 1), "detected": detected,
                      "elevated": bool(detected and a > spec["healthy_g"])})

    # ---- diagnosis -------------------------------------------------------
    th = P.THRESHOLDS[component_type]
    findings: list[str] = []
    level = "normal"

    def bump(to: str) -> None:
        nonlocal level
        order = {"normal": 0, "watch": 1, "alarm": 2}
        if order[to] > order[level]:
            level = to

    def check(feature: str, value: float, text: str) -> None:
        if feature not in th:
            return
        s = P.severity(component_type, feature, value)
        if s >= 1.0:
            findings.append(f"{text} - above the alarm limit ({th[feature].alarm}{th[feature].unit}).")
            bump("alarm")
        elif s >= 0.5:
            findings.append(f"{text} - past half-way to the alarm limit ({th[feature].alarm}{th[feature].unit}).")
            bump("watch")

    check("vib_rms", rms, f"Overall vibration RMS {rms:.3f} g")
    check("vib_kurtosis", stats["kurtosis"], f"Kurtosis {stats['kurtosis']:.1f} (3 = Gaussian; impulsive impacts push it up)")
    check("vib_crest", stats["crest_factor"], f"Crest factor {stats['crest_factor']:.1f} (localised defect indicator)")
    check("fault_band_ratio", defect_band_pct / 100.0, f"{defect_band_pct:.0f}% of vibration energy in the {lo:.0f}-{hi:.0f} Hz defect band")
    for ln in lines:
        where = "envelope spectrum" if ln["domain"] == "envelope" else "spectrum"
        if ln["elevated"]:
            findings.append(f"{ln['name']} at {ln['hz']:.0f} Hz stands {ln['snr_db']:.0f} dB above the floor in the "
                            f"{where} at {ln['amplitude_g']:.3f} g (healthy < {ln['healthy_g']} g): {ln['meaning']}.")
            bump("watch")
        elif ln["detected"]:
            findings.append(f"{ln['name']} at {ln['hz']:.0f} Hz is visible in the {where} but at a normal amplitude "
                            f"({ln['amplitude_g']:.3f} g).")
    if level == "normal":
        findings.insert(0, "No characteristic fault line is elevated and all time-domain statistics are within healthy limits.")

    headline = {
        "normal": "Waveform looks healthy.",
        "watch": "Waveform shows an emerging fault signature - keep this component under observation.",
        "alarm": "Waveform carries a developed fault signature - inspect at the next opportunity.",
    }[level]

    return {
        "component_type": component_type,
        "sample_rate_hz": sample_rate_hz,
        "stats": stats,
        "spectrum": {"freq_hz": [round(float(f), 2) for f in freqs], "amplitude_g": [round(float(v), 6) for v in amp]},
        "envelope_spectrum": {"freq_hz": [round(float(f), 2) for f in freqs], "amplitude_g": [round(float(v), 6) for v in env_amp]},
        "envelope": [round(float(v), 5) for v in env],
        "peaks": _peaks(freqs, amp),
        "envelope_peaks": _peaks(freqs, env_amp, 3),
        "characteristic_lines": lines,
        "band_energy_pct": band_energy,
        "defect_band_pct": defect_band_pct,
        "level": level,
        "headline": headline,
        "findings": findings,
    }
