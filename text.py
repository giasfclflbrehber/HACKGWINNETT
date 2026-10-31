import html
import json
import os
import re
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
import webbrowser
from datetime import date

import speech_recognition as sr
import pyttsx3

engine = pyttsx3.init()
recognizer = sr.Recognizer()

PROFILE_FILE = "profile.json"
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

last_message = ""
last_url = None  # the website from the most recent condition lookup
current_page = "menu"
history = []

# ---------- Speaking and listening ----------

def speak(message):
    global last_message
    last_message = message
    print("App:", message)
    engine.say(message)
    engine.runAndWait()

def listen():
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("Listening...")
        try:
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=8)
            text = recognizer.recognize_google(audio).lower()
            print("You said:", text)
            return text
        except (sr.WaitTimeoutError, sr.UnknownValueError):
            return ""
        except sr.RequestError:
            speak("I can't reach the speech service. Check your internet.")
            return ""

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
            answer = input("I couldn't hear you. Type your answer, "
                           "or press Enter to skip: ").strip().lower()
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
    history.append(current_page)
    current_page = page
    PAGES[page]()

def go_back():
    global current_page
    if history:
        current_page = history.pop()
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

def main():
    speak("Welcome to the Medicare navigator.")
    menu()
    while True:
        text = listen()
        if "quit" in text or "exit" in text:
            speak("Goodbye.")
            break
        if text:
            handle(text)

if __name__ == "__main__":
    main()