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


def waveform_features(component_type: str, waves: np.ndarray) -> dict[str, np.ndarray]:
    """Statistical + FFT features for waveforms of shape (n, samples) or (samples,)."""
    w = np.atleast_2d(np.asarray(waves, dtype=float))
    centered = w - w.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(centered**2, axis=1))
    peak = np.max(np.abs(centered), axis=1)
    safe_rms = np.maximum(rms, 1e-9)
    crest = peak / safe_rms
    kurtosis = np.mean(centered**4, axis=1) / safe_rms**4  # Pearson (normal = 3)

    spectrum = np.abs(np.fft.rfft(centered * _WINDOW[None, :], axis=1)) ** 2
    spectrum[:, 0] = 0.0
    total = np.maximum(spectrum.sum(axis=1), 1e-12)
    lo, hi = FAULT_BANDS_HZ[component_type]
    band = (_FREQS >= lo) & (_FREQS <= hi)
    band_ratio = spectrum[:, band].sum(axis=1) / total
    dominant = _FREQS[np.argmax(spectrum, axis=1)]

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
    w = np.asarray(waveform, dtype=float)
    w = w - w.mean()
    amp = np.abs(np.fft.rfft(w * _WINDOW)) * 2.0 / _WINDOW.sum()
    return _FREQS.tolist(), amp.tolist()
