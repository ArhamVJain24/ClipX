import os
import json
import logging
import subprocess
import time
import hashlib
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
        
        logger.info(f"Downloading from Drive: {url}")
        
        output = gdown.download(url, filepath, quiet=False)
        
        if not output or not os.path.exists(filepath):
            return jsonify({"success": False, "error": "Failed to download from Google Drive. Ensure the link is set to 'Anyone with the link can view'."}), 400
            
        logger.info(f"Drive File downloaded to {filepath}")
        return jsonify({"success": True, "filename": filename})
        
    except Exception as e:
        logger.error(f"Error in drive upload: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze_video():
    audio_path = None
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({"success": False, "error": "Filename is required"}), 400
            
        filename = data['filename']
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(video_path):
            return jsonify({"success": False, "error": "File not found"}), 404
        
        # Check cache first — if this exact video was analyzed before, return cached results instantly
        logger.info(f"Checking cache for {filename}...")
        cached = get_cached_results(filename)
        if cached:
            logger.info(f"Cache HIT for {filename} — returning cached results")
            return jsonify(cached)
            
        # Get video duration using ffprobe
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        # We calculate total_duration using ffmpeg stderr output
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
        
        # Extract audio using ffmpeg
        filename_without_ext = os.path.splitext(filename)[0]
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{filename_without_ext}_audio.mp3")
        
        logger.info(f"Extracting audio to {audio_path}")
        ffmpeg_cmd = [
            ffmpeg_exe, '-i', video_path, '-vn', '-acodec', 'libmp3lame', 
            '-ar', '16000', '-ac', '1', '-q:a', '6', '-y', audio_path
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Upload audio to Gemini
        logger.info("Uploading audio to Gemini")
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            return jsonify({"success": False, "error": "GEMINI_API_KEY is not set"}), 500
            
        client = genai.Client(api_key=api_key)
        audio_file = client.files.upload(file=audio_path)
        
        # Generate content
        logger.info("Calling Gemini API")
        # Calculate dynamic cap based on video duration
        # Roughly 1 clip per 1 minute (60 seconds), with a floor of 10 and ceiling of 30
        ideal_cap = int(total_duration / 60) if total_duration > 0 else 15
        dynamic_cap = max(10, min(30, ideal_cap))
        
        duration_context = f"The video is EXACTLY {total_duration} seconds long." if total_duration > 0 else "The video may be long (1-2 hours)."
        
        prompt = f"""
        You are ClipX, an expert video content analyst.
        {duration_context}

        TASK: Act as a hyper-curated filter. You MUST perform a "Forced Attention Traversal" of the entire video.
        
        CRITICAL INSTRUCTION 1: Break the video down into chronological 10-minute blocks (e.g. 0-10, 10-20, etc.). For EVERY single 10-minute block until the end of the video, you MUST provide a 1-sentence summary of what happened in that block to prove you analyzed it.
        CRITICAL INSTRUCTION 2: While summarizing, search for the absolute most valuable, insightful, or entertaining viral clips. 
        CRITICAL INSTRUCTION 3: You MUST extract exactly 10 to 15 of the absolute best clips across the ENTIRE video. Most of your 10-minute blocks will have 0 clips in them. Be extremely picky. Only extract clips that score 65 points or higher.

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
        - Deep Semantic Importance (+30): Use your advanced intelligence to analyze the underlying meaning of the words in context. If a segment carries immense, profound value, perfectly articulates a universal pain point, or is a "golden nugget" of wisdom, award massive bonus points even if it lacks a traditional hook.

        reaction_score (0-100), based ONLY on audible audio, never inferred from visuals or assumed from context:
        - Audible applause: +40
        - Audible laughter: +30
        - Audible verbal agreement/cheering from someone other than the speaker: +30
        If nothing is audibly present, reaction_score MUST be 0.

        CRITICAL LANGUAGE RULE: If the audio is in Hindi or Hinglish, transliterate the `quote` using the English alphabet (e.g. write "Emotion daru ki tarah hota hai" instead of Hindi script). Do NOT translate the meaning.

        You MUST return ONLY a valid JSON array matching this exact structure:
        [
          {{
            "time_block": "Minute 0 to 10",
            "block_summary": "1-sentence summary of the speaker's main points in this block...",
            "viral_clips_found": [
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
          }},
          {{
            "time_block": "Minute 10 to 20",
            "block_summary": "...",
            "viral_clips_found": []
          }}
        ]
        
        Return ONLY the JSON array. Do not include any other text.
        """
        
        models_to_try = ["gemini-3.0-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash"]
        response = None
        last_error = None
        
        for attempt, fallback_model in enumerate(models_to_try):
            try:
                logger.info(f"Calling Gemini API with {fallback_model} (Attempt {attempt+1}/{len(models_to_try)})")
                response = client.models.generate_content(
                    model=fallback_model,
                    contents=[
                        types.Content(
                            parts=[
                                types.Part.from_uri(
                                    file_uri=audio_file.uri,
                                    mime_type=audio_file.mime_type,
                                ),
                                types.Part.from_text(text=prompt),
                            ]
                        )
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        top_p=1.0,
                        top_k=1,
                        response_mime_type="application/json",
                    )
                )
                break  # Success!
            except Exception as e:
                last_error = e
                logger.warning(f"API Error with {fallback_model}: {e}. Retrying in 10s...")
                if attempt < len(models_to_try) - 1:
                    time.sleep(10)
        
        if not response:
            raise last_error
        
        response_text = response.text.strip()
        
        import re
        # Find the first '[' and last ']' in case there's any preamble text
        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            response_text = match.group(0)
            
        response_text = response_text.strip()
        
        try:
            blocks = json.loads(response_text)
            
            # Process and flatten moments
            moments = []
            for block in blocks:
                clips = block.get('viral_clips_found', [])
                for m in clips:
                    try:
                        start_sec = float(m.get('start_seconds', 0))
                        end_sec = float(m.get('end_seconds', 0))
                        
                        # STRICT BOUNDS CHECK: Drop hallucinated timestamps beyond the video duration
                        if start_sec > total_duration or start_sec < 0:
                            continue
                        if end_sec > total_duration:
                            end_sec = total_duration
                        if start_sec >= end_sec:
                            continue
                            
                        m['start_seconds'] = start_sec
                        m['end_seconds'] = end_sec
                    except:
                        continue

                    insight = int(m.get('insight_score', 0))
                    reaction = int(m.get('reaction_score', 0))
                    total = min(insight + reaction, 100)
                    m['score'] = total
                    
                    # FILTER: Keep any segment that scores at least 40 points
                    if total >= 40:
                        moments.append(m)
                    
            # First, sort by score descending to isolate the absolute best moments
            moments.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            # Cap the maximum number of moments to the dynamic cap
            moments = moments[:dynamic_cap]
            
            # Finally, sort the curated list by score in descending order (highest score first)
            moments.sort(key=lambda x: x.get('score', 0), reverse=True)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {response_text}")
            return jsonify({"success": False, "error": f"Failed to parse Gemini response as JSON: {str(e)}"}), 500
            
        # Post-process moments
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
            
        # Clean up audio file
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
        
        # Save to cache so the same video always returns identical results
        save_to_cache(filename, result_data)
        logger.info(f"Cache SAVED for {filename}")
        
        return jsonify(result_data)
        
    except Exception as e:
        logger.error(f"Error in analyze: {str(e)}")
        # Clean up audio file on error too
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except:
            pass
        return jsonify({"success": False, "error": str(e)}), 500

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
    app.run(debug=True, host='0.0.0.0', port=5000)
