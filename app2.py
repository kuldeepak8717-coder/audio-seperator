"""AI Studio Audio Splitter Pro Max — batch, all models, all formats."""

from __future__ import annotations

import os
import re
import traceback
from pathlib import Path

import gradio as gr

import separator_core as core

CSS = """
footer {visibility: hidden}
.output-audio {margin-top: 12px;}
"""

MODE_LOCAL = "Local File(s)"
MODE_YOUTUBE = "YouTube Link(s)"
MODE_BOTH = "Both"
INPUT_MODES = [MODE_LOCAL, MODE_YOUTUBE, MODE_BOTH]

MAX_ITEMS = 20


def _file_path(item) -> str | None:
    """Normalise a Gradio File value to a plain path."""
    if item is None:
        return None
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("path") or item.get("name") or item.get("url")
    return getattr(item, "path", None) or getattr(item, "name", None)


def _md(value) -> str:
    """Make a value safe inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _build_summary(
    results: list[core.SeparationResult], errors: list[tuple[str, str]]
) -> str:
    lines = [
        "### ✅ Results",
        "",
        "| # | Track | Model | Format | Stems | Status |",
        "|---|-------|-------|--------|-------|--------|",
    ]
    for index, result in enumerate(results, 1):
        lines.append(
            f"| {index} | {_md(result.track_name)} | "
            f"`{_md(result.model_name)}` | {_md(result.output_format)} | "
            f"{result.stem_count} | ✅ Done |"
        )
    for label, error in errors:
        short = error if len(error) <= 240 else error[:237] + "..."
        lines.append(f"| – | {_md(label)} | – | – | – | ❌ {_md(short)} |")
    if errors:
        lines.append("")
        lines.append(
            f"⚠️ **{len(errors)} item(s) failed.** पूरा error log नीचे देखें."
        )
    return "\n".join(lines)


def process_audio(
    local_files,
    youtube_urls,
    input_mode,
    model_type,
    device,
    quality_shifts,
    overlap_value,
    segment_value,
    output_format,
    make_mixes,
    progress=gr.Progress(),
):
    """Process every local file / YouTube URL and return 12 outputs."""
    log_lines: list[str] = []

    def log(message: str) -> None:
        log_lines.append(message)
        print(message)

    def download_progress(index: int, total: int, prefix: str):
        def callback(fraction, text):
            try:
                fraction = float(fraction)
            except (TypeError, ValueError):
                fraction = 0.0
            fraction = min(max(fraction, 0.0), 1.0)
            # core.download_youtube_audio emits 0.04 -> 0.12
            local = min(max((fraction - 0.04) / 0.08, 0.0), 1.0) * 0.2
            progress((index + local) / total, desc=f"{prefix} {text}")

        return callback

    def separation_progress(index: int, total: int, prefix: str, base: float):
        span = 1.0 - base

        def callback(fraction, text):
            try:
                fraction = float(fraction)
            except (TypeError, ValueError):
                fraction = 0.0
            fraction = min(max(fraction, 0.0), 1.0)
            # core.separate emits 0.10 -> 1.0
            local = base + min(max((fraction - 0.10) / 0.90, 0.0), 1.0) * span
            progress((index + local) / total, desc=f"{prefix} {text}")

        return callback

    try:
        sources: list[tuple[str, str, bool]] = []

        if input_mode in (MODE_LOCAL, MODE_BOTH):
            files = local_files or []
            if isinstance(files, (str, dict)):
                files = [files]
            for item in files:
                path = _file_path(item)
                if path:
                    sources.append((Path(path).name, path, False))

        if input_mode in (MODE_YOUTUBE, MODE_BOTH):
            text = youtube_urls or ""
            urls = [part.strip() for part in re.split(r"[\s,]+", text) if part.strip()]
            seen_urls: set[str] = set()
            for url in urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    sources.append((url, url, True))

        # Remove duplicate local files.
        deduped: list[tuple[str, str, bool]] = []
        seen_paths: set[str] = set()
        for label, source, is_url in sources:
            if not is_url:
                key = str(Path(source).resolve()).lower()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
            deduped.append((label, source, is_url))
        sources = deduped

        if not sources:
            raise core.SeparationError(
                "कोई input नहीं मिला / No input provided.\n"
                "Files अपलोड करें या YouTube URL डालें."
            )
        if len(sources) > MAX_ITEMS:
            raise core.SeparationError(
                f"एक बार में अधिकतम {MAX_ITEMS} items process हो सकते हैं. "
                f"अभी {len(sources)} हैं."
            )

        total = len(sources)
        results: list[core.SeparationResult] = []
        errors: list[tuple[str, str]] = []

        for index, (label, source, is_url) in enumerate(sources):
            prefix = f"[{index + 1}/{total}]"
            try:
                if is_url:
                    log(f"{prefix} YouTube: {source}")
                    audio_path = core.download_youtube_audio(
                        source,
                        on_progress=download_progress(index, total, prefix),
                    )
                    label = Path(audio_path).stem
                    log(f"{prefix} Downloaded: {Path(audio_path).name}")
                else:
                    audio_path = source
                    log(f"{prefix} File: {label}")

                result = core.separate(
                    audio_path,
                    model_name=model_type,
                    shifts=quality_shifts,
                    overlap=overlap_value,
                    output_format=output_format,
                    make_mixes=bool(make_mixes),
                    device=device,
                    segment=segment_value,
                    on_progress=separation_progress(
                        index, total, prefix, 0.2 if is_url else 0.0
                    ),
                )
                results.append(result)
                log(
                    f"{prefix} ✅ {result.track_name}: "
                    f"{result.stem_count} stems ({result.output_format})"
                )
            except core.SeparationError as exc:
                errors.append((label, str(exc)))
                log(f"{prefix} ❌ {label}: {exc}")
            except Exception as exc:  # keep batch running even if one file dies
                errors.append((label, f"Unexpected error: {exc}"))
                log(f"{prefix} ❌ {label}: unexpected error: {exc}")
            progress((index + 1) / total, desc=f"{prefix} finished")

        if not results:
            raise gr.Error(
                "कोई भी item process नहीं हो सका:\n\n"
                + "\n".join(f"- {name}: {error}" for name, error in errors)
            )

        first = results[0]
        stems = first.stems

        if len(results) == 1:
            zip_path = first.zip_path
        else:
            zip_path = core.package_results(results, "audio_separator_batch")
        if not zip_path:
            zip_path = core.package_results(results, "audio_separator_batch")

        preview = f"### 📥 Extracted Stems — `{_md(first.track_name)}`"
        if len(results) > 1:
            preview += (
                f"\n<sub>Preview shows the first track. All {len(results)} "
                "tracks are inside the ZIP.</sub>"
            )

        summary = _build_summary(results, errors)
        log_text = "\n".join(log_lines) or "No log output."

        return (
            zip_path,
            preview,
            stems.get("vocals"),
            stems.get("drums"),
            stems.get("bass"),
            stems.get("piano"),
            stems.get("guitar"),
            stems.get("other"),
            stems.get("instrumental"),
            stems.get("full_mix"),
            summary,
            log_text,
        )

    except gr.Error:
        raise
    except core.SeparationError as exc:
        raise gr.Error(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - last-resort safety net
        traceback.print_exc()
        raise gr.Error(f"Unexpected error: {exc}") from exc


with gr.Blocks(
    title="AI Studio Audio Splitter Pro Max",
    delete_cache=(86400, 86400),
) as demo:
    gr.Markdown("# 🎛️ AI Studio Audio Splitter Pro Max")
    gr.Markdown(
        "**11 AI models · 6 stems · Instrumental + Full Mix · Batch processing · "
        "WAV/FLAC/MP3 · CPU/GPU**\n\n"
        "Upload one or many files, or paste YouTube links (one per line). "
        "The ZIP contains every stem for every track."
    )

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 📂 Input")
            input_mode = gr.Radio(
                choices=INPUT_MODES,
                value=MODE_LOCAL,
                label="Input source",
            )
            local_files = gr.File(
                label="Upload audio/video files (one or many)",
                file_count="multiple",
                type="filepath",
                visible=True,
            )
            youtube_urls = gr.Textbox(
                label="YouTube URLs (one per line)",
                lines=4,
                placeholder=(
                    "https://www.youtube.com/watch?v=...\n"
                    "https://youtu.be/..."
                ),
                visible=False,
            )

            def toggle_inputs(choice):
                local = choice in (MODE_LOCAL, MODE_BOTH)
                youtube = choice in (MODE_YOUTUBE, MODE_BOTH)
                return gr.update(visible=local), gr.update(visible=youtube)

            input_mode.change(
                fn=toggle_inputs,
                inputs=[input_mode],
                outputs=[local_files, youtube_urls],
            )

            gr.Markdown("### ⚙️ Engine")
            model_selection = gr.Dropdown(
                choices=core.model_choices(),
                value="htdemucs_6s",
                label="AI Model",
                info=(
                    "htdemucs_6s gives 6 stems. htdemucs_ft is the highest "
                    "quality 4-stem model but about 4x slower."
                ),
            )
            device_selection = gr.Dropdown(
                choices=core.available_device_choices(),
                value="Auto",
                label="Device",
                info="Auto uses CUDA when available, otherwise CPU.",
            )
            format_selection = gr.Dropdown(
                choices=core.FORMAT_CHOICES,
                value=core.FORMAT_WAV,
                label="Output format",
            )
            shifts_value = gr.Slider(
                minimum=1,
                maximum=8,
                step=1,
                value=2,
                label="Quality Shifts",
                info=(
                    "Higher = cleaner separation but slower. "
                    "1–2 is best on CPU."
                ),
            )
            overlap_value = gr.Slider(
                minimum=0.1,
                maximum=0.5,
                step=0.05,
                value=0.25,
                label="Overlap Ratio",
                info="0.25 is the standard optimum.",
            )
            segment_value = gr.Slider(
                minimum=0,
                maximum=8,
                step=1,
                value=0,
                label="Segment size (seconds)",
                info=(
                    "0 = automatic. Integer seconds; Demucs clamps to the "
                    "model's maximum automatically."
                ),
            )
            make_mixes = gr.Checkbox(
                value=True,
                label="Instrumental + Full Mix भी बनाएँ (extra stems)",
            )

            submit_btn = gr.Button("🚀 Separate Everything", variant="primary")

        with gr.Column(scale=1):
            gr.Markdown("### 📥 Output")
            zip_out = gr.File(label="📦 Download ZIP (all stems + mixes)")

            preview_label = gr.Markdown("### 📥 Extracted Stems")

            wf_opts = gr.WaveformOptions(
                sample_rate=44100,
                waveform_color="#9ca3af",
                waveform_progress_color="#3b82f6",
            )

            with gr.Accordion("🎧 Preview (first track)", open=True):
                vocals_out = gr.Audio(
                    label="🎤 Vocals",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                drums_out = gr.Audio(
                    label="🥁 Drums",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                bass_out = gr.Audio(
                    label="🎸 Bass",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                piano_out = gr.Audio(
                    label="🎹 Piano",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                guitar_out = gr.Audio(
                    label="🎸 Guitar",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                other_out = gr.Audio(
                    label="🎼 Other",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                instrumental_out = gr.Audio(
                    label="🎼 Instrumental (vocals removed)",
                    type="filepath",
                    waveform_options=wf_opts,
                )
                full_mix_out = gr.Audio(
                    label="🎚️ Full Mix (reconstructed)",
                    type="filepath",
                    waveform_options=wf_opts,
                )

            summary_out = gr.Markdown("Results will appear here.")
            log_out = gr.Textbox(
                label="Processing log",
                lines=8,
                max_lines=20,
                interactive=False,
            )

    gr.Markdown(
        f"<sub>📁 Generated files: <code>{core.RUNTIME_DIR}</code> "
        "(auto-cleaned after 24 hours). "
        "4-stem models leave Piano/Guitar empty; "
        "the ZIP always contains every available stem.</sub>"
    )

    submit_btn.click(
        fn=process_audio,
        inputs=[
            local_files,
            youtube_urls,
            input_mode,
            model_selection,
            device_selection,
            shifts_value,
            overlap_value,
            segment_value,
            format_selection,
            make_mixes,
        ],
        outputs=[
            zip_out,
            preview_label,
            vocals_out,
            drums_out,
            bass_out,
            piano_out,
            guitar_out,
            other_out,
            instrumental_out,
            full_mix_out,
            summary_out,
            log_out,
        ],
    )


def main() -> None:
    demo.queue(default_concurrency_limit=1, max_size=16)
    launch_kwargs = {
        "server_name": "127.0.0.1",
        "show_error": True,
        "inbrowser": os.environ.get(
            "AUDIO_SEPARATOR_OPEN_BROWSER", "1"
        ).lower() not in {"0", "false", "no"},
        "allowed_paths": [str(core.RUNTIME_DIR)],
        "footer_links": [],
        "theme": gr.themes.Soft(),
        "css": CSS,
    }
    # server_port is omitted: Gradio starts at 7860 and picks the next free
    # port automatically if 7860 is already in use.
    demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()
