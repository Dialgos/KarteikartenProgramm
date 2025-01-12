import os
import json
import traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from PyPDF2 import PdfReader
from openai import OpenAI
import constants  # constants.py enthält openAIAPI

app = Flask(__name__)
app.secret_key = 'Ihr_geheimer_Schlüssel'  # bitte anpassen

# Initialisiere den OpenAI-Client mit dem API-Key aus constants.
client = OpenAI(api_key=constants.openAIAPI)

# Verzeichnis, in dem die Benutzerdaten gespeichert werden.
DATA_DIR = "/home/lukas/KarteikartenProgramm/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def get_user_cards(username):
    """Lädt die Karten des Benutzers (als Liste) aus einer JSON-Datei."""
    filepath = os.path.join(DATA_DIR, f"{username}.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            app.logger.error("Fehler beim Laden der Datei %s: %s", filepath, e)
    return []

def save_user_cards(username, cards):
    """Speichert die Karten des Benutzers in einer JSON-Datei."""
    filepath = os.path.join(DATA_DIR, f"{username}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cards, f, ensure_ascii=False, indent=2)
    except Exception as e:
        app.logger.error("Fehler beim Speichern der Datei %s: %s", filepath, e)

def get_user_credentials(username):
    """Lädt die Zugangsdaten (z.B. Passwort) aus einer JSON-Datei für den Benutzer."""
    filepath = os.path.join(DATA_DIR, f"{username}_auth.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            app.logger.error("Fehler beim Laden der Auth-Datei %s: %s", filepath, e)
    return None

def save_user_credentials(username, password):
    """Speichert die Zugangsdaten für den Benutzer."""
    filepath = os.path.join(DATA_DIR, f"{username}_auth.json")
    daten = {"username": username, "password": password}  # In einer echten App unbedingt hashen!
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
    except Exception as e:
        app.logger.error("Fehler beim Speichern der Auth-Datei %s: %s", filepath, e)

def bereinige_generierten_text(text):
    """
    Entfernt Markdown-Code-Fences (z. B. ```json ... ```) sowie
    führende/nachfolgende Leerzeichen.
    """
    text = text.strip()
    if text.startswith("```"):
        zeilen = text.splitlines()
        if zeilen[0].startswith("```"):
            zeilen = zeilen[1:]
        if zeilen and zeilen[-1].strip() == "```":
            zeilen = zeilen[:-1]
        text = "\n".join(zeilen).strip()
    return text

# ---------------------------
# Routen zur Benutzerverwaltung und Authentifizierung
# ---------------------------

@app.route("/", methods=["GET"])
def select_user():
    """
    Zeigt die Benutzer-Auswahlseite an, auf der man einen Benutzer anlegen
    und auswählen kann.
    """
    users = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith("_auth.json"):
            users.append(filename[:-10])  # entfernt _auth.json
    return render_template("user_select.html", users=users)

@app.route("/create_user", methods=["POST"])
def create_user():
    daten = request.get_json()
    username = daten.get("username", "").strip()
    password = daten.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "Benutzername und Passwort müssen übermittelt werden."}), 400
    # Überprüfe, ob Benutzer schon existiert
    if get_user_credentials(username) is not None:
        return jsonify({"error": "Benutzer existiert bereits."}), 400
    # Erstelle leere Karten-Datei und speichere Zugangsdaten
    save_user_cards(username, [])
    save_user_credentials(username, password)
    return jsonify({"user": username})

@app.route("/login", methods=["POST"])
def login():
    daten = request.get_json()
    username = daten.get("username", "").strip()
    password = daten.get("password", "").strip()
    creds = get_user_credentials(username)
    if creds is None or creds.get("password") != password:
        return jsonify({"error": "Ungültige Anmeldedaten."}), 400
    session["user"] = username
    return jsonify({"user": username})

# Routen, die einen angemeldeten Benutzer erfordern:
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("select_user"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/app/<username>", methods=["GET"])
@login_required
def main_app(username):
    """
    Zeigt das Hauptinterface (main.html) für den angegebenen Benutzer an.
    """
    # Für zusätzliche Sicherheit prüfen wir, ob der angemeldete User passt.
    if session.get("user") != username:
        return redirect(url_for("select_user"))
    cards = get_user_cards(username)
    return render_template("main.html", username=username, cards=json.dumps(cards, ensure_ascii=False, indent=2))

@app.route("/load_cards", methods=["GET"])
def load_cards_route():
    """
    Liefert die Karten des Benutzers als JSON.
    """
    username = request.args.get("user", "").strip()
    if not username:
        return jsonify({"error": "Kein Benutzer angegeben."}), 400
    cards = get_user_cards(username)
    return jsonify({"cards": cards})

@app.route("/manual_card", methods=["POST"])
def manual_card():
    """
    Fügt manuell eine Karte hinzu, bearbeitet oder löscht sie.
    Erwartet JSON-Daten: {user, question, solution, [editIndex]}.
    Zum Löschen einer Karte müssen Frage und Lösung leer sein.
    Beim Bearbeiten wird, falls vorhanden, das Feld "formula" beibehalten.
    """
    daten = request.get_json()
    username = daten.get("user", "").strip()
    if not username:
        return jsonify({"error": "Kein Benutzer angegeben."}), 400

    # Karten laden
    karten = get_user_cards(username)
    edit_index = daten.get("editIndex")

    # Löschen: Wenn Frage und Lösung leer sind und ein Index übergeben wurde.
    if edit_index is not None:
        try:
            edit_index = int(edit_index)
            if edit_index < 0 or edit_index >= len(karten):
                return jsonify({"error": "Ungültiger Index."}), 400

            frage = daten.get("question", "").strip()
            loesung = daten.get("solution", "").strip()

            if frage == "" and loesung == "":
                # Karte löschen
                karten.pop(edit_index)
            else:
                # Beim Bearbeiten: Falls bereits ein "formula"-Feld existiert, beibehalten.
                original_karte = karten[edit_index]
                lecture = original_karte.get("lecture", username)
                formula = original_karte.get("formula", "")
                # Setze die bearbeitete Karte – hier wird "formula" übernommen, wenn vorhanden.
                card_obj = {
                    "question": frage,
                    "solution": loesung,
                    "lecture": lecture
                }
                if formula:
                    card_obj["formula"] = formula
                karten[edit_index] = card_obj
        except Exception as e:
            return jsonify({"error": "Fehler beim Bearbeiten/Löschen der Karte: " + str(e)}), 400
    else:
        # Neue Karte hinzufügen: Falls keine "formula" vom Formular kommt, setzen wir sie nicht (kann später hinzugefügt werden)
        frage = daten.get("question", "").strip()
        loesung = daten.get("solution", "").strip()
        if not (frage and loesung):
            return jsonify({"error": "Bitte beide Felder ausfüllen."}), 400
        # Neue Karten erhalten als "lecture" den aktuellen Benutzernamen
        karten.append({"question": frage, "solution": loesung, "lecture": username})
    
    save_user_cards(username, karten)
    return jsonify({"cards": karten})

@app.route("/upload", methods=["POST"])
def upload_pdf():
    username = request.form.get("user", "").strip()
    if not username:
        return jsonify({"error": "Kein Benutzer angegeben."}), 400

    if "pdf_file" not in request.files:
        return jsonify({"error": "Keine Datei übermittelt!"}), 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return jsonify({"error": "Keine Datei ausgewählt!"}), 400

    try:
        # PDF-Text extrahieren
        reader = PdfReader(file)
        voller_text = ""
        for seite in reader.pages:
            seiten_text = seite.extract_text()
            if seiten_text:
                voller_text += seiten_text + "\n"

        # Vorlesungstitel extrahieren
        lecture_name, _ = os.path.splitext(file.filename)

        # Erweiterter Prompt
        prompt = (
            "Extrahiere die wichtigsten Lernpunkte aus folgendem Vorlesungstext und generiere Index-Karten. "
            "Falls der Text mathematische Gleichungen enthält oder Gleichungen teilweise unvollständig sind, kombiniere "
            "das mathematische Wissen von OpenAI mit dem Vorlesungsinhalt. Falls möglich, gebe diese Gleichungen in einem eigenen "
            "Feld \"formula\" im LaTeX-Format zurück. **Wichtig:** Verwende in allen LaTeX-Formeln doppelte Backslashes (\\\\) für "
            "LaTeX-Befehle. Jede Karte soll ein JSON-Objekt mit den Schlüsseln \"question\", \"solution\" und, falls vorhanden, "
            "\"formula\" sein.\n\n"
            "Vorlesungstext:\n" + voller_text + "\n\n"
            "Gib das Ergebnis als JSON-Liste zurück, z. B. so:\n"
            "[{\"question\": \"Was ist ...?\", \"solution\": \"Es ist ...\", \"formula\": \"\\\\(E=mc^2\\\\)\"}, "
            "{\"question\": \"Wie funktioniert ...?\", \"solution\": \"So funktioniert es ...\"}]"
        )

        app.logger.debug("An OpenAI gesendetes Prompt:")
        app.logger.debug(prompt)

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
                {"role": "user", "content": prompt}
            ]
        )

        generierter_text = completion.choices[0].message.content
        app.logger.debug("Roh generierter Text von OpenAI:")
        app.logger.debug(generierter_text)

        def extract_json(text):
            start = text.find("```json")
            if start != -1:
                start = text.find("\n", start)
                end = text.find("```", start)
                if end != -1:
                    return text[start:end].strip()
            return text.strip()

        cleaned_text = extract_json(generierter_text)
        
        # Korrigiere unzureichend maskierte Backslashes in LaTeX-Ausdrücken:
        import re
        cleaned_text = re.sub(r'(?<!\\)\\\(', r'\\\\(', cleaned_text)
        cleaned_text = re.sub(r'(?<!\\)\\\)', r'\\\\)', cleaned_text)
        
        app.logger.debug("Bereinigter generierter Text:")
        app.logger.debug(cleaned_text)

        try:
            karten_json = json.loads(cleaned_text)
            app.logger.debug("Erfolgreich geparstes JSON:")
            app.logger.debug(karten_json)
        except Exception as json_err:
            app.logger.error("Fehler beim Parsen des generierten JSON:")
            app.logger.error(traceback.format_exc())
            return jsonify({
                "error": "Fehler beim Parsen des generierten JSON.",
                "generated_text": cleaned_text,
                "exception": str(json_err)
            }), 500

        for karte in karten_json:
            karte["lecture"] = lecture_name

        existierende_karten = get_user_cards(username)
        neue_karten = existierende_karten + karten_json
        save_user_cards(username, neue_karten)

        return jsonify({"cards": neue_karten, "new_count": len(karten_json)})
    
    except Exception as e:
        app.logger.error("Fehler beim PDF-Upload und -Verarbeiten:")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
