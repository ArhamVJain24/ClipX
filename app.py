import os
import json
import logging
import subprocess
import time
import hashlib
import threading
from flask import Flask, render_template, request, jsonify, Response, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from google import genai
from google.genai import types
import gdown
import uuid
import re

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global active tasks tracker
active_tasks = {}

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024  # 5GB
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

CACHE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(CACHE_FOLDER, exist_ok=True)

def get_cached_results(filename):
    """Return cached results if they exist for this filename, else None."""
    safe_name = secure_filename(filename)
    cache_path = os.path.join(CACHE_FOLDER, f"{safe_name}.json")
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_to_cache(filename, data):
    """Save analysis results to cache."""
    safe_name = secure_filename(filename)
    cache_path = os.path.join(CACHE_FOLDER, f"{safe_name}.json")
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

ALLOWED_EXTENSIONS = {'mp4'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response

@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'video' not in request.files:
            return jsonify({"success": False, "error": "No video part in the request"}), 400
        
        file = request.files['video']
        if file.filename == '':
            return jsonify({"success": False, "error": "No selected file"}), 400
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            logger.info(f"File saved to {filepath}")
            return jsonify({"success": True, "filename": filename})
            
        return jsonify({"success": False, "error": "Invalid file format. Only .mp4 is allowed."}), 400
    except Exception as e:
        logger.error(f"Error in upload: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/task-status/<task_id>', methods=['GET'])
def task_status(task_id):
    task = active_tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "Task not found"}), 404
    return jsonify({"success": True, "task": task})

def download_drive_task(task_id, url, filepath, filename):
    try:
        active_tasks[task_id] = {"status": "downloading"}
        logger.info(f"Downloading from Drive: {url}")
        output = gdown.download(url, filepath, quiet=False)
        
        if not output or not os.path.exists(filepath):
            active_tasks[task_id] = {"status": "error", "error": "Failed to download from Google Drive. Ensure the link is set to 'Anyone with the link can view'."}
            return
            
        logger.info(f"Drive File downloaded to {filepath}")
        active_tasks[task_id] = {"status": "complete", "filename": filename}
    except Exception as e:
        logger.error(f"Error in drive download task: {str(e)}")
        active_tasks[task_id] = {"status": "error", "error": str(e)}

@app.route('/api/upload-drive', methods=['POST'])
def upload_drive():
    try:
        data = request.get_json()
        if not data or 'drive_url' not in data:
            return jsonify({"success": False, "error": "Google Drive URL is required"}), 400
            
        url = data['drive_url']
        
        # generate a secure random filename
        random_id = str(uuid.uuid4())[:8]
        filename = f"drive_upload_{random_id}.mp4"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        task_id = f"task_{uuid.uuid4()}"
        active_tasks[task_id] = {"status": "starting"}
        
        thread = threading.Thread(target=download_drive_task, args=(task_id, url, filepath, filename))
        thread.start()
        
        return jsonify({"success": True, "task_id": task_id})
        
    except Exception as e:
        logger.error(f"Error in drive upload: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

def run_analysis_task(task_id, filename):
    audio_path = None
    try:
        active_tasks[task_id] = {"status": "analyzing"}
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(video_path):
            active_tasks[task_id] = {"status": "error", "error": "File not found"}
            return
        
        # Check cache first
        logger.info(f"Checking cache for {filename}...")
        cached = get_cached_results(filename)
        if cached:
            logger.info(f"Cache HIT for {filename} — returning cached results")
            active_tasks[task_id] = {"status": "complete", "data": cached}
            return
            
        # Get video duration using ffprobe
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        total_duration = 0.0
        try:
            cmd = [ffmpeg_exe, '-i', video_path]
            result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            import re
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
            if match:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = float(match.group(3))
                total_duration = hours * 3600 + minutes * 60 + seconds
        except Exception as e:
            logger.error(f"Error getting duration: {e}")
        
        # Extract audio using ffmpeg in chunks
        filename_without_ext = os.path.splitext(filename)[0]
        audio_path_base = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename_without_ext}_audio")
        
        logger.info(f"Extracting audio chunks to {audio_path_base}_%03d.mp3")
        ffmpeg_cmd = [
            ffmpeg_exe, '-i', video_path, '-vn', '-acodec', 'libmp3lame', 
            '-ar', '16000', '-ac', '1', '-q:a', '6', '-f', 'segment', '-segment_time', '1800', 
            '-reset_timestamps', '1', '-y', f"{audio_path_base}_%03d.mp3"
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Upload chunks and analyze
        import glob
        audio_chunks = sorted(glob.glob(f"{audio_path_base}_*.mp3"))
        all_moments = []
        
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            active_tasks[task_id] = {"status": "error", "error": "GEMINI_API_KEY is not set"}
            return
            
        client = genai.Client(api_key=api_key)
        
        for i, chunk_path in enumerate(audio_chunks):
            chunk_offset = i * 1800
            logger.info(f"Uploading chunk {i+1}/{len(audio_chunks)}: {chunk_path}")
            audio_file = client.files.upload(file=chunk_path)
            
            prompt = f"""
            You are ClipX, an expert video content analyst. This is chunk {i+1} of a long video.

            TASK: Act as a hyper-curated filter. Listen to the ENTIRE audio file from start to finish.
            
            CRITICAL INSTRUCTION 1: Search for the absolute most valuable, insightful, or entertaining viral clips across the ENTIRE duration of this audio chunk.
            CRITICAL INSTRUCTION 2: Do NOT stop early. You MUST process the chunk until the very end. 
            CRITICAL INSTRUCTION 3: You MUST extract AT LEAST 4 to 6 of the absolute best clips from this specific chunk. Even if the video is boring, you must find the best 4-6 moments.

            For each highly valuable segment, score:
            insight_score (0-100), evaluate based on BOTH mechanical facts and social media psychology (sum all that apply):
            # Mechanical Value
            - Contains a specific, concrete claim, number, or fact (+15)
            - Offers an actionable takeaway or practical advice (+15)
            - Self-contained and quotable without extra context (+15)
            # Psychological Virality
            - The "Scroll-Stopper" Hook (+20): Starts with a highly controversial, shocking, or counter-intuitive statement.
            - High Emotional Valence (+15): Evokes a strong emotional reaction (inspiration, anger, shock, intense laughter).
            - The "Aha!" Moment (+20): Connects unrelated dots or reveals a profound, high-level insight.
            # Holistic Context & Intelligence
            - Deep Semantic Importance (+30): perfectly articulates a universal pain point, or is a "golden nugget" of wisdom.

            reaction_score (0-100), based ONLY on audible audio, never inferred from visuals or assumed from context:
            - Audible applause: +40
            - Audible laughter: +30
            - Audible verbal agreement/cheering from someone other than the speaker: +30
            If nothing is audibly present, reaction_score MUST be 0.

            CRITICAL LANGUAGE RULE: If the audio is in Hindi or Hinglish, transliterate the `quote` using the English alphabet. Do NOT translate the meaning.

            You MUST return ONLY a valid JSON array of objects matching this exact structure:
            [
              {{
                "start_seconds": <number>,
                "end_seconds": <number>,
                "quote": "<string>",
                "summary": "<string>",
                "insight_score": <integer>,
                "reaction_score": <integer>,
                "tags": ["<string>"],
                "has_reaction": <boolean>
              }}
            ]
            
            Return ONLY the JSON array. Do not include any other text or formatting.
            """
            
            models_to_try = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
            response = None
            max_retries = 4
            
            for attempt in range(max_retries):
                fallback_model = models_to_try[attempt % len(models_to_try)]
                try:
                    logger.info(f"Calling Gemini API on Chunk {i+1} with {fallback_model} (Attempt {attempt+1}/{max_retries})")
                    response = client.models.generate_content(
                        model=fallback_model,
                        contents=[
                            types.Content(
                                parts=[
                                    types.Part.from_uri(file_uri=audio_file.uri, mime_type=audio_file.mime_type),
                                    types.Part.from_text(text=prompt),
                                ]
                            )
                        ],
                        config=types.GenerateContentConfig(temperature=0.0, top_p=1.0, top_k=1, response_mime_type="application/json")
                    )
                    break
                except Exception as e:
                    wait_time = (2 ** attempt) * 5  # 5s, 10s, 20s, 40s
                    logger.warning(f"API Error with {fallback_model}: {e}. Retrying in {wait_time}s...")
                    if attempt < max_retries - 1:
                        time.sleep(wait_time)
            
            if response:
                response_text = response.text.strip()
                import re
                match = re.search(r'\[.*\]', response_text, re.DOTALL)
                if match:
                    response_text = match.group(0)
                
                try:
                    clips = json.loads(response_text)
                    for m in clips:
                        try:
                            start_sec = float(m.get('start_seconds', 0)) + chunk_offset
                            end_sec = float(m.get('end_seconds', 0)) + chunk_offset
                            if start_sec > total_duration or start_sec < 0: continue
                            if end_sec > total_duration: end_sec = total_duration
                            if start_sec >= end_sec: continue
                            
                            m['start_seconds'] = start_sec
                            m['end_seconds'] = end_sec
                            
                            insight = int(m.get('insight_score', 0))
                            reaction = int(m.get('reaction_score', 0))
                            total = min(insight + reaction, 100)
                            m['score'] = total
                            
                            if total >= 40:
                                all_moments.append(m)
                        except:
                            continue
                except Exception as e:
                    logger.error(f"Failed to parse JSON for chunk {i+1}: {e}")
            
            # Clean up the file from Gemini API storage
            try:
                client.files.delete(name=audio_file.name)
            except:
                pass
            
            # Clean up local chunk
            try:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
            except:
                pass

        # Sort and select top moments
        all_moments.sort(key=lambda x: x.get('score', 0), reverse=True)
        ideal_cap = int(total_duration / 60) if total_duration > 0 else 15
        dynamic_cap = max(10, min(30, ideal_cap))
        moments = all_moments[:dynamic_cap]
            
        def format_time(seconds):
            hours = int(seconds) // 3600
            mins = (int(seconds) % 3600) // 60
            secs = int(seconds) % 60
            if hours > 0:
                return f"{hours}:{mins:02d}:{secs:02d}"
            else:
                return f"{mins:02d}:{secs:02d}"
            
        for moment in moments:
            start_fmt = format_time(moment.get('start_seconds', 0))
            end_fmt = format_time(moment.get('end_seconds', 0))
            moment['timestamp_display'] = f"{start_fmt} — {end_fmt}"
            
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
                logger.info(f"Cleaned up {audio_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up audio file: {str(e)}")
            
        result_data = {
            "success": True, 
            "moments": moments, 
            "total_duration": total_duration
        }
        
        save_to_cache(filename, result_data)
        logger.info(f"Cache SAVED for {filename}")
        
        active_tasks[task_id] = {"status": "complete", "data": result_data}
        
    except Exception as e:
        logger.error(f"Error in analyze_task: {str(e)}")
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except:
            pass
        active_tasks[task_id] = {"status": "error", "error": str(e)}

@app.route('/api/analyze', methods=['POST'])
def analyze_video():
    data = request.get_json()
    if not data or 'filename' not in data:
        return jsonify({"success": False, "error": "Filename is required"}), 400
        
    task_id = f"task_{uuid.uuid4()}"
    active_tasks[task_id] = {"status": "starting"}
    
    thread = threading.Thread(target=run_analysis_task, args=(task_id, data['filename']))
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id})

@app.route('/api/results/<filename>', methods=['GET'])
def get_api_results(filename):
    cached = get_cached_results(filename)
    if cached:
        return jsonify(cached)
    return jsonify({"success": False, "error": "Results not found for this video. It may not have been analyzed yet."}), 404

@app.route('/api/video/<filename>')
def get_video(filename):
    try:
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(video_path):
            return "File not found", 404
            
        file_size = os.path.getsize(video_path)
        range_header = request.headers.get('Range', None)
        
        if range_header:
            range_match = range_header.strip().replace('bytes=', '').split('-')
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if len(range_match) > 1 and range_match[1] else file_size - 1
            
            length = end - start + 1
            
            with open(video_path, 'rb') as f:
                f.seek(start)
                data = f.read(length)
                
            response = Response(data, 206, mimetype='video/mp4', content_type='video/mp4', direct_passthrough=True)
            response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
            response.headers.add('Accept-Ranges', 'bytes')
            response.headers.add('Content-Length', str(length))
            return response
        else:
            return send_file(video_path, mimetype='video/mp4')
            
    except Exception as e:
        logger.error(f"Error serving video: {str(e)}")
        return str(e), 500

@app.route('/results/<filename>')
def results(filename):
    return render_template('results.html', filename=filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
