from multiprocessing import Manager

manager = Manager()
# this will store objects that the run manager inits prior to calling gunicorn
# then, through this will objects are accessed in app.py
# all objects will be Manager.dict()

active_user_processes = manager.dict()
#format
# {
#     "user_id" : {
#       "video process": VideoProcessingThread(),
#       "report process": ReportProcessingThread(),
#       "frame queue": Manager.Queue(),
#       "report queue": Manager.Queue(),
#       "report list": Manager.list()
#    }
# }
