import multiprocessing as mp
import os 
import time

from crash_summary import create_report

class ReportProcessingThread(mp.Process): 
    def __init__(self, report_queue, report_list, db_lock, stop_event):
        super().__init__()

        self.report_list = report_list
        self.report_queue = report_queue
        self.db_lock = db_lock
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set(): 
            crash_image_paths = self.report_queue.get()

            try: 
                current_time = time.time()
                crash_description = create_report([crash_image_paths[0]])
                print(f"making the report took {time.time() - current_time}")

                image_filenames = [os.path.basename(path) for path in crash_image_paths]

                new_report = {
                    "description" : crash_description,
                    "images" : image_filenames
                }

                with self.db_lock: 
                    print("added new report")
                    self.report_list.append(new_report)

            except Exception as e: 
                print(f"report processor thread failed : {e}")

            self.report_queue.task_done()

