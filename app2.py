import os
import sys
import shutil
import zipfile
import subprocess
import gradio as gr
from pydub import AudioSegment
import yt_dlp

def download_youtube_audio(url):
    """Downloads audio from a YouTube link and returns the local file path."""
    if not url:
        return None
    
    download_dir = "downloads"
    os.makedirs(download_dir, exist_ok=True)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # Fix extension reference after ffmpeg postprocessing conversion
        wav_filename = os.path.splitext(filename)[0] + ".wav"
        return wav_filename

def convert_to_mp3(file_path):
    """Converts a WAV file to MP3 format to save storage space."""
    if not file_path or not os.path.exists(file_path):
        return None
    mp3_path = os.path.splitext(file_path)[0] + ".mp3"
    try:
        audio = AudioSegment.from_wav(file_path)
        audio.export(mp3_path, format="mp3", bitrate="320k")
        return mp3_path
    except Exception as e:
        print(f"Format conversion error: {e}")
        return file_path

def process_audio(input_file, yt_url, input_type, model_type, quality_shifts, overlap_value, output_format):
    audio_path = None
    if input_type == "Local File Upload":
        audio_path = input_file
    elif input_type == "YouTube Link":
        try:
            audio_path = download_youtube_audio(yt_url)
        except Exception as e:
            print(f"YouTube Download Error: {e}")
            return [None] * 7

    if not audio_path or not os.path.exists(audio_path):
        return [None] * 7

    output_dir = "separated"
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs",
        "-n", model_type,
        "--shifts", str(quality_shifts),
        "--overlap", str(overlap_value),
        "-o", output_dir,
        audio_path
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Demucs Separation Error: {e}")
        return [None] * 7

    base_filename = os.path.basename(audio_path)
    # FIXED: Added [0] index accessor to capture clean string name instead of tuple
    track_name = os.path.splitext(base_filename)[0]
    model_folder = os.path.join(output_dir, model_type, track_name)

    stems = ["vocals.wav", "drums.wav", "bass.wav", "piano.wav", "guitar.wav", "other.wav"]
    processed_stems = {}

    for stem in stems:
        file_path = os.path.join(model_folder, stem)
        if os.path.exists(file_path):
            if output_format == "MP3 (320kbps High Quality)":
                processed_stems[stem] = convert_to_mp3(file_path)
            else:
                processed_stems[stem] = file_path
        else:
            processed_stems[stem] = None

    zip_path = os.path.join(output_dir, f"{track_name}_all_stems.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for stem, path in processed_stems.items():
            if path and os.path.exists(path):
                zipf.write(path, os.path.basename(path))

    return [
        processed_stems["vocals.wav"],
        processed_stems["drums.wav"],
        processed_stems["bass.wav"],
        processed_stems["piano.wav"],
        processed_stems["guitar.wav"],
        processed_stems["other.wav"],
        zip_path if os.path.exists(zip_path) else None
    ]

css = """
footer {visibility: hidden}
.output-audio {margin-top: 12px;}
"""

with gr.Blocks(title="AI Studio Audio Splitter Pro") as demo:
    gr.Markdown("# 🎛️ AI Studio Audio Splitter Pro")
    gr.Markdown("Separate vocals and musical instruments using advanced AI algorithms directly inside your browser.")

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 📂 Source Configuration")
            input_mode = gr.Radio(
                choices=["Local File Upload", "YouTube Link"], 
                value="Local File Upload", 
                label="Choose Audio Input Method"
            )
            
            upload_field = gr.Audio(label="Upload Audio File", type="filepath", visible=True)
            youtube_field = gr.Textbox(label="Paste YouTube URL", placeholder="https://youtube.com...", visible=False)
            
            def toggle_inputs(choice):
                if choice == "Local File Upload":
                    return gr.update(visible=True), gr.update(visible=False)
                return gr.update(visible=False), gr.update(visible=True)
            
            input_mode.change(fn=toggle_inputs, inputs=[input_mode], outputs=[upload_field, youtube_field])

            gr.Markdown("### ⚙️ Engine Settings")
            model_selection = gr.Dropdown(
                choices=["htdemucs", "htdemucs_6s"], 
                value="htdemucs_6s", 
                label="AI Engine Architecture Model",
                info="htdemucs split 4 stems. htdemucs_6s adds dedicated Piano & Guitar stems."
            )
            shifts_value = gr.Slider(
                minimum=1, maximum=8, step=1, value=4, 
                label="Quality Shifts", 
                info="Higher values improve separation depth but increase processing times."
            )
            overlap_value = gr.Slider(
                minimum=0.1, maximum=0.5, step=0.05, value=0.25, 
                label="Overlap Ratio", 
                info="Controls the structural smoothness between spliced structural frames."
            )
            format_selection = gr.Dropdown(
                choices=["WAV (Lossless Audio Source)", "MP3 (320kbps High Quality)"], 
                value="WAV (Lossless Audio Source)", 
                label="Output Audio Format Type"
            )
            
            submit_btn = gr.Button("🚀 Begin Deep Extraction", variant="primary")

        with gr.Column(scale=1):
            gr.Markdown("### 📥 Extracted Master Stems")
            
            wf_opts = gr.WaveformOptions(sample_rate=44100, waveform_color="#9ca3af", waveform_progress_color="#3b82f6")
            
            zip_out = gr.File(label="📦 Download All Stems (.ZIP Package)")
            
            vocals_out = gr.Audio(label="🎤 Lead & Backing Vocals", type="filepath", waveform_options=wf_opts)
            drums_out = gr.Audio(label="🥁 Percussion & Drums", type="filepath", waveform_options=wf_opts)
            bass_out = gr.Audio(label="🎸 Bassline Synth/Guitar", type="filepath", waveform_options=wf_opts)
            piano_out = gr.Audio(label="🎹 Classical Piano Keys", type="filepath", waveform_options=wf_opts)
            guitar_out = gr.Audio(label="🎸 Acoustic/Electric Guitars", type="filepath", waveform_options=wf_opts)
            other_out = gr.Audio(label="🎼 Miscellaneous Accompaniment (Other)", type="filepath", waveform_options=wf_opts)

    submit_btn.click(
        fn=process_audio,
        inputs=[upload_field, youtube_field, input_mode, model_selection, shifts_value, overlap_value, format_selection],
        outputs=[vocals_out, drums_out, bass_out, piano_out, guitar_out, other_out, zip_out]
    )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, css=css)
