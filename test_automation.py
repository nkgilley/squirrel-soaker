#!/usr/bin/env python3
# test_automation.py
# Simulates a squirrel capture event on the Pi and triggers local solenoid spray and video recording.

import os
import sys
import shutil

# Add current directory to path to import capture
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import capture

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
    
    is_squirrel, confidence, spray_duration = capture.check_for_squirrel(filepath, is_test=True)
    print("Inference results: is_squirrel={0}, confidence={1:.4f}, spray_duration={2:.1f}s".format(is_squirrel, confidence, spray_duration))
    
    if is_squirrel and confidence > 0.70:
        print("Test MATCH! Squirrel detected with high confidence ({0:.1f}%). Triggering spray for {1:.1f}s...".format(confidence * 100, spray_duration))
        capture.trigger_spray_locally(spray_duration)
    else:
        print("Test NO MATCH. Prediction: squirrel={0}, confidence={1:.4f}".format(is_squirrel, confidence))
        
    if os.path.exists(filepath):
        os.remove(filepath)
        print("Cleaned up temp test image.")

if __name__ == '__main__':
    run_test()
