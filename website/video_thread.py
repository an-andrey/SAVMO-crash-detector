# import threading
import multiprocessing as mp
import cv2
import time
import torch
from PIL import Image
import os
import onnxruntime as ort
import numpy as np

from utils.video_processing_utils import load_transforms, smoothen_predictions

# Creating a thread so the video continuisly runs in the background of the site
class VideoProcessingThread(mp.Process): 
    def __init__(self, video_path, frame_queue, report_queue, stop_event, max_gap_size = 5): 
        super().__init__()
        self.video_path = video_path
        self.max_gap_size = max_gap_size

        self.stop_event = mp.Event()

        self.frame_queue = frame_queue
        self.report_queue = report_queue
        self.stop_event = stop_event
        
        # FOR ONNX
        #getting the model and the transforms for the input
        self.transform = load_transforms()
        self.ort_session = ort.InferenceSession("model.onnx")
        self.input_name = self.ort_session.get_inputs()[0].name

        self.status = mp.Value("i", 1) # 1 is running, 0 is paused
    
    def run(self): # main video loop that parses the frames in the background
        cap = cv2.VideoCapture(self.video_path) # the video frame
        if not cap.isOpened(): 
            print("Can't open the video file")
            self.status.value = 0 
            return
    
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frame_interval = 1 #max(1, int(round(video_fps/ 10))) # to reduce compute, processing every 10th frame

        # Different State variables 
        raw_predictions = [] 
        smoothed_predictions = [] # applying smoothen_predictions from the utils 

        # to detect if a frame is within a crash sequence, using max_gap_size to check in this many frames in the future
        # so to do that we need a small buffer between processed frames and what's displayed
        frame_buffer = []
        prediction_delay = self.max_gap_size
        CRASH_REPORT_THRESHOLD = 10 #num of frames of crashes needed to create a report
        UPLOAD_DIR = 'static/uploads'
        is_in_crash_event = False
        frame_idx = 0
        display_frame_bgr = None
        current_prediction = None

        
        try: 
            while not self.stop_event.is_set(): 
                
                frame_fetched, frame = cap.read() # get current frame

                if not frame_fetched: # either failed to fetch the frame, or video ended
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0) # set video to 0 fps to stop

                    raw_predictions.clear()
                    smoothed_predictions.clear()
                    frame_buffer.clear()
                    frame_idx = 0
                    continue
            
                is_sampled_frame = (frame_idx % frame_interval == 0)
                frame_buffer.append(frame)

                if is_sampled_frame: 
                    # FOR ONNX
                    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) #get frame in image format as RGB

                    image_tensor = self.transform(image)

                    image_data = image_tensor.unsqueeze(0).numpy()
                    image_data = image_data.astype(np.float32)
                    output = self.ort_session.run(None, {self.input_name: image_data}) # returns score for both classes
                    prediction_index = np.argmax(output[0], axis=1)[0] #gets either 0 or 1 as the prediction
                    prediction = int(prediction_index)

                    raw_predictions.append(prediction)
                    
                    smoothed_predictions = smoothen_predictions(raw_predictions, self.max_gap_size)
                
                # start displaying the frame after the delay
                if len(smoothed_predictions) > prediction_delay: 
                    display_frame_bgr = frame_buffer.pop(0)
                    prediction_index = len(smoothed_predictions) - prediction_delay - 1
                    current_prediction = smoothed_predictions[prediction_index]
                
                # Check for a sustained crash event using the smoothed predictions
                current_history = smoothed_predictions[:len(smoothed_predictions) - prediction_delay]
                
                # grab the last frames, and check if there's enough crash frames to make a report
                if len(current_history) >= CRASH_REPORT_THRESHOLD: 
                    
                    # Check the last CRASH_REPORT_THRESHOLD predictions
                    last_predictions = current_history[-CRASH_REPORT_THRESHOLD:]

                    # if the last CRASH_REPORT_THRESHOLD frames are all crashes, make a report
                    if all(p == 1 for p in last_predictions) and not is_in_crash_event: 
                        is_in_crash_event = True
                        
                        if not os.path.exists(UPLOAD_DIR):
                            os.makedirs(UPLOAD_DIR)
                            print(f"Created directory: {UPLOAD_DIR}")
                        
                        # Grabbing 3 key frames from the crash
                        
                        # grab the frame that's in the middle of the crash event
                        middle_index_in_history = len(current_history) - (CRASH_REPORT_THRESHOLD // 2) - 1
                        
                        # Grab 2 other frames that are evenly spaced out
                        sampled_indices_to_report = [
                            middle_index_in_history - 3,
                            middle_index_in_history,
                            middle_index_in_history + 3
                        ]

                        # Map sampled indices to the original video frame numbers
                        # They're not the same, since we're not parsing all frames from a video
                        frame_indices_to_report = [max(0, i * frame_interval) for i in sampled_indices_to_report]
                        
                        # grab the frames and save them locally as jpg's
                        report_file_paths = []
                        all_frames_retrieved = True
                        
                        #making a temporary video capture to grab crash frames
                        temp_capture = cv2.VideoCapture(self.video_path) 

                        for i, frame_num in enumerate(frame_indices_to_report):
                            temp_capture.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                            crash_frame_fetched, crash_frame = temp_capture.read()
                            
                            if crash_frame_fetched:
                                # SAVE the image to the 'upload/' directory
                                timestamp = int(temp_capture.get(cv2.CAP_PROP_POS_MSEC))
                                filename = f"crash_frame_{timestamp}ms_p{i+1}.jpg"
                                file_path = os.path.join(UPLOAD_DIR, filename)
                                
                                cv2.imwrite(file_path, crash_frame)
                                report_file_paths.append(file_path)
                                print(f"Saved frame {i+1} to: {file_path}")
                            else:
                                all_frames_retrieved = False
                                print(f"Failed to re-read frame {frame_num} for reporting.")
                                break
                                
                        temp_capture.release() # Release the temporary capture
                        
                        ######################################
                        # Pass crash frames to report thread #
                        ######################################
                        if all_frames_retrieved and len(report_file_paths) == 3:
                            self.report_queue.put(report_file_paths)

                        else:
                            print("Skipping AI report due to insufficient/failed frame retrieval.")
                            
                    elif not all(p == 1 for p in last_predictions): # Crash event has ended
                        is_in_crash_event = False

                if display_frame_bgr is not None: 
                    display_prediction = "CRASH DETECTED" if current_prediction == 1 else "No Crash"
                    color = (0, 0, 255) if display_prediction == "CRASH DETECTED" else (0, 255, 0)
                    
                    font_scale = 1.5
                    font_thickness = 3
                    text_pos = (10, 50)
                    
                    (text_width, text_height), baseline = cv2.getTextSize(
                        display_prediction, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness
                    )
                    
                    padding = 5 
                    rect_start = (text_pos[0] - padding, text_pos[1] - text_height - baseline - padding)
                    rect_end = (text_pos[0] + text_width + padding, text_pos[1] + padding)
                    
                    # Draw the black background rectangle
                    cv2.rectangle(display_frame_bgr, rect_start, rect_end, (0, 0, 0), -1)
                    
                    # Draw the "CRASH" / "No Crash" text
                    cv2.putText(display_frame_bgr, 
                                display_prediction, 
                                text_pos, 
                                cv2.FONT_HERSHEY_SIMPLEX, 
                                font_scale, 
                                color, 
                                font_thickness, 
                                cv2.LINE_AA)

                    ########################
                    # ADDING JPGS TO FRAME QUEUE #
                    ########################
                    _, encoded_image = cv2.imencode(".jpg", display_frame_bgr)
                    frame_bytes = bytearray(encoded_image)

                    #Frame Queue has a set limit
                    try: 
                        self.frame_queue.put_nowait(frame_bytes)

                    except:
                        time.sleep(0.01) 
                        continue

                frame_idx += 1
                
        finally: 
            cap.release()
            self.status.value = 0
    
    def stop(self): #stops the thread
        self.stop_event.set() 
