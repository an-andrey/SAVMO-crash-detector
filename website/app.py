import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, session
from werkzeug.utils import secure_filename 
import time
from queue import Empty
import uuid
import atexit
import sys
import signal
import errno

#file imports
from video_thread import VideoProcessingThread
from report_thread import ReportProcessingThread
from utils.app_utils import *

#objects defined from run_manager.py, created prior to setting the gunicorn workers
import shared_objects

# setting paths & config
UPLOAD_FOLDER = 'static/uploads'
VIDEO_UPLOAD_FOLDER = "videos/user_uploads"
VIDEO_DEMO_FOLDER = "videos/demos"

app = Flask(__name__)
app.secret_key = "secret_key_passhrase"
app.config['VIDEO_DEMO_FOLDER'] = VIDEO_DEMO_FOLDER
app.config['VIDEO_UPLOAD_FOLDER'] = VIDEO_UPLOAD_FOLDER
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024 # sets file limit for user videos
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_user_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']

def clear_queue(queue):
    while True:
            try:
                # Use get_nowait() to pull items out without blocking
                queue.get_nowait()
            except Empty:
                break
            except Exception as e:
                break

def clear_list(list): 
    del list[:]

def reset_user_processes(user_id):
    user = shared_objects.active_user_processes.get(user_id)

    if user: 
        if "video_stop_event" in user:
            user["video_stop_event"].set()
        
        if "report_stop_event" in user:
            user["report_stop_event"].set()

        clear_queue(user["frame queue"])
        clear_queue(user["report queue"])
        clear_list(user["report list"])

def create_user_processes(user_id, video_path): 
    reset_user_processes(user_id)
    
    # 1. Create a NEW inner dictionary using the Manager
    # This allows us to modify 'user_data' in place later without reassigning
    user_data = shared_objects.manager.dict() 

    # 2. Setup Queues/Lists
    user_data["frame queue"] = shared_objects.manager.Queue(maxsize=60)
    user_data["report queue"] = shared_objects.manager.Queue()
    user_data["report list"] = shared_objects.manager.list()
    
    # 3. Setup Processes
    report_lock = shared_objects.manager.Lock()
    video_stop_event = shared_objects.manager.Event()    
    report_stop_event =shared_objects.manager.Event()   

    video_proc = VideoProcessingThread(
        video_path, 
        user_data["frame queue"], 
        user_data["report queue"], 
        video_stop_event
    )
    
    report_proc = ReportProcessingThread(
        user_data["report queue"], 
        user_data["report list"],  
        report_lock,
        report_stop_event
    )
    
    video_proc.start()
    report_proc.start()

    # 4. Store references
    user_data["video_pid"] = video_proc.pid
    user_data["report_pid"] = report_proc.pid

    user_data["video_stop_event"] = video_stop_event
    user_data["report_stop_event"] = report_stop_event

    # 5. Assign the inner Manager dict to the outer Manager dict
    # Because user_data is a Proxy (Manager.dict), updates to it happen globally automatically
    shared_objects.active_user_processes[user_id] = user_data

# app routes
@app.route('/')
def home(): 
    return render_template("home.html")

@app.route('/start_thread', methods=["POST"])
def start_thread(): 
    user_id = get_user_id()
    video_path = None
    start_user_processes = False

    # Check for demo video first
    if 'demo_video' in request.form:
        filename = request.form['demo_video']
        # Securely join path
        video_path = os.path.join(app.config['VIDEO_DEMO_FOLDER'], filename)
        if not os.path.exists(video_path):
            flash("Demo video not found. Please check configuration.")
            return redirect(url_for('home'))
        else: 
            start_user_processes = True

    # Check for file upload
    elif 'file' in request.files:
        file = request.files['file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('home'))
        
        if file:
            filename = secure_filename(file.filename)
            video_path = os.path.join(app.config['VIDEO_UPLOAD_FOLDER'], filename)
            file.save(video_path)
            print(f"Starting uploaded video: {video_path}")
            start_user_processes = True

    if start_user_processes == True:
        create_user_processes(user_id, video_path)
        return redirect(url_for("video"))

    if not video_path:
        flash("No video source selected.")
        return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    user_id = get_user_id()
    user = shared_objects.active_user_processes[user_id]

    #this avoids issues, since multiprocessing's list is an iterator rather than a list
    #and makes things slow on the web side
    local_list = []

    if "report list" in user:
        local_list = list(user["report list"])

    return render_template('dashboard.html', reports=reversed(local_list))
        
def stream_frames(user_id): # grabs the latest frame from the video thread
    user = shared_objects.active_user_processes.get(user_id)
    last_frame_time = time.time()
    frame_interval = 1/60

    if not user:
        return 

    frame_queue = user["frame queue"]
    
    while True:
        time_to_wait = frame_interval- (time.time() - last_frame_time)

        if time_to_wait > 0: 
            time.sleep(time_to_wait)

        last_frame_time = time.time()

        try:
            frame = frame_queue.get_nowait()
            yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except Empty:
            print("EMPTY QUEUE")
            time.sleep(0.01)
            continue
        except Exception:
            break


@app.route("/video")
def video(): 
    return render_template("video.html")

@app.route("/video_feed")
def video_feed(): 
    user_id = get_user_id()
    return Response(
        stream_frames(user_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )
##################
# ERROR HANDLING #
##################
@app.errorhandler(404)
def page_not_found(e):
    # 404: Not Found
    return render_template('error.html', error_code=404, error_message="We couldn't find the page you're looking for."), 404

@app.errorhandler(500)
def internal_server_error(e):
    # 500: Internal Server Error
    # Log the error if needed: app.logger.error(f"Server Error: {e}")
    return render_template('error.html', error_code=500, error_message="Something went wrong on our end. We're fixing it."), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Generic handler for other exceptions
    # Pass the error details if you want (be careful with sensitive info in production)
    return render_template('error.html', error_code=500, error_message="An unexpected error occurred."), 500


def pid_exists(pid):
    """Check whether pid exists in the current process table."""
    if pid is None: return False
    try:
        os.kill(pid, 0) # 0 signal doesn't kill, just checks existence
    except OSError as err:
        if err.errno == errno.ESRCH:
            # ESRCH == No such process
            return False
        elif err.errno == errno.EPERM:
            # EPERM clearly means there's a process to deny access to
            return True
        else:
            # According to "man 2 kill" possible error values are
            # (EINVAL, EPERM, ESRCH)
            raise
    return True

#Adding global variables to templates
@app.context_processor
def add_global_vars(): 
    report_count = 0 
    is_feed_live = False

    try:
        user_id = get_user_id()
        user_data = shared_objects.active_user_processes.get(user_id)
        
        # Check validity using PID
        pid = user_data.get("video_pid") if user_data else None
        is_feed_live = pid_exists(pid)
        if "report list" in user_data:
            report_count = len(user_data["report list"])
        
    except Exception:
        is_feed_live = False

    return {
        "is_feed_live": is_feed_live,
        "report_count": report_count
    }

# --- Run the App ---
if __name__ == '__main__':
    # Ensure the 'static/uploads' directory exists
    # Run in debug mode for development
    app.run(debug=False, threaded=True)

    # cleaning all previous frames and videos uploaded by the user
    clean_dir(UPLOAD_FOLDER)
    clean_dir(VIDEO_UPLOAD_FOLDER)
