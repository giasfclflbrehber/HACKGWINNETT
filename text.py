"""
Medicare Navigator - voice app with a window (GUI). One file.

Run it:
    pip install SpeechRecognition pyttsx3 pyaudio
    python medicare_navigator_combined.py

Layout of this file:
    1. Imports and settings
    2. Window plumbing + speaking and listening   (changed: now talks to the window)
    3. Your original logic                        (same as before, 5 small edits
                                                   marked with  # GUI:  comments)
    4. The window itself (tkinter)
    5. main() - starts the logic thread and the window
"""
import gc
import html
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from tkinter import font as tkfont

import speech_recognition as sr
import pyttsx3

engine = None  # created inside the logic thread (see run_logic)
recognizer = sr.Recognizer()

PROFILE_FILE = "profile.json"
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

last_message = ""
last_url = None  # the website from the most recent condition lookup
current_page = "menu"
history = []

# =====================================================================
# WINDOW PLUMBING
# =====================================================================
# The window and the app logic run in two different threads (like two
# Java threads). They talk through queues, which are thread-safe mailboxes.

ui_queue = queue.Queue()      # logic  -> window  (things to display)
typed_queue = queue.Queue()   # window -> logic   (typed text, button clicks)
voice_queue = queue.Queue()   # microphone -> logic (what was heard)

SERVICE_ERROR = "__service_error__"
SKIP = "skip"

speaking = threading.Event()   # set while the app is talking
mute_until = 0.0               # ignore the microphone until this time
mic_ready = False
skip_requested = False


def ui_event(kind, data=None):
    ui_queue.put((kind, data))


# ---------- Speaking and listening ----------

def speak(message):
    global last_message, mute_until
    last_message = message
    print("App:", message)
    ui_event("say", message)          # GUI: show it on screen too
    ui_event("status", "Speaking")
    speaking.set()
    try:
        engine.say(message)
        engine.runAndWait()
    finally:
        speaking.clear()
        mute_until = time.time() + 0.7   # don't hear our own voice
    ui_event("status", "Ready")


def on_audio(rec, audio):
    """Called by the microphone each time it captures a phrase."""
    if speaking.is_set() or time.time() < mute_until:
        return
    try:
        text = rec.recognize_google(audio).lower()
    except sr.UnknownValueError:
        return
    except sr.RequestError:
        voice_queue.put(SERVICE_ERROR)
        return
    voice_queue.put(text)


def start_microphone():
    global mic_ready
    try:
        mic = sr.Microphone()
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
        recognizer.listen_in_background(mic, on_audio, phrase_time_limit=8)
        mic_ready = True
    except Exception as err:
        print("Microphone problem:", err)
        ui_event("system", "I can't use the microphone right now. "
                           "You can type or tap the buttons instead.")


def drain(q):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def wait_for_input(timeout):
    """Wait for typed text OR a voice phrase. Returns None on timeout."""
    end = None if timeout is None else time.time() + timeout
    while True:
        for q in (typed_queue, voice_queue):
            try:
                return q.get_nowait()
            except queue.Empty:
                pass
        if end is not None and time.time() > end:
            return None
        time.sleep(0.05)


def listen(main=False):
    """Hears speech, typing, or a button click - whichever comes first."""
    global skip_requested
    skip_requested = False
    ui_event("mode", "menu" if main else "question")
    drain(voice_queue)
    ui_event("status", "Listening" if mic_ready else "Ready")

    item = wait_for_input(10)
    if item is None:                       # nobody said anything
        ui_event("status", "Ready")
        return ""

    ui_event("mode", "busy")
    if item == SERVICE_ERROR:
        ui_event("status", "Error")
        speak("I can't reach the speech service. Check your internet.")
        return ""

    text = item.strip().lower()
    print("You said:", text)
    ui_event("heard", text)
    if text == SKIP:
        skip_requested = not main
        return ""
    ui_event("status", "Thinking")
    return text


def type_fallback():
    """Used when we couldn't hear an answer. Waits for typing, speech, or Skip."""
    global skip_requested
    if skip_requested:                     # Skip was already pressed
        skip_requested = False
        return ""
    ui_event("mode", "question")
    ui_event("system", "I couldn't hear you. Say it again, type your "
                       "answer below, or press Skip.")
    ui_event("status", "Listening" if mic_ready else "Ready")
    while True:
        item = wait_for_input(None)
        if item != SERVICE_ERROR:
            break
    ui_event("mode", "busy")
    text = item.strip().lower()
    ui_event("heard", text)
    return "" if text == SKIP else text


# =====================================================================
# YOUR APP LOGIC
# =====================================================================

def words(text):
    return set(re.findall(r"[a-z']+", text.lower()))


def yes(text):
    return bool(words(text) & {"yes", "yeah", "yep", "correct", "right", "sure"})


def no(text):
    return bool(words(text) & {"no", "nope", "wrong", "incorrect"})


# ---------- Saved information ----------

def load_profile():
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            return json.load(f)
    return {}


def save_profile(profile):
    with open(PROFILE_FILE, "w") as f:
        json.dump(profile, f, indent=2)


# ---------- Turning speech into values ----------

def parse_name(text):
    return text.strip().title() or None


def parse_year(text):
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None


def parse_yes_no(text):
    if yes(text):
        return True
    if no(text):
        return False
    return None


def parse_month_year(text):
    year = re.search(r"\b(19|20)\d{2}\b", text)
    month = next((i + 1 for i, m in enumerate(MONTHS) if m in text), None)
    if year and month:
        return f"{year.group()}-{month:02d}"
    return None


def show_month_year(value):
    year, month = value.split("-")
    return f"{MONTHS[int(month) - 1].title()} {year}"


def show_yes_no(value):
    return "yes" if value else "no"


def add_months(value, n):
    year, month = map(int, value.split("-"))
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def parse_full_date(text):
    year_match = re.search(r"\b(20\d{2})\b", text)
    month = next((i + 1 for i, m in enumerate(MONTHS) if m in text), None)
    rest = re.sub(r"\b20\d{2}\b", "", text)
    day_match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", rest)
    if not (month and day_match):
        return None
    year = int(year_match.group(1)) if year_match else date.today().year
    try:
        return date(year, month, int(day_match.group(1)))
    except ValueError:
        return None


def show_full_date(d):
    return f"{MONTHS[d.month - 1].title()} {d.day}, {d.year}"


# ---------- Asking for information by voice ----------

def ask_field(question, parser, show=str):
    """Ask, repeat back, and confirm. Returns the value, or None if skipped."""
    for _ in range(3):
        speak(question)
        answer = listen()
        if not answer:
            answer = type_fallback().strip().lower()   # GUI: was input(...)
            if not answer:
                return None
        value = parser(answer)
        if value is None:
            speak("I couldn't understand that.")
            continue
        speak(f"I heard {show(value)}. Is that right? Say yes or no.")
        if yes(listen()):
            return value
    speak("Let's skip that one for now.")
    return None


def collect_profile():
    ui_event("page", "info")                           # GUI: update the header
    profile = load_profile()
    speak("Let's set up your information. You can skip any question by staying quiet.")

    name = ask_field("What is your name?", parse_name)
    if name:
        profile["name"] = name

    year = ask_field("What year were you born?", parse_year)
    if year:
        profile["birth_year"] = year

    on_ssdi = ask_field("Do you get Social Security disability benefits? "
                        "Say yes or no.", parse_yes_no, show_yes_no)
    if on_ssdi is not None:
        profile["on_ssdi"] = on_ssdi

    if profile.get("on_ssdi"):
        start = ask_field("In what month and year did your disability "
                          "benefits start?", parse_month_year, show_month_year)
        if start:
            profile["ssdi_start"] = start

    save_profile(profile)
    speak("Saved. Say deadlines to see your timeline.")


def read_profile():
    profile = load_profile()
    if not profile:
        speak("I don't have any information saved yet. Say set up my info.")
        return
    parts = []
    if "name" in profile:
        parts.append(f"Your name is {profile['name']}.")
    if "birth_year" in profile:
        parts.append(f"You were born in {profile['birth_year']}.")
    if "on_ssdi" in profile:
        parts.append("You get disability benefits." if profile["on_ssdi"]
                     else "You don't get disability benefits.")
    if "ssdi_start" in profile:
        parts.append(f"They started in {show_month_year(profile['ssdi_start'])}.")
    speak(" ".join(parts))


def delete_profile():
    speak("Do you want me to delete all your saved information? Say yes or no.")
    if yes(listen()):
        if os.path.exists(PROFILE_FILE):
            os.remove(PROFILE_FILE)
        speak("Your information is deleted.")
    else:
        speak("Okay, I kept it.")


# ---------- Letters ----------

LETTER_NAMES = {
    "denial": "a denial letter",
    "msn": "a Medicare summary notice",
    "eob": "an explanation of benefits",
    "bill": "a bill",
    "approval": "an approval letter",
    "enrollment": "an enrollment or premium letter",
    "unknown": "a letter you are not sure about",
}

LETTER_HELP = {
    "denial": [
        "A denial means Medicare or your plan said no to a service or payment. "
        "A denial is not always final. You can usually appeal.",
        "The letter should list your appeal deadline and how to file. "
        "Write the deadline down. It is often between 60 and 120 days.",
        "For free help with an appeal, call 1 800 633 4227 and ask for your "
        "state health insurance counselor.",
    ],
    "msn": [
        "A Medicare summary notice is not a bill. It lists what providers "
        "charged, what Medicare approved, and what you may owe.",
        "Check that you actually got every service listed. "
        "If something looks wrong, call 1 800 633 4227.",
    ],
    "eob": [
        "An explanation of benefits comes from your plan. It is not a bill. "
        "It shows what was charged and what the plan paid.",
        "Compare it with any bill you get from your doctor before paying.",
    ],
    "bill": [
        "Before you pay a bill, compare it with your latest summary notice "
        "or explanation of benefits.",
        "If the amounts don't match, call the provider's billing office and "
        "ask them to explain. You can also call 1 800 633 4227.",
    ],
    "approval": [
        "An approval letter means your plan agreed to cover a service or item.",
        "Check the dates and the provider name on the letter. "
        "Approvals often only last a set time.",
    ],
    "enrollment": [
        "Letters about enrollment or premiums can be time sensitive. "
        "Look for a due date and an amount.",
        "Be careful about scams. Medicare won't call you to ask for payment "
        "or your card number. If unsure, call 1 800 633 4227.",
    ],
    "unknown": [
        "Look at the top of the letter. Find who sent it, the date, and "
        "whether it says this is not a bill.",
        "Also look for any deadline. If you're still unsure, call "
        "1 800 633 4227 and have the letter in your hand.",
    ],
}


def parse_letter_type(text):
    if any(w in text for w in ["not sure", "don't know", "dont know", "unknown", "no idea"]):
        return "unknown"
    if any(w in text for w in ["denial", "denied", "not covered", "rejected", "appeal"]):
        return "denial"
    if any(w in text for w in ["summary notice", "summary", "msn"]):
        return "msn"
    if any(w in text for w in ["explanation of benefits", "benefits", "eob"]):
        return "eob"
    if any(w in text for w in ["bill", "owe", "payment"]):
        return "bill"
    if any(w in text for w in ["approval", "approved", "authorization"]):
        return "approval"
    if any(w in text for w in ["enrollment", "premium", "penalty", "welcome"]):
        return "enrollment"
    return None


def letters():
    speak("Let's go through your letter.")
    kind = ask_field(
        "What kind of letter is it? You can say denial, summary notice, "
        "explanation of benefits, bill, approval, enrollment, or not sure.",
        parse_letter_type, lambda v: LETTER_NAMES[v])
    if kind is None:
        return

    for line in LETTER_HELP[kind]:
        speak(line)

    has_deadline = ask_field("Does the letter give a deadline? Say yes or no.",
                             parse_yes_no, show_yes_no)
    if not has_deadline:
        return

    deadline = ask_field("What is the deadline? Say the month, day, and year.",
                         parse_full_date, show_full_date)
    if deadline is None:
        return

    days_left = (deadline - date.today()).days
    if days_left < 0:
        speak("That date has already passed. Call 1 800 633 4227 right away. "
              "You may still have options.")
    elif days_left == 0:
        speak("That deadline is today. Act on it right away.")
    else:
        speak(f"You have {days_left} days left. Try to act well before then.")


# ---------- Plans ----------

PLAN_QUESTIONS = [
    ("keep_doctors", "Is there a doctor or specialist you want to keep?"),
    ("takes_drugs", "Do you take prescription drugs regularly?"),
    ("equipment", "Do you use medical equipment, like a wheelchair, oxygen, or a CPAP?"),
    ("travels", "Do you travel a lot, or live in more than one state?"),
    ("low_income", "Do you have Medicaid or a low income?"),
]

PLAN_TIPS = {
    "keep_doctors": "Ask your doctors which plans they accept. With Medicare "
                    "Advantage, check that they are in the plan's network.",
    "takes_drugs": "You will need drug coverage, called Part D, or an Advantage "
                   "plan that includes it. Check that your drugs are on the plan's list.",
    "equipment": "Ask whether your equipment supplier is in the plan's network, "
                 "and whether the plan needs approval before you get equipment.",
    "travels": "Original Medicare works across the country. Advantage networks "
               "are often local, so check what is covered away from home.",
    "low_income": "You may qualify for help with costs, like Extra Help for "
                  "drug costs or Medicare Savings Programs. Ask a state health "
                  "insurance counselor, or call 1 800 633 4227.",
}


def plans():
    speak("I will ask a few quick questions to help you think about plan types. "
          "I can't pick a plan for you.")
    answers = {}
    for key, question in PLAN_QUESTIONS:
        value = ask_field(question + " Say yes or no.", parse_yes_no, show_yes_no)
        if value is not None:
            answers[key] = value

    profile = load_profile()
    profile["plan_answers"] = answers
    save_profile(profile)

    speak("There are two main paths. Original Medicare lets you see any doctor "
          "or hospital in the country that takes Medicare. Medicare Advantage "
          "is a private plan that bundles your coverage, often with extras, "
          "but it usually limits you to a network of doctors.")

    for key, _ in PLAN_QUESTIONS:
        if answers.get(key):
            speak(PLAN_TIPS[key])

    speak("If you are under 65, you may have fewer supplement plan choices in "
          "some states. A free state health insurance counselor can explain "
          "your options. You can also compare plans at medicare dot gov.")


# ---------- Learn about my condition ----------

# General, plain-language facts. Not medical advice.
CONDITIONS = {
    "ms": {
        "name": "multiple sclerosis",
        "aliases": ["multiple sclerosis", "ms"],
        "what": "Multiple sclerosis is a long-term condition where the immune "
                "system attacks the protective coating around nerves in the "
                "brain and spinal cord. This disrupts messages between the "
                "brain and the body.",
        "symptoms": "Common symptoms include fatigue, numbness or tingling, "
                    "vision problems, and trouble with walking and balance.",
        "treatments": "Treatments include medicines that reduce relapses and "
                      "slow the disease, steroids for flare-ups, physical "
                      "therapy, and medicines for symptoms like stiffness.",
        "facts": "It affects everyone differently. Many people have relapses "
                 "followed by recovery. It is most often diagnosed in young "
                 "adults. There is no cure yet, but treatment has improved a lot.",
    },
    "parkinsons": {
        "name": "Parkinson's disease",
        "aliases": ["parkinson", "parkinsons"],
        "what": "Parkinson's disease is a nervous system condition that "
                "affects movement. It happens when brain cells that make a "
                "chemical called dopamine are lost over time.",
        "symptoms": "Common symptoms include shaking at rest, stiff muscles, "
                    "slow movement, and balance problems.",
        "treatments": "Treatments include medicines like levodopa, physical, "
                      "occupational, and speech therapy, and for some people "
                      "a surgery called deep brain stimulation.",
        "facts": "Symptoms usually start slowly and often on one side of the "
                 "body. There is no cure, but many people manage symptoms well "
                 "for years. It is more common in older adults.",
    },
    "als": {
        "name": "ALS, also called Lou Gehrig's disease",
        "aliases": ["als", "lou gehrig", "amyotrophic lateral sclerosis"],
        "what": "ALS is a progressive disease that damages the nerve cells "
                "that control muscles, so the muscles slowly get weaker.",
        "symptoms": "Common symptoms include muscle weakness, twitching, and "
                    "over time trouble speaking, swallowing, and breathing.",
        "treatments": "There is no cure, but a few medicines can slow it for "
                      "some people. Care teams also help with breathing "
                      "support, nutrition, speech devices, and therapy.",
        "facts": "People with ALS can qualify for Medicare as soon as their "
                 "disability benefits begin, without the usual 24 month wait. "
                 "Care from a team that specializes in ALS can make a real "
                 "difference in quality of life.",
    },
    "cp": {
        "name": "cerebral palsy",
        "aliases": ["cerebral palsy", "cp"],
        "what": "Cerebral palsy is a group of conditions that affect movement, "
                "muscle tone, and posture. It is caused by a problem with "
                "brain development before, during, or soon after birth.",
        "symptoms": "Symptoms vary a lot. They can include stiff or floppy "
                    "muscles, shaky movements, and trouble with balance or "
                    "walking.",
        "treatments": "Treatments include physical, occupational, and speech "
                      "therapy, medicines for muscle stiffness, mobility aids, "
                      "and sometimes surgery.",
        "facts": "It is lifelong. The brain injury itself does not get worse, "
                 "but how it affects the body can change over time. Severity "
                 "ranges from mild to very significant.",
    },
    "epilepsy": {
        "name": "epilepsy",
        "aliases": ["epilepsy", "seizure"],
        "what": "Epilepsy is a brain condition that causes repeated seizures, "
                "which happen when brain cells send sudden bursts of "
                "electrical activity.",
        "symptoms": "Seizures can cause shaking, staring spells, confusion, "
                    "or loss of awareness, depending on the type.",
        "treatments": "Anti-seizure medicines control seizures in most people. "
                      "Others may use surgery, nerve stimulation, or a special "
                      "diet.",
        "facts": "If someone has a seizure, stay with them, keep them safe, "
                 "and time it. Call emergency services if it lasts more than "
                 "5 minutes. Many people with epilepsy live seizure free "
                 "with treatment.",
    },
    "diabetes": {
        "name": "diabetes",
        "aliases": ["diabetes", "diabetic"],
        "what": "Diabetes is a condition where the body has trouble using "
                "blood sugar. In type 1, the body does not make insulin. In "
                "type 2, the body does not use insulin well.",
        "symptoms": "Common symptoms include being very thirsty, urinating a "
                    "lot, tiredness, blurry vision, and slow healing.",
        "treatments": "Treatments include insulin for type 1. For type 2, "
                      "healthy eating, activity, medicines, and sometimes "
                      "insulin. Checking blood sugar is a big part of care.",
        "facts": "Over time it can affect the eyes, kidneys, nerves, and "
                 "feet, so regular checkups matter. Medicare covers some "
                 "diabetes supplies and screenings.",
    },
    "copd": {
        "name": "COPD",
        "aliases": ["copd", "chronic obstructive pulmonary", "emphysema"],
        "what": "COPD is a long-term lung disease that makes it hard to "
                "breathe. It includes emphysema and chronic bronchitis, and "
                "is usually caused by smoking or long exposure to irritants.",
        "symptoms": "Common symptoms include shortness of breath, a long-term "
                    "cough, and wheezing.",
        "treatments": "Treatments include inhalers, pulmonary rehab, oxygen "
                      "for some people, and quitting smoking.",
        "facts": "Lung damage can't be reversed, but treatment can slow it "
                 "and help you feel better. Flu and pneumonia vaccines are "
                 "recommended.",
    },
    "ra": {
        "name": "rheumatoid arthritis",
        "aliases": ["rheumatoid arthritis", "ra"],
        "what": "Rheumatoid arthritis is an autoimmune disease where the "
                "immune system attacks the lining of the joints.",
        "symptoms": "Common symptoms include joint pain, swelling, and "
                    "stiffness, often in both hands or both feet and worse in "
                    "the morning.",
        "treatments": "Treatments include medicines that slow the disease, "
                      "pain and anti-inflammatory medicines, and physical "
                      "therapy.",
        "facts": "Starting treatment early helps protect the joints. It can "
                 "also affect other parts of the body, like the eyes and "
                 "heart.",
    },
    "lupus": {
        "name": "lupus",
        "aliases": ["lupus"],
        "what": "Lupus is an autoimmune disease where the immune system "
                "attacks healthy tissue. It can affect the skin, joints, "
                "kidneys, and heart.",
        "symptoms": "Common symptoms include tiredness, joint pain, skin "
                    "rashes, and fever. Symptoms often come in flares.",
        "treatments": "Treatments include medicines like hydroxychloroquine, "
                      "anti-inflammatory medicines, and medicines that calm "
                      "the immune system.",
        "facts": "It is more common in women. Sun exposure can trigger flares "
                 "for many people, so sun protection helps. Symptoms vary a "
                 "lot from person to person.",
    },
    "kidney": {
        "name": "kidney disease",
        "aliases": ["kidney", "renal", "dialysis"],
        "what": "Kidney disease means the kidneys slowly lose their ability "
                "to filter waste and extra fluid from the blood.",
        "symptoms": "Early on there are often no symptoms. Later it can cause "
                    "tiredness, swelling, and changes in urination.",
        "treatments": "Treatments include controlling blood pressure and "
                      "blood sugar, diet changes, and medicines. If the "
                      "kidneys fail, treatment is dialysis or a transplant.",
        "facts": "High blood pressure and diabetes are the most common "
                 "causes. Simple blood and urine tests can find it early. "
                 "People with permanent kidney failure who need dialysis or "
                 "a transplant can qualify for Medicare at any age.",
    },
    "md": {
        "name": "muscular dystrophy",
        "aliases": ["muscular dystrophy", "duchenne"],
        "what": "Muscular dystrophy is a group of inherited conditions that "
                "cause muscles to weaken over time.",
        "symptoms": "Common symptoms include muscle weakness, trouble "
                    "walking, and frequent falls. Which muscles are affected "
                    "depends on the type.",
        "treatments": "There is no cure, but steroids help some types. Care "
                      "also includes physical therapy, heart and breathing "
                      "checks, and assistive devices.",
        "facts": "There are many types. It is caused by changes in genes. "
                 "The heart and lungs can be involved, so regular checkups "
                 "are important.",
    },
}

# Where each built-in summary comes from, and the page to open for more.
SOURCE_NAME = "MedlinePlus, a service of the National Library of Medicine"
SOURCE_URLS = {
    "ms": "https://medlineplus.gov/multiplesclerosis.html",
    "parkinsons": "https://medlineplus.gov/parkinsonsdisease.html",
    "als": "https://medlineplus.gov/amyotrophiclateralsclerosis.html",
    "cp": "https://medlineplus.gov/cerebralpalsy.html",
    "epilepsy": "https://medlineplus.gov/epilepsy.html",
    "diabetes": "https://medlineplus.gov/diabetes.html",
    "copd": "https://medlineplus.gov/copd.html",
    "ra": "https://medlineplus.gov/rheumatoidarthritis.html",
    "lupus": "https://medlineplus.gov/lupus.html",
    "kidney": "https://medlineplus.gov/kidneydiseases.html",
    "md": "https://medlineplus.gov/musculardystrophy.html",
}
# Conditions where the summary also mentions Medicare coverage rules.
MEDICARE_NOTE = {"als", "kidney"}
MEDICARE_URL = "https://www.medicare.gov"


def mentions(text, alias):
    # Short aliases like "ms" or "als" must match a whole word.
    if " " in alias or len(alias) >= 5:
        return alias in text
    return alias in words(text)


def parse_condition(text):
    text = re.sub(r"^(i have|i've got|i got|learn about|tell me about|about|my)\s+",
                  "", text.strip())
    for key, info in CONDITIONS.items():
        if any(mentions(text, a) for a in info["aliases"]):
            return ("saved", key)
    return ("online", text) if text else None


def show_condition(value):
    kind, val = value
    return CONDITIONS[val]["name"] if kind == "saved" else val


def web_get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "MedicareVoiceNavigator/1.0 (student project)"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def web_get_raw(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "MedicareVoiceNavigator/1.0 (student project)"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read()


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_medlineplus(xml_bytes):
    """Pull title, short summary, and page link from a MedlinePlus response."""
    root = ET.fromstring(xml_bytes)
    doc = root.find(".//list/document")
    if doc is None:
        return None
    fields = {c.get("name"): "".join(c.itertext()) for c in doc.findall("content")}
    title = clean_text(fields.get("title", ""))
    summary = clean_text(fields.get("FullSummary") or fields.get("snippet", ""))
    url = doc.get("url")
    if not (title and summary and url):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    short = ""
    for s in sentences[:6]:
        if len(short) + len(s) > 700:
            break
        short += (" " if short else "") + s
    return (title, short or sentences[0], url)


def lookup_medlineplus(term):
    """Look up a health topic on MedlinePlus. Returns (title, summary, url) or None."""
    try:
        url = ("https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&retmax=1&term="
               + urllib.parse.quote_plus(term))
        return parse_medlineplus(web_get_raw(url))
    except Exception:
        return None


def lookup_online(term):
    """Get a short summary from Wikipedia. Returns (title, summary, url) or None."""
    try:
        search = web_get("https://en.wikipedia.org/w/api.php?action=opensearch"
                         "&limit=1&format=json&search=" + urllib.parse.quote(term))
        titles = search[1]
        if not titles:
            return None
        title = titles[0]
        page = web_get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                       + urllib.parse.quote(title.replace(" ", "_")))
        sentences = re.split(r"(?<=[.!?])\s+", page.get("extract", ""))
        summary = " ".join(sentences[:3]).strip()
        url = (page.get("content_urls", {}).get("desktop", {}).get("page")
               or "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")))
        return (title, summary, url) if summary else None
    except Exception:
        return None


def open_website(url):
    print("Link:", url)
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if opened:
        speak("I opened it in your browser. The link is also in the window "
              "where this app is running.")
    else:
        speak("I couldn't open the browser, but the link is in the window "
              "where this app is running.")


def offer_website(url):
    global last_url
    last_url = url
    ui_event("link", url)                              # GUI: show the website bar
    print("Link:", url)
    speak("Do you want me to open the website for more information? "
          "Say yes or no. You can also say open the website later.")
    answer = listen()
    if yes(answer) or any(p in answer for p in ["open", "take me", "go to"]):
        open_website(url)


def website_command():
    if last_url:
        open_website(last_url)
    else:
        speak("I don't have a website yet. Say learn about my condition first.")


def condition_info():
    speak("I can share general information about a health condition. "
          "I won't save which one you choose.")
    value = ask_field("Which condition do you want to learn about?",
                      parse_condition, show_condition)
    if value is None:
        return
    kind, val = value

    if kind == "saved":
        info = CONDITIONS[val]
        speak(f"Here is some general information about {info['name']}.")
        speak("What it is. " + info["what"])
        speak("Symptoms. " + info["symptoms"])
        speak("Treatments. " + info["treatments"])
        speak("Good to know. " + info["facts"])
        speak("Source: " + SOURCE_NAME + ".")
        if val in MEDICARE_NOTE:
            speak("For Medicare coverage rules, the source is medicare dot gov.")
        url = SOURCE_URLS[val]
    else:
        speak("That one isn't in my built-in list. Let me look it up online.")
        result = lookup_medlineplus(val)
        if result:
            title, summary, url = result
            speak(f"I found a MedlinePlus page called {title}. " + summary)
            speak("Source: " + SOURCE_NAME + ". This is an overview. "
                  "The website has more on treatments.")
        else:
            result = lookup_online(val)
            if result is None:
                speak("I couldn't find it online. Check your internet, or "
                      "try saying the full name of the condition.")
                return
            title, summary, url = result
            speak(f"MedlinePlus had nothing on that, so here is a short "
                  f"summary of {title}. " + summary)
            speak("Source: Wikipedia. Wikipedia is not a medical authority, "
                  "so check medline plus dot gov or ask your doctor for "
                  "treatments and more detail.")

    speak("This is general information, not medical advice. Your doctor "
          "knows your situation best.")
    offer_website(url)


# ---------- Pages ----------

def menu():
    speak("You can say deadlines, plans, letters, learn about my condition, "
          "set up my info, open the website, or help. Say quit to stop.")


def deadlines():
    profile = load_profile()
    if "ssdi_start" not in profile:
        speak("I need a little information first. Say set up my info.")
        return
    year, month = add_months(profile["ssdi_start"], 24)
    when = f"{MONTHS[month - 1].title()} {year}"
    speak("Medicare usually starts after 24 months of disability benefits. "
          f"Based on what you told me, that is around {when}. "
          "Some conditions, like ALS, start sooner. "
          "Check medicare.gov or call 1 800 633 4227 to confirm your date.")


PAGES = {"menu": menu, "deadlines": deadlines, "plans": plans,
         "letters": letters, "condition": condition_info}


def go_to(page):
    global current_page
    ui_event("page", page)                             # GUI: update the header
    history.append(current_page)
    current_page = page
    PAGES[page]()


def go_back():
    global current_page
    if history:
        current_page = history.pop()
        ui_event("page", current_page)                 # GUI: update the header
        PAGES[current_page]()
    else:
        speak("You are already at the start.")


# ---------- Commands ----------

def handle(text):
    if "repeat" in text or "say that again" in text:
        speak(last_message)
    elif any(p in text for p in ["website", "web site", "open the page", "open the link",
                                 "open link", "open it", "take me there", "go to the site"]):
        website_command()
    elif any(w in text for w in ["condition", "disease", "illness", "learn about"]):
        go_to("condition")
    elif "delete" in text or "clear my" in text:
        delete_profile()
    elif "set up" in text or "my info" in text or "update" in text:
        collect_profile()
    elif "what do you know" in text or "read my" in text:
        read_profile()
    elif "go back" in text or text == "back":
        go_back()
    elif "menu" in text or "start over" in text:
        history.clear()
        go_to("menu")
    elif "help" in text:
        menu()
    elif "deadline" in text or "when" in text:
        go_to("deadlines")
    elif "plan" in text:
        go_to("plans")
    elif "letter" in text:
        go_to("letters")
    else:
        speak("Sorry, I didn't catch that. Say help to hear your options.")


def run_logic():
    """Your old main() loop. It now runs in a background thread."""
    global engine
    engine = pyttsx3.init()    # create the voice in THIS thread (some systems require it)
    start_microphone()
    speak("Welcome to the Medicare navigator.")
    ui_event("page", "menu")
    menu()
    while True:
        text = listen(main=True)
        if "quit" in text or "exit" in text:
            speak("Goodbye.")
            ui_event("quit")
            break
        if text:
            try:
                handle(text)
            except Exception as err:
                print("Error:", err)
                ui_event("system", f"Something went wrong: {err}")
                speak("Sorry, something went wrong. Say help to hear your options.")


# =====================================================================
# THE WINDOW
# =====================================================================

P = {
    "sidebar": "#0F2742", "sidebar_hover": "#1B3A63", "sidebar_text": "#FFFFFF",
    "sidebar_muted": "#A9BDD6", "sidebar_disabled": "#6E88A6",
    "accent": "#2563EB", "accent_light": "#8CB8FF",
    "bg": "#F1F5F9", "card": "#FFFFFF", "text": "#0F172A", "muted": "#475569",
    "border": "#CBD5E1", "app_bubble": "#E0ECFF", "user_bubble": "#D1FAE5",
    "link_bg": "#EFF6FF", "focus": "#FBBF24",
}

# Status colors. Each status also has a word, so color is never the only clue.
STATUS = {
    "Ready": "#475569",
    "Listening": "#15803D",
    "Speaking": "#1D4ED8",
    "Thinking": "#B45309",
    "Error": "#B91C1C",
}

PAGE_INFO = {
    "menu": ("Home", "Say what you need, or tap a topic on the left."),
    "deadlines": ("Deadlines", "Your Medicare timeline."),
    "plans": ("Plans", "Think through which type of plan may fit you."),
    "letters": ("Letters", "Understand a letter you received."),
    "condition": ("Learn about a condition",
                  "General information only. Not medical advice."),
    "info": ("My info", "Saved only on this computer."),
}

# (page key, button label, text sent exactly as if you had said it)
NAV = [
    ("menu", "Home", "menu"),
    ("deadlines", "Deadlines", "deadlines"),
    ("plans", "Plans", "plans"),
    ("letters", "Letters", "letters"),
    ("condition", "Learn about a condition", "learn about my condition"),
    ("info", "Set up my info", "set up my info"),
    ("read", "Read my saved info", "what do you know about me"),
]

BASE_SIZE = 15
# font name: (size offset, bold?)
FONT_SPECS = {
    "small": (-3, False), "body": (0, False), "nav": (0, True),
    "button": (0, True), "big": (7, False), "title": (15, True),
    "brand": (7, True),
}


class FlatButton(tk.Label):
    """A modern flat button (also works with Tab + Enter/Space for keyboard users)."""

    def __init__(self, parent, text, command, font, bg, fg, hover_bg,
                 disabled_fg="#94A3B8", disabled_bg=None,
                 anchor="center", padx=16, pady=10):
        super().__init__(parent, text=text, font=font, bg=bg, fg=fg,
                         anchor=anchor, padx=padx, pady=pady, cursor="hand2",
                         takefocus=1, highlightthickness=2,
                         highlightbackground=bg, highlightcolor=P["focus"])
        self.command = command
        self.base_bg = bg
        self.fg_on = fg
        self.hover_bg = hover_bg
        self.disabled_fg = disabled_fg
        self.disabled_bg = disabled_bg
        self.enabled = True
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", lambda e: self._apply())
        self.bind("<Button-1>", self.invoke)
        self.bind("<Return>", self.invoke)
        self.bind("<space>", self.invoke)

    def _apply(self):
        if self.enabled:
            bg, fg, cursor = self.base_bg, self.fg_on, "hand2"
        else:
            bg = self.disabled_bg or self.base_bg
            fg, cursor = self.disabled_fg, "arrow"
        self.configure(bg=bg, fg=fg, cursor=cursor, highlightbackground=bg,
                       takefocus=1 if self.enabled else 0)

    def _on_enter(self, _event):
        if self.enabled:
            self.configure(bg=self.hover_bg, highlightbackground=self.hover_bg)

    def invoke(self, _event=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._apply()

    def set_base_bg(self, bg):
        self.base_bg = bg
        self._apply()


class NavigatorApp:
    def __init__(self, root):
        self.root = root
        self.status = "Ready"
        self.pulse_big = False
        self.scale = 0
        self.link_url = None

        root.title("Medicare Navigator")
        root.geometry("1180x780")
        root.minsize(980, 680)
        root.configure(bg=P["bg"])
        root.columnconfigure(0, minsize=260)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        family = self.pick_family()
        self.fonts = {
            name: tkfont.Font(family=family, size=BASE_SIZE + off,
                              weight="bold" if bold else "normal")
            for name, (off, bold) in FONT_SPECS.items()
        }

        self.nav_buttons = {}
        self.command_buttons = []   # work only at the main menu
        self.answer_buttons = []    # work only while a question is open

        self.build_sidebar()
        self.build_main()
        self.set_mode("busy")
        self.set_page("menu")

        self.entry.focus_set()
        self.root.after(80, self.poll)
        self.root.after(500, self.pulse)

    # ---------- small helpers ----------
    def pick_family(self):
        available = set(tkfont.families(self.root))
        for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue",
                     "Arial", "DejaVu Sans"):
            if name in available:
                return name
        return "Helvetica"

    def make_card(self, parent):
        return tk.Frame(parent, bg=P["card"], highlightbackground=P["border"],
                        highlightthickness=1)

    def send(self, text):
        typed_queue.put(text)

    def submit(self):
        text = self.entry.get().strip()
        if text:
            typed_queue.put(text)
            self.entry.delete(0, "end")

    # ---------- layout ----------
    def build_sidebar(self):
        side = tk.Frame(self.root, bg=P["sidebar"])
        side.grid(row=0, column=0, sticky="nsew")

        # Bottom group first, so it stays pinned to the bottom.
        bottom = tk.Frame(side, bg=P["sidebar"])
        bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 20))

        tk.Label(bottom, text="Text size", font=self.fonts["small"],
                 bg=P["sidebar"], fg=P["sidebar_muted"], anchor="w"
                 ).pack(fill="x", padx=6, pady=(0, 6))
        size_row = tk.Frame(bottom, bg=P["sidebar"])
        size_row.pack(fill="x")
        for label, delta in (("A-", -1), ("A+", 1)):
            FlatButton(size_row, label, lambda d=delta: self.change_text_size(d),
                       self.fonts["button"], P["sidebar_hover"], P["sidebar_text"],
                       P["accent"], pady=8
                       ).pack(side="left", expand=True, fill="x", padx=3)
        FlatButton(bottom, "Quit", self.root.destroy, self.fonts["button"],
                   "#991B1B", "#FFFFFF", "#B91C1C", pady=10
                   ).pack(fill="x", padx=3, pady=(14, 0))

        # Brand
        tk.Label(side, text="Medicare", font=self.fonts["brand"],
                 bg=P["sidebar"], fg=P["sidebar_text"], anchor="w"
                 ).pack(fill="x", padx=22, pady=(28, 0))
        tk.Label(side, text="Navigator", font=self.fonts["brand"],
                 bg=P["sidebar"], fg=P["accent_light"], anchor="w"
                 ).pack(fill="x", padx=22)
        tk.Label(side, text="Voice-guided help", font=self.fonts["small"],
                 bg=P["sidebar"], fg=P["sidebar_muted"], anchor="w"
                 ).pack(fill="x", padx=22, pady=(2, 18))

        # Navigation buttons
        for key, label, command in NAV:
            button = FlatButton(
                side, label, lambda c=command: self.send(c), self.fonts["nav"],
                P["sidebar"], P["sidebar_text"], P["sidebar_hover"],
                disabled_fg=P["sidebar_disabled"], anchor="w", padx=18, pady=11)
            button.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[key] = button

        tk.Frame(side, bg="#24405F", height=1).pack(fill="x", padx=22, pady=12)
        FlatButton(side, "Open last website", self.open_link, self.fonts["nav"],
                   P["sidebar"], P["sidebar_text"], P["sidebar_hover"],
                   disabled_fg=P["sidebar_disabled"], anchor="w", padx=18, pady=11
                   ).pack(fill="x", padx=12, pady=2)

    def build_main(self):
        main = tk.Frame(self.root, bg=P["bg"])
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=22)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        # --- header: page title + status pill ---
        header = tk.Frame(main, bg=P["bg"])
        header.grid(row=0, column=0, sticky="ew")
        titles = tk.Frame(header, bg=P["bg"])
        titles.pack(side="left")
        self.title_label = tk.Label(titles, text="", font=self.fonts["title"],
                                    bg=P["bg"], fg=P["text"], anchor="w")
        self.title_label.pack(anchor="w")
        self.hint_label = tk.Label(titles, text="", font=self.fonts["body"],
                                   bg=P["bg"], fg=P["muted"], anchor="w")
        self.hint_label.pack(anchor="w")

        self.pill = tk.Frame(header, bg=STATUS["Ready"])
        self.pill.pack(side="right", anchor="n", pady=6)
        self.dot = tk.Canvas(self.pill, width=16, height=16, highlightthickness=0,
                             bg=STATUS["Ready"])
        self.dot.pack(side="left", padx=(14, 0), pady=9)
        self.dot_id = self.dot.create_oval(4, 4, 12, 12, fill="white", outline="")
        self.status_label = tk.Label(self.pill, text="Ready", font=self.fonts["button"],
                                     bg=STATUS["Ready"], fg="white")
        self.status_label.pack(side="left", padx=(6, 16), pady=6)

        # --- big "now saying" card ---
        now_card = self.make_card(main)
        now_card.grid(row=1, column=0, sticky="ew", pady=(18, 12))
        tk.Frame(now_card, bg=P["accent"], width=8).pack(side="left", fill="y")
        inner = tk.Frame(now_card, bg=P["card"])
        inner.pack(side="left", fill="both", expand=True, padx=22, pady=16)
        tk.Label(inner, text="THE NAVIGATOR SAYS", font=self.fonts["small"],
                 bg=P["card"], fg=P["muted"], anchor="w").pack(fill="x")
        self.now_label = tk.Label(inner, text="Starting up...", font=self.fonts["big"],
                                  bg=P["card"], fg=P["text"], justify="left",
                                  anchor="w", wraplength=700)
        self.now_label.pack(fill="x", pady=(6, 0))
        inner.bind("<Configure>",
                   lambda e: self.now_label.configure(wraplength=max(300, e.width - 10)))

        # --- conversation ---
        log_card = self.make_card(main)
        log_card.grid(row=2, column=0, sticky="nsew")
        tk.Label(log_card, text="CONVERSATION", font=self.fonts["small"],
                 bg=P["card"], fg=P["muted"], anchor="w"
                 ).pack(fill="x", padx=22, pady=(14, 0))
        body = tk.Frame(log_card, bg=P["card"])
        body.pack(fill="both", expand=True)
        self.log = tk.Text(body, wrap="word", height=6, bg=P["card"], relief="flat",
                           highlightthickness=0, padx=16, pady=8, state="disabled",
                           cursor="arrow", takefocus=0, font=self.fonts["body"])
        scroll = tk.Scrollbar(body, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("app", justify="left", spacing1=6, spacing3=6)
        self.log.tag_configure("user", justify="right", spacing1=6, spacing3=6)
        self.log.tag_configure("system", justify="center", spacing1=6, spacing3=6)

        # --- website bar (hidden until a link exists) ---
        self.link_bar = tk.Frame(main, bg=P["link_bg"], highlightbackground=P["border"],
                                 highlightthickness=1)
        tk.Label(self.link_bar, text="More information", font=self.fonts["button"],
                 bg=P["link_bg"], fg=P["text"]).pack(side="left", padx=(16, 10), pady=10)
        self.link_label = tk.Label(self.link_bar, text="", font=self.fonts["small"],
                                   bg=P["link_bg"], fg=P["muted"], anchor="w")
        self.link_label.pack(side="left", fill="x", expand=True)
        FlatButton(self.link_bar, "Open website", self.open_link, self.fonts["button"],
                   P["accent"], "#FFFFFF", "#1D4ED8", pady=6
                   ).pack(side="right", padx=10, pady=8)

        # --- typing bar ---
        bar = tk.Frame(main, bg=P["bg"])
        bar.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        bar.columnconfigure(0, weight=1)
        self.entry = tk.Entry(bar, font=self.fonts["body"], relief="flat", bg=P["card"],
                              fg=P["text"], insertbackground=P["text"],
                              highlightthickness=2, highlightbackground=P["border"],
                              highlightcolor=P["accent"])
        self.entry.grid(row=0, column=0, sticky="ew", ipady=10)
        self.entry.bind("<Return>", lambda e: self.submit())
        FlatButton(bar, "Send", self.submit, self.fonts["button"], P["accent"],
                   "#FFFFFF", "#1D4ED8", pady=10
                   ).grid(row=0, column=1, padx=(10, 0))
        tk.Label(bar, text="Type a command or an answer and press Enter. "
                           "You can also just speak.",
                 font=self.fonts["small"], bg=P["bg"], fg=P["muted"]
                 ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- quick buttons ---
        quick = tk.Frame(main, bg=P["bg"])
        quick.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        for text, command, colors in (
            ("Yes", "yes", ("#15803D", "#FFFFFF", "#166534")),
            ("No", "no", ("#B91C1C", "#FFFFFF", "#991B1B")),
            ("Skip", SKIP, ("#475569", "#FFFFFF", "#334155")),
        ):
            button = FlatButton(quick, text, lambda c=command: self.send(c),
                                self.fonts["button"], colors[0], colors[1], colors[2],
                                disabled_bg="#E2E8F0", padx=26)
            button.pack(side="left", padx=(0, 8))
            self.answer_buttons.append(button)

        tk.Frame(quick, bg=P["bg"], width=24).pack(side="left")
        for text, command in (("Repeat", "repeat"), ("Go back", "go back"),
                              ("Help", "help")):
            button = FlatButton(quick, text, lambda c=command: self.send(c),
                                self.fonts["button"], "#E2E8F0", P["text"], "#CBD5E1",
                                padx=22)
            button.pack(side="left", padx=(0, 8))
            self.command_buttons.append(button)

    # ---------- behavior ----------
    def change_text_size(self, delta):
        self.scale = max(-2, min(10, self.scale + delta))
        for name, (offset, _bold) in FONT_SPECS.items():
            self.fonts[name].configure(size=BASE_SIZE + offset + self.scale)

    def set_status(self, status):
        self.status = status
        color = STATUS.get(status, STATUS["Ready"])
        self.pill.configure(bg=color)
        self.dot.configure(bg=color)
        self.status_label.configure(text=status, bg=color)

    def pulse(self):
        """Makes the dot throb while the app is listening."""
        if self.status == "Listening":
            self.pulse_big = not self.pulse_big
            r = 7 if self.pulse_big else 4
        else:
            r = 4
        self.dot.coords(self.dot_id, 8 - r, 8 - r, 8 + r, 8 + r)
        self.root.after(500, self.pulse)

    def set_mode(self, mode):
        menu_on = mode == "menu"
        question_on = mode == "question"
        for button in self.nav_buttons.values():
            button.set_enabled(menu_on)
        for button in self.command_buttons:
            button.set_enabled(menu_on)
        for button in self.answer_buttons:
            button.set_enabled(question_on)

    def set_page(self, key):
        title, hint = PAGE_INFO.get(key, PAGE_INFO["menu"])
        self.title_label.configure(text=title)
        self.hint_label.configure(text=hint)
        for nav_key, button in self.nav_buttons.items():
            button.set_base_bg(P["accent"] if nav_key == key else P["sidebar"])

    def show_link(self, url):
        self.link_url = url
        shown = url if len(url) < 70 else url[:67] + "..."
        self.link_label.configure(text=shown)
        self.link_bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))

    def open_link(self):
        if self.link_url:
            webbrowser.open(self.link_url)

    def add_bubble(self, kind, text):
        width = max(320, int(self.log.winfo_width() * 0.7))
        if kind == "system":
            label = tk.Label(self.log, text=text, font=self.fonts["small"],
                             bg=P["card"], fg=P["muted"], wraplength=width,
                             justify="center", padx=8, pady=4)
        else:
            bg = P["app_bubble"] if kind == "app" else P["user_bubble"]
            label = tk.Label(self.log, text=text, font=self.fonts["body"], bg=bg,
                             fg=P["text"], wraplength=width, justify="left",
                             padx=16, pady=10)
        self.log.configure(state="normal")
        start = self.log.index("end-1c")
        self.log.window_create("end", window=label)
        self.log.insert("end", "\n")
        self.log.tag_add(kind, start, "end-1c")
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---------- reading messages from the logic thread ----------
    def poll(self):
        try:
            while True:
                kind, data = ui_queue.get_nowait()
                if kind == "say":
                    self.now_label.configure(text=data)
                    self.add_bubble("app", data)
                elif kind == "heard":
                    self.add_bubble("user", data)
                elif kind == "system":
                    self.add_bubble("system", data)
                elif kind == "status":
                    self.set_status(data)
                elif kind == "mode":
                    self.set_mode(data)
                elif kind == "page":
                    self.set_page(data)
                elif kind == "link":
                    self.show_link(data)
                elif kind == "quit":
                    self.root.after(1200, self.root.destroy)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)


# =====================================================================
# START
# =====================================================================

def main():
    root = tk.Tk()
    NavigatorApp(root)                                   # builds the window
    threading.Thread(target=run_logic, daemon=True).start()   # starts your logic
    root.mainloop()                                      # runs the window


if __name__ == "__main__":
    main()


