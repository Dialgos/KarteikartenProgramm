import os
import json
import traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from PyPDF2 import PdfReader
from openai import OpenAI
import constants  # constants.py enthält openAIAPI = "Ihr_API_Key_hier"

app = Flask(__name__)

# Initialisiere den OpenAI-Client mit dem API-Key aus constants.
client = OpenAI(api_key=constants.openAIAPI)

def bereinige_generierten_text(text):
    """
    Entfernt Markdown-Code-Fences (z. B. ```json ... ```) und
    leert führende bzw. nachfolgende Leerzeichen.
    """
    text = text.strip()
    if text.startswith("```"):
        # Entferne die erste Zeile (optional mit Sprachangabe) und die letzte Zeile, falls es Code-Fences sind.
        zeilen = text.splitlines()
        if zeilen[0].startswith("```"):
            zeilen = zeilen[1:]
        if zeilen and zeilen[-1].strip() == "```":
            zeilen = zeilen[:-1]
        text = "\n".join(zeilen).strip()
    return text

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_pdf():
    if 'pdf_file' not in request.files:
        flash("Keine Datei übermittelt!")
        return redirect(url_for('index'))

    file = request.files['pdf_file']
    if file.filename == '':
        flash("Keine Datei ausgewählt!")
        return redirect(url_for('index'))

    try:
        # Lese den Text aus der PDF.
        reader = PdfReader(file)
        voller_text = ""
        for seite in reader.pages:
            seiten_text = seite.extract_text()
            if seiten_text:
                voller_text += seiten_text + "\n"

        # Erstelle das Prompt für OpenAI.
        prompt = (
            "Extrahiere die wichtigsten Lernpunkte aus folgendem Vorlesungstext "
            "und generiere Index-Karten. Jede Karte soll ein JSON-Objekt mit den Schlüsseln "
            "'question' und 'solution' sein.\n\nVorlesungstext:\n" + voller_text + "\n\n"
            "Gib das Ergebnis als JSON-Liste zurück, z. B. so:\n"
            '[{\"question\": \"Was ist ...?\", \"solution\": \"Es ist ...\"}, '
            '{\"question\": \"Wie funktioniert ...?\", \"solution\": \"So funktioniert es ...\"}]'
        )

        app.logger.debug("An OpenAI gesendetes Prompt:")
        app.logger.debug(prompt)

        # Rufe die OpenAI API auf.
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
                {"role": "user", "content": prompt}
            ]
        )

        # Extrahiere und bereinige den generierten Text.
        generierter_text = completion.choices[0].message.content
        app.logger.debug("Roh generierter Text von OpenAI:")
        app.logger.debug(generierter_text)

        generierter_text = bereinige_generierten_text(generierter_text)
        app.logger.debug("Bereinigter generierter Text:")
        app.logger.debug(generierter_text)

        # Versuche, den bereinigten Text als JSON zu laden.
        try:
            karten_json = json.loads(generierter_text)
            app.logger.debug("Erfolgreich geparstes JSON:")
            app.logger.debug(karten_json)
        except Exception as json_err:
            app.logger.error("Fehler beim Parsen des generierten JSON:")
            app.logger.error(traceback.format_exc())
            return jsonify({
                "error": "Fehler beim Parsen des generierten JSON.",
                "generated_text": generierter_text,
                "exception": str(json_err)
            }), 500

        # Rückgabe der generierten Karten.
        return jsonify({"cards": karten_json})
    
    except Exception as e:
        app.logger.error("Fehler beim PDF-Upload und -Verarbeiten:")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route('/generate_cards', methods=['POST'])
def generate_cards():
    daten = request.get_json()
    vorlesungstext = daten.get("lecture_text", "")
    if not vorlesungstext:
        return jsonify({"error": "Kein Vorlesungstext übermittelt."}), 400

    prompt = (
        "Extrahiere die wichtigsten Lernpunkte aus folgendem Vorlesungstext "
        "und generiere Index-Karten. Jede Karte soll ein JSON-Objekt mit den Schlüsseln "
        "'question' und 'solution' sein.\n\nVorlesungstext:\n" + vorlesungstext + "\n\n"
        "Gib das Ergebnis als JSON-Liste zurück, z. B. so:\n"
        '[{\"question\": \"Was ist ...?\", \"solution\": \"Es ist ...\"}, '
        '{\"question\": \"Wie funktioniert ...?\", \"solution\": \"So funktioniert es ...\"}]'
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
                {"role": "user", "content": prompt}
            ]
        )
        generierter_text = completion.choices[0].message.content
        app.logger.debug("Roh generierter Text (generate_cards):")
        app.logger.debug(generierter_text)

        generierter_text = bereinige_generierten_text(generierter_text)
        app.logger.debug("Bereinigter generierter Text (generate_cards):")
        app.logger.debug(generierter_text)

        try:
            karten_json = json.loads(generierter_text)
            app.logger.debug("Erfolgreich geparstes JSON (generate_cards):")
            app.logger.debug(karten_json)
        except Exception as json_err:
            app.logger.error("Fehler beim Parsen des generierten JSON (generate_cards):")
            app.logger.error(traceback.format_exc())
            return jsonify({
                "error": "Fehler beim Parsen des generierten JSON.",
                "generated_text": generierter_text,
                "exception": str(json_err)
            }), 500

        return jsonify({"cards": karten_json})
    
    except Exception as e:
        app.logger.error("Fehler beim Generieren der Karten:")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
