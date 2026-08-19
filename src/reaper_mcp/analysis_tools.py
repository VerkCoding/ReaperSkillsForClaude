import os
import logging

import numpy as np
import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.analysis_tools")


# STFT size used for band measurement, and the window it implies.
_N_FFT = 2048


def _nothing_to_measure():
    """Return an error dict when the project holds no audio, otherwise None.

    Rendering an empty project produces no file and leaves REAPER showing a render
    error, so the emptiness is reported directly instead.
    """
    if RPR.GetProjectLength(0) <= 0:
        return {
            "success": False,
            "error": "The project is empty. There is nothing to render or measure.",
        }
    return None


def _band_rms_db(D: np.ndarray, freqs: np.ndarray, lo: float, hi: float, enbw: float) -> float:
    """Return the RMS level of one frequency band in dBFS.

    D holds amplitude-normalised STFT magnitudes, so each bin carries the amplitude of
    the component it represents. Power is summed across the bins of the band rather
    than averaged: averaging made the reading depend on how many bins the band spans,
    and unnormalised magnitudes placed a -6 dBFS tone at +31 dB. Dividing by the
    window's equivalent noise bandwidth removes the energy a Hann window spreads into
    neighbouring bins. Summing every band of a signal now reproduces its overall RMS.
    """
    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        return -120.0
    power = float(np.mean(np.sum((D[mask, :] / np.sqrt(2)) ** 2, axis=0) / enbw))
    return float(10 * np.log10(power + 1e-12))


def register_tools(mcp):

    @mcp.tool()
    def analyze_frequency_spectrum() -> dict:
        """
        Renders the project and measures RMS level in dB across seven frequency bands.
        """
        try:
            import librosa
            import soundfile as sf
            from reaper_mcp.render_tools import render_to_temp_file

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                y, sr = librosa.load(tmp, sr=None, mono=True)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            # Magnitudes are scaled to component amplitude so the reported levels are
            # in dBFS rather than raw STFT units.
            window = np.hanning(_N_FFT + 1)[:-1]
            enbw = float(_N_FFT * np.sum(window ** 2) / (np.sum(window) ** 2))
            D = np.abs(librosa.stft(y, n_fft=_N_FFT)) * (2.0 / np.sum(window))
            freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)

            bands = {
                "sub_bass":   (20,   60),
                "bass":       (60,   250),
                "low_mids":   (250,  500),
                "mids":       (500,  2000),
                "high_mids":  (2000, 4000),
                "presence":   (4000, 8000),
                "brilliance": (8000, min(20000, sr // 2)),
            }

            results = {
                name: {
                    "range_hz": f"{lo}-{hi}",
                    "level_db": round(_band_rms_db(D, freqs, lo, hi, enbw), 1),
                }
                for name, (lo, hi) in bands.items()
            }

            return {
                "success": True,
                "frequency_bands": results,
                "note": "level_db is the RMS level of the band in dBFS.",
            }
        except Exception as e:
            logger.error(f"analyze_frequency_spectrum failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def detect_clipping() -> dict:
        """
        Renders the project and detects samples at or above 0 dBFS.
        """
        try:
            import soundfile as sf
            from reaper_mcp.render_tools import render_to_temp_file

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if data.ndim > 1:
                mono = np.max(np.abs(data), axis=1)
            else:
                mono = np.abs(data)

            clip_threshold = 0.9999
            clipped_samples = int(np.sum(mono >= clip_threshold))
            peak_linear = float(np.max(mono))
            peak_db = float(20 * np.log10(peak_linear)) if peak_linear > 0 else -120.0

            return {
                "success": True,
                "clipping_detected": clipped_samples > 0,
                "clipped_samples": clipped_samples,
                "peak_db": round(peak_db, 2),
                "peak_linear": round(peak_linear, 4),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_dynamics() -> dict:
        """
        Renders the project and measures RMS, peak, crest factor, and dynamic range score.
        """
        try:
            import soundfile as sf
            from reaper_mcp.render_tools import render_to_temp_file

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            # Measured across the channels rather than on their sum. Summing to mono
            # cancels out-of-phase material, which reported a -6 dBFS anti-phase mix as
            # -120 dB silence.
            samples = data if data.ndim > 1 else data[:, None]
            rms = float(np.sqrt(np.mean(samples ** 2)))
            peak = float(np.max(np.abs(samples)))
            rms_db = float(20 * np.log10(rms)) if rms > 0 else -120.0
            peak_db = float(20 * np.log10(peak)) if peak > 0 else -120.0
            crest_db = peak_db - rms_db

            # Dynamic range is scored over 3-second windows to reflect sustained
            # dynamics. Material shorter than one window fits no block at all, which
            # previously reported a score of 0.0 as though the range had been measured.
            block_size = int(rate * 3)
            n_blocks = len(samples) // block_size if block_size else 0
            if n_blocks:
                blocks = [samples[i * block_size:(i + 1) * block_size] for i in range(n_blocks)]
                dr_window = f"{n_blocks} window(s) of 3s"
            else:
                blocks = [samples]
                dr_window = "whole render, shorter than one 3s window"

            dr_scores = []
            for block in blocks:
                blk_peak = float(np.max(np.abs(block)))
                blk_rms = float(np.sqrt(np.mean(block ** 2)))
                if blk_rms > 0 and blk_peak > 0:
                    dr_scores.append(float(20 * np.log10(blk_peak / blk_rms)))
            dr = float(np.mean(dr_scores)) if dr_scores else None

            return {
                "success": True,
                "rms_db": round(rms_db, 1),
                "peak_db": round(peak_db, 1),
                "crest_factor_db": round(crest_db, 1),
                "dr_score": round(dr, 1) if dr is not None else None,
                "dr_measured_over": dr_window,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_stereo_field() -> dict:
        """
        Renders the project and measures stereo width and mono compatibility.
        """
        try:
            import soundfile as sf
            from reaper_mcp.render_tools import render_to_temp_file

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if data.ndim < 2 or data.shape[1] < 2:
                return {"success": False, "error": "Project rendered as mono. Stereo field analysis is unavailable."}

            L, R = data[:, 0], data[:, 1]
            mid = (L + R) / 2
            side = (L - R) / 2
            mid_rms = float(np.sqrt(np.mean(mid ** 2)))
            side_rms = float(np.sqrt(np.mean(side ** 2)))

            # With a silent mid channel the ratio divides by the guard constant and
            # returns billions, which reads as a measurement. Fully out-of-phase
            # material is reported as such instead.
            if mid_rms < 1e-6:
                width_ratio = None
                width_note = "mid channel is silent: the channels are opposite in phase"
            else:
                width_ratio = round(side_rms / mid_rms, 3)
                width_note = None

            # corrcoef returns NaN for a channel that never changes, and NaN cannot be
            # represented in JSON.
            if float(np.std(L)) == 0.0 or float(np.std(R)) == 0.0:
                correlation = None
            else:
                correlation = round(float(np.corrcoef(L, R)[0, 1]), 3)

            return {
                "success": True,
                "stereo_width_ratio": width_ratio,
                "width_note": width_note,
                "lr_correlation": correlation,
                "mid_rms_db": round(float(20 * np.log10(mid_rms + 1e-10)), 1),
                "side_rms_db": round(float(20 * np.log10(side_rms + 1e-10)), 1),
                "mono_compatible": correlation > 0.0 if correlation is not None else None,
                "notes": "width_ratio: 0.0 = mono, >0.5 = stereo. lr_correlation: 1.0 = mono, 0.0 = stereo, <0.0 = phase issues.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_transients() -> dict:
        """
        Renders the project and detects transient onset events.
        """
        try:
            import librosa
            from reaper_mcp.render_tools import render_to_temp_file

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file(sample_rate=44100)
            try:
                y, sr = librosa.load(tmp, sr=None, mono=True)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
            onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()
            capped = onset_times[:100]

            return {
                "success": True,
                "onset_count": len(onset_times),
                "onset_times_seconds": [round(t, 3) for t in capped],
                "note": "Output limited to 100 events." if len(onset_times) > 100 else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
