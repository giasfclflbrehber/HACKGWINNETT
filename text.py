import speech_recognition as sr
import pyttsx3

engine = pyttsx3.init()
recognizer = sr.Recognizer()

PAGES = {
    "deadlines": "Your enrollment deadlines will show here.",
    "plans": "Plan options will show here.",
    "letters": "Letter explanations will show here.",
}

def speak(message):
    print("App:", message)
    engine.say(message)
    engine.runAndWait()

def listen():
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        speak("Listening.")
        try:
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=8)
            return recognizer.recognize_google(audio).lower()
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            speak("I can't reach the speech service. Check your internet.")
            return ""

def handle(text):
    if "deadline" in text:
        speak(PAGES["deadlines"])
    elif "plan" in text:
        speak(PAGES["plans"])
    elif "letter" in text:
        speak(PAGES["letters"])
    elif "help" in text:
        speak("You can say deadlines, plans, letters, or quit.")
    else:
        speak("Sorry, I didn't catch that. Say help to hear your options.")

def main():
    speak("Welcome to the Medicare navigator. Say help to hear your options.")
    while True:
        text = listen()
        print("You said:", text)
        if "quit" in text or "exit" in text:
            speak("Goodbye.")
            break
        if text:
            handle(text)

if __name__ == "__main__":
    main()
