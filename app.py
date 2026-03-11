import os
import subprocess
import tempfile
import shutil
import atexit
import json
from flask import Flask, request, send_file, render_template, jsonify
from vosk import Model, KaldiRecognizer
import wave

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit

# -------------------------------------------------------------------
# Load Vosk model once at startup
# -------------------------------------------------------------------
MODEL_PATH = os.environ.get('VOSK_MODEL_PATH', 'model')
if not os.path.exists(MODEL_PATH):
    raise RuntimeError(f"Vosk model not found at {MODEL_PATH}. Please download it.")
model = Model(MODEL_PATH)

# -------------------------------------------------------------------
# Helper: clean up temporary files after response
# -------------------------------------------------------------------
_temp_files = []

def cleanup_temp_files():
    for f in _temp_files:
        try:
            os.remove(f)
        except:
            pass

atexit.register(cleanup_temp_files)

def register_temp_file(path):
    _temp_files.append(path)
    return path

# -------------------------------------------------------------------
# Helper: group words into sentences
# -------------------------------------------------------------------
def group_words_into_sentences(words, max_gap=0.5):
    """Group word timestamps into sentence segments."""
    if not words:
        return []
    sentences = []
    current_words = [words[0]]
    prev_end = words[0]['end']

    for w in words[1:]:
        gap = w['start'] - prev_end
        if current_words[-1]['word'].endswith(('.', '!', '?')) or gap > max_gap:
            sentence_text = ' '.join([wrd['word'] for wrd in current_words])
            sentences.append({
                'start': current_words[0]['start'],
                'end': current_words[-1]['end'],
                'text': sentence_text
            })
            current_words = [w]
        else:
            current_words.append(w)
        prev_end = w['end']

    if current_words:
        sentence_text = ' '.join([wrd['word'] for wrd in current_words])
        sentences.append({
            'start': current_words[0]['start'],
            'end': current_words[-1]['end'],
            'text': sentence_text
        })
    return sentences

# -------------------------------------------------------------------
# Helper: get media duration using ffprobe
# -------------------------------------------------------------------
def get_duration(file_path):
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())

# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/separate', methods=['POST'])
def separate_audio():
    """Extract audio from uploaded video and return as MP3."""
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': 'Empty file'}), 400

    with tempfile.NamedTemporaryFile(delete=False, suffix='_input_video') as tmp_in:
        video_file.save(tmp_in.name)
        input_path = register_temp_file(tmp_in.name)

    output_fd, output_path = tempfile.mkstemp(suffix='.mp3')
    os.close(output_fd)
    register_temp_file(output_path)

    cmd = ['ffmpeg', '-i', input_path, '-vn', '-acodec', 'libmp3lame', '-y', output_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'ffmpeg failed: {e.stderr}'}), 500

    return send_file(output_path, as_attachment=True, download_name='audio.mp3')

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """Transcribe uploaded audio using Vosk, return text, words, and sentences."""
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({'error': 'Empty file'}), 400

    with tempfile.NamedTemporaryFile(delete=False, suffix='_input_audio') as tmp_in:
        audio_file.save(tmp_in.name)
        input_path = register_temp_file(tmp_in.name)

    wav_fd, wav_path = tempfile.mkstemp(suffix='.wav')
    os.close(wav_fd)
    register_temp_file(wav_path)

    convert_cmd = [
        'ffmpeg', '-i', input_path,
        '-ar', '16000', '-ac', '1',
        '-c:a', 'pcm_s16le',
        '-y', wav_path
    ]
    try:
        subprocess.run(convert_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'ffmpeg conversion failed: {e.stderr}'}), 500

    wf = wave.open(wav_path, 'rb')
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != 'NONE':
        return jsonify({'error': 'Audio file must be WAV format mono PCM.'}), 400

    recognizer = KaldiRecognizer(model, wf.getframerate())
    recognizer.SetWords(True)

    results_text = []
    all_words = []

    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if recognizer.AcceptWaveform(data):
            res = json.loads(recognizer.Result())
            if 'text' in res:
                results_text.append(res['text'])
            if 'result' in res:
                all_words.extend(res['result'])

    final_res = json.loads(recognizer.FinalResult())
    if 'text' in final_res:
        results_text.append(final_res['text'])
    if 'result' in final_res:
        all_words.extend(final_res['result'])

    full_text = ' '.join(results_text).strip()
    sentences = group_words_into_sentences(all_words)

    return jsonify({
        'text': full_text,
        'words': all_words,
        'sentences': sentences
    })

@app.route('/merge', methods=['POST'])
def merge_video_audio():
    """Merge uploaded video and audio, adjusting volume."""
    if 'video' not in request.files or 'audio' not in request.files:
        return jsonify({'error': 'Both video and audio files are required'}), 400
    video_file = request.files['video']
    audio_file = request.files['audio']
    if video_file.filename == '' or audio_file.filename == '':
        return jsonify({'error': 'Empty file(s)'}), 400

    volume = request.form.get('volume', '1.0')
    try:
        volume = float(volume)
    except ValueError:
        return jsonify({'error': 'Volume must be a number'}), 400

    with tempfile.NamedTemporaryFile(delete=False, suffix='_video') as tmp_video:
        video_file.save(tmp_video.name)
        video_path = register_temp_file(tmp_video.name)
    with tempfile.NamedTemporaryFile(delete=False, suffix='_audio') as tmp_audio:
        audio_file.save(tmp_audio.name)
        audio_path = register_temp_file(tmp_audio.name)

    out_fd, out_path = tempfile.mkstemp(suffix='_merged.mp4')
    os.close(out_fd)
    register_temp_file(out_path)

    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-i', audio_path,
        '-filter:a', f'volume={volume}',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-shortest',
        '-y', out_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'ffmpeg merge failed: {e.stderr}'}), 500

    return send_file(out_path, as_attachment=True, download_name='merged.mp4')

@app.route('/merge-translated', methods=['POST'])
def merge_translated():
    """
    Merge video with translated audio aligned to original sentence timestamps.
    Expects:
        - video file (field 'video')
        - sentences.json file (field 'sentences_json') containing a JSON array of {start, end}
        - multiple audio files (field 'audio_files') in the same order as the sentences
        - (optional) volume multiplier (field 'volume')
    """
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    if 'sentences_json' not in request.files:
        return jsonify({'error': 'No sentences JSON file provided'}), 400
    if 'audio_files' not in request.files:
        return jsonify({'error': 'No audio files provided'}), 400

    video_file = request.files['video']
    sentences_file = request.files['sentences_json']
    audio_files = request.files.getlist('audio_files')

    if video_file.filename == '':
        return jsonify({'error': 'Empty video file'}), 400
    if sentences_file.filename == '':
        return jsonify({'error': 'Empty sentences JSON file'}), 400
    if len(audio_files) == 0 or any(f.filename == '' for f in audio_files):
        return jsonify({'error': 'Empty audio file(s)'}), 400

    # Optional volume
    volume = request.form.get('volume', '1.0')
    try:
        volume = float(volume)
    except ValueError:
        return jsonify({'error': 'Volume must be a number'}), 400

    # Save video
    with tempfile.NamedTemporaryFile(delete=False, suffix='_video') as tmp_video:
        video_file.save(tmp_video.name)
        video_path = register_temp_file(tmp_video.name)

    # Load and parse sentences JSON
    sentences_data = json.load(sentences_file)
    if not isinstance(sentences_data, list):
        return jsonify({'error': 'Sentences JSON must be an array'}), 400

    # Validate each sentence has start and end
    for s in sentences_data:
        if 'start' not in s or 'end' not in s:
            return jsonify({'error': 'Each sentence must have start and end fields'}), 400

    # Number of sentences must match number of audio files
    if len(sentences_data) != len(audio_files):
        return jsonify({'error': f'Number of sentences ({len(sentences_data)}) does not match number of audio files ({len(audio_files)})'}), 400

    # Save all audio files to temp
    audio_paths = []
    for i, af in enumerate(audio_files):
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'_audio_{i}') as tmp_audio:
            af.save(tmp_audio.name)
            audio_paths.append(register_temp_file(tmp_audio.name))

    # Get total video duration
    try:
        video_duration = get_duration(video_path)
    except Exception as e:
        return jsonify({'error': f'Could not get video duration: {str(e)}'}), 500

    # Verify each audio duration matches the corresponding sentence duration
    for i, audio_path in enumerate(audio_paths):
        try:
            audio_dur = get_duration(audio_path)
            sent_dur = sentences_data[i]['end'] - sentences_data[i]['start']
            # Allow small tolerance (e.g., 0.1 seconds)
            if abs(audio_dur - sent_dur) > 0.1:
                return jsonify({
                    'error': f'Audio duration for sentence {i} ({audio_dur:.2f}s) does not match original duration ({sent_dur:.2f}s)'
                }), 400
        except Exception as e:
            return jsonify({'error': f'Could not get audio duration for sentence {i}: {str(e)}'}), 500

    # Build filter_complex
    # First input is the video (index 0), then all audio inputs (indices 1..N)
    # We'll create a silent base of total video duration, then overlay each delayed audio.
    filter_parts = []
    audio_input_indices = []

    # Generate silence of video duration
    silence_label = 'silence'
    filter_parts.append(f"aevalsrc=0::d={video_duration}[{silence_label}]")

    # For each audio, apply adelay
    delayed_labels = []
    for i, (sentence, audio_path) in enumerate(zip(sentences_data, audio_paths)):
        start_ms = int(sentence['start'] * 1000)  # adelay expects milliseconds
        # adelay format: for each channel (assuming stereo, but we can just use same delay for all)
        # We'll use "|" separated delays; for simplicity, assume 2 channels.
        # We'll detect number of channels? Could use ffprobe but simpler: assume stereo, adelay works for mono too.
        delay_filter = f"[{i+1}:a]adelay={start_ms}|{start_ms}[delayed{i}]"
        filter_parts.append(delay_filter)
        delayed_labels.append(f"[delayed{i}]")

    # Combine all delayed audio with the silence using amix
    # Inputs: silence + all delayed
    all_inputs = f"[{silence_label}]" + "".join(delayed_labels)
    mix_filter = f"{all_inputs}amix=inputs={1+len(delayed_labels)}:duration=longest:dropout_transition=0[audio_out]"
    filter_parts.append(mix_filter)

    # Apply volume adjustment to the final mixed audio
    if volume != 1.0:
        filter_parts.append(f"[audio_out]volume={volume}[audio_out]")

    filter_complex = "; ".join(filter_parts)

    # Build ffmpeg command
    out_fd, out_path = tempfile.mkstemp(suffix='_translated_merged.mp4')
    os.close(out_fd)
    register_temp_file(out_path)

    # Inputs: video first, then all audio files
    cmd = ['ffmpeg', '-i', video_path] + [ '-i', ap for ap in audio_paths ] + [
        '-filter_complex', filter_complex,
        '-map', '0:v:0',
        '-map', '[audio_out]',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-y', out_path
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'ffmpeg merge failed: {e.stderr}'}), 500

    return send_file(out_path, as_attachment=True, download_name='translated_merged.mp4')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
