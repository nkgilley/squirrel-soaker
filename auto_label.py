#!/usr/bin/env python3
# auto_label.py
# Uses Gemini 2.5 Flash to automatically classify raw images into the dataset.

import os
import shutil
import PIL.Image
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError

# Load environment variables from .env file
load_dotenv()

# Setup paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, 'data', 'raw')
SQUIRREL_DIR = os.path.join(BASE_DIR, 'data', 'dataset', 'squirrel')
NOT_SQUIRREL_DIR = os.path.join(BASE_DIR, 'data', 'dataset', 'not_squirrel')

# Ensure directories exist
for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR]:
    os.makedirs(d, exist_ok=True)

# Initialize Gemini Client
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY environment variable not set. Please check your .env file.")
    exit(1)

client = genai.Client(api_key=api_key)

def auto_label_images():
    # Scan raw folder for images
    images = sorted([f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    
    if not images:
        return
        
    print("Found {0} unclassified images in data/raw/. Auto-labeling with Gemini...".format(len(images)))
    
    for filename in images:
        filepath = os.path.join(RAW_DIR, filename)
        print("Processing {0}... ".format(filename), end="", flush=True)
        
        try:
            # Check if file still exists (to prevent race conditions)
            if not os.path.exists(filepath):
                print("skipped (already processed)")
                continue
                
            # Load the image
            image = PIL.Image.open(filepath)
            
            # Request classification from Gemini Flash
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    image,
                    "Is there a squirrel in this photo? Look closely at the birdfeeder, the deck, or the ground. Reply with exactly 'yes' or 'no' in lowercase, and nothing else."
                ]
            )
            
            result = response.text.strip().lower()
            
            # Handle classification
            if result == 'yes':
                shutil.move(filepath, os.path.join(SQUIRREL_DIR, filename))
                print("🐿️  SQUIRREL")
            elif result == 'no':
                shutil.move(filepath, os.path.join(NOT_SQUIRREL_DIR, filename))
                print("❌  NOT SQUIRREL")
            else:
                # Fallback in case of unexpected text response
                print("⚠️  UNCERTAIN (Gemini replied: {0})".format(result))
                
        except APIError as e:
            print("API Error: {0}".format(e))
        except Exception as e:
            print("Failed: {0}".format(e))

if __name__ == '__main__':
    auto_label_images()
