import os
import shutil
import subprocess
import gradio as gr

def separate_audio(audio_path, high_quality):
    if not audio_path:
        return [None] * 4

    output_dir = "separated"
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    shifts = "2" if high_quality else "1"
    cmd = [
        "demucs",
        "-n", "htdemucs",
        "--shifts", shifts,
        "-o", output_dir,
        audio_path
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error during separation: {e}")
        return [None] * 4

    # फाइल नेम निकालने का बिल्कुल सही तरीका ताकि फोल्डर पाथ न टूटे
    base_filename = os.path.basename(audio_path)
    track_name = os.path.splitext(base_filename)[0]
    
    model_folder = os.path.join(output_dir, "htdemucs", track_name)

    stems = ["vocals.wav", "drums.wav", "bass.wav", "other.wav"]
    output_files = []

    for stem in stems:
        file_path = os.path.join(model_folder, stem)
        if os.path.exists(file_path):
            output_files.append(file_path)
        else:
            print(f"Warning: Missing stem file at {file_path}")
            output_files.append(None)

    return output_files

css = """
footer {visibility: hidden}
.output-audio {margin-top: 10px;}
"""

with gr.Blocks(title="AI Audio Separator") as demo:
    gr.Markdown("# 🎵 AI Audio Stem Separator")
    gr.Markdown("अपनी ऑडियो फाइल अपलोड करें और उसे Vocals, Drums, Bass, और Accompaniment में अलग करें।")
    
    with gr.Row():
        with gr.Column(scale=1):
            input_audio = gr.Audio(label="ऑडियो फाइल अपलोड करें", type="filepath")
            hq_toggle = gr.Checkbox(label="High Quality (Shifts=2, प्रोसेसिंग में थोड़ा समय लगेगा)", value=False)
            submit_btn = gr.Button("ऑडियो अलग करें", variant="primary")
            
        with gr.Column(scale=1):
            gr.Markdown("### 📥 अलग किए गए ट्रैक्स (Stems)")
            out_vocals = gr.Audio(label="🎤 Vocals (आवाज़)", type="filepath")
            out_drums = gr.Audio(label="🥁 Drums (ड्रम्स)", type="filepath")
            out_bass = gr.Audio(label="🎸 Bass (बेस)", type="filepath")
            out_other = gr.Audio(label="🎹 Other (बाकी म्यूज़िक)", type="filepath")

    submit_btn.click(
        fn=separate_audio,
        inputs=[input_audio, hq_toggle],
        outputs=[out_vocals, out_drums, out_bass, out_other]
    )

if __name__ == "__main__":
    # परमानेंट फिक्स: server_name को '127.0.0.1' किया ताकि विंडोज पर सीधे लिंक काम करे
    demo.launch(server_name="127.0.0.1", server_port=7860, css=css)
