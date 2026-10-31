# HACKGWINNETT
# Beginner AI division



Medical care Voice Navigator

A voice app that helps people with disabilities find their way around medical care. You talk, it talks back.

Not official advice. It gives general info only. For real answers, go to www.medicare.gov or call 1-800-633-4227.

What it does
Deadlines: estimates when your medical care may start
Letters: explains medical care letters and counts days to a deadline
Plans: things to check when comparing plans
Learn about my condition: explains a health condition and opens the source website
Setup (Windows)

You need a microphone, internet, and Python 3.12 (newer versions break PyAudio).

git clone https://github.com/YOUR-USERNAME/medical care-voice-navigator.git
cd medical care-voice-navigator
py -3.12 -m venv venv
venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python text.py

Mac/Linux: use python3.12 -m venv venv and source venv/bin/activate. Install PortAudio first (brew install portaudio or sudo apt install portaudio19-dev).

What to say

help · deadlines · letters · plans · learn about my condition · open the website · set up my info · repeat that · go back · quit

Privacy
Your info is saved in profile.json on your computer only. It is kept out of GitHub. Say "delete my info" to erase it.
Your voice is sent to Google to turn it into text.
Sources

Condition info is from MedlinePlus and has not been reviewed by a medical professional. medical care rules are from www.medicare.gov
