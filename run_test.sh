export PYTHONPATH=.:src:$PYTHONPATH
QT_QPA_PLATFORM=offscreen xvfb-run python /home/jules/verification/verify_catch_screen.py
