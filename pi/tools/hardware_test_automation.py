#!/usr/bin/env python3
# hardware_test_automation.py
# Simulates a squirrel capture event on the Pi and triggers local solenoid spray and video recording.

import os
import sys
import shutil

# Add the repository root to import the Pi capture package.
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pi import capture

def run_test():
    print("Starting simulated squirrel detection test...")
    local_time = capture.get_eastern_time()
    filename = "test_sim_{0}.jpg".format(local_time.strftime("%Y%m%d_%H%M%S"))
    filepath = os.path.join(capture.OUTPUT_DIR, filename)
    
    source_img = os.path.expanduser('~/squirrel_soaker/test_squirrel.jpg')
    if not os.path.exists(source_img):
        print("Error: test image {0} not found!".format(source_img))
        return
        
    shutil.copy(source_img, filepath)
    print("Copied test squirrel image to {0}".format(filepath))
    
    with open(filepath, 'rb') as image_file:
        result = capture.check_for_squirrel(filename, image_file.read(), should_save=False, is_test=True)
    is_squirrel = result.get('detected_squirrel', False)
    should_spray = result.get('should_spray', False)
    confidence = float(result.get('confidence', 0.0))
    spray_duration = float(result.get('spray_duration', 3.0))
    print("Inference results: squirrel={0}, should_spray={1}, confidence={2:.4f}, duration={3:.1f}s".format(
        is_squirrel, should_spray, confidence, spray_duration
    ))
    
    if should_spray:
        print("Test MATCH! Squirrel detected with high confidence ({0:.1f}%). Triggering spray for {1:.1f}s...".format(confidence * 100, spray_duration))
        capture.trigger_spray_locally(spray_duration)
    else:
        print("Test NO MATCH. Prediction: squirrel={0}, confidence={1:.4f}".format(is_squirrel, confidence))
        
    if os.path.exists(filepath):
        os.remove(filepath)
        print("Cleaned up temp test image.")

if __name__ == '__main__':
    run_test()
