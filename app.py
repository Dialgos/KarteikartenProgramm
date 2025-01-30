import os
import re
import smtplib
import sqlite3
import traceback
import json
import random
import string
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from PyPDF2 import PdfReader
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash
import constants  # constants.py must have: openAIAPI, EPW

app = Flask(__name__)
app.secret_key = 'Ihr_geheimer_Schlüssel'  # Change this for production

# -------------------------------------------------
# 1) Setup OpenAI client
# -------------------------------------------------
client = OpenAI(api_key=constants.openAIAPI)

# -------------------------------------------------
# 2) Setup Flask-Login
# -------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"


class User(UserMixin):
    def __init__(self, user_id, email):
        self.id = user_id   # Flask-Login expects .id
        self.email = email  # keep track of email as well

@login_manager.user_loader
def load_user(user_id):
    """
    Reload user object from user_id stored in the session.
    """
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        uid, email = row
        return User(uid, email)
    return None

# -------------------------------------------------
# 3) Utility: Send Email
# -------------------------------------------------
def send_info_via_email(recipient, subject, mailbody):
    SENDER = 'lukas.strobel@na1583.de'
    SMTP_SERVER = 'smtp.1und1.de'
    SMTP_PORT = 465
    PASSWORD = constants.EPW  # from constants.py

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SENDER
    msg['To'] = recipient
    part = MIMEText(mailbody, 'html')
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, [recipient], msg.as_string())
        app.logger.info(f"Email sent to {recipient}")
    except Exception as e:
        app.logger.error(f"Error sending email: {e}")

    return f"Email sent to {recipient}"

# -------------------------------------------------
# 4) Database Helper Functions for karteikarten
# -------------------------------------------------
def get_user_id_by_email(email):
    """
    Returns (id) from 'users' table for the given email, or None if not found.
    """
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_cards_for_user(user_id):
    """
    Return a list of dicts representing all cards for the given user_id.
    Sorted maybe by created_at or something. We'll do ascending by id for now.
    """
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        SELECT id, question, solution, formula, lecture
        FROM karteikarten
        WHERE user_id = ?
        ORDER BY id ASC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    cards = []
    for row in rows:
        card_id, question, solution, formula, lecture = row
        cards.append({
            "id": card_id,
            "question": question,
            "solution": solution,
            "formula": formula if formula else "",
            "lecture": lecture if lecture else ""
        })
    return cards

def create_card(user_id, question, solution, lecture, formula=None):
    """
    Insert a new card into 'karteikarten'.
    Returns the new card's id.
    """
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO karteikarten (user_id, question, solution, formula, lecture, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (user_id, question, solution, formula, lecture))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update_card(card_id, question, solution, lecture, formula=None):
    """
    Update an existing card in 'karteikarten'.
    """
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        UPDATE karteikarten
        SET question = ?, solution = ?, formula = ?, lecture = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (question, solution, formula, lecture, card_id))
    conn.commit()
    conn.close()

def delete_card(card_id):
    """
    Delete a card by its ID.
    """
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("DELETE FROM karteikarten WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()

# -------------------------------------------------
# 5) Routes: Login, Register, Verify
# -------------------------------------------------
@app.route("/")
def login_page():
    return render_template("user_select.html")

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()
    if not email or not password:
        return jsonify({"error": "Please provide email and password"}), 400

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = c.fetchone()
    if row:
        conn.close()
        return jsonify({"error": "User with that email already exists"}), 400

    code = "".join(random.choices(string.digits, k=6))
    hashed_password = generate_password_hash(password)
    c.execute("""
        INSERT INTO users (email, password_hash, verified, verification_code)
        VALUES (?, ?, ?, ?)
    """, (email, hashed_password, 0, code))
    conn.commit()
    conn.close()

    subject = "Your Verification Code"
    mailbody = f"""
    <p>Thank you for registering!</p>
    <p>Your verification code is: <strong>{code}</strong></p>
    <p>Please enter this code on the verification page.</p>
    """
    send_info_via_email(email, subject, mailbody)

    return jsonify({"success": True, "email": email})

@app.route("/verify_page")
def verify_page():
    user_email = request.args.get("email", "")
    return render_template("verify_page.html", user_email=user_email)

@app.route("/verify_and_login", methods=["POST"])
def verify_and_login():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()

    if not email or not code:
        return "Missing email or code", 400

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id, verification_code, verified, password_hash FROM users WHERE email = ?", (email,))
    row = c.fetchone()
    if not row:
        conn.close()
        return "No such user", 400

    user_id, stored_code, verified, password_hash = row

    if verified == 1:
        # Already verified => just log in
        conn.close()
        user_obj = User(user_id, email)
        login_user(user_obj)
        return redirect(url_for("main_app", user_email=email))

    if stored_code != code:
        conn.close()
        return "Incorrect code", 400

    # correct code => verify
    c.execute("""
        UPDATE users
        SET verified=1, verification_code=NULL
        WHERE id=?
    """, (user_id,))
    conn.commit()
    conn.close()

    # auto-login
    user_obj = User(user_id, email)
    login_user(user_obj)
    return redirect(url_for("main_app", user_email=email))

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id, password_hash, verified FROM users WHERE email = ?", (email,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "User not found"}), 400

    user_id, password_hash, verified = row
    if verified == 0:
        return jsonify({"error": "Please verify your account first"}), 400

    if not check_password_hash(password_hash, password):
        return jsonify({"error": "Incorrect password"}), 400

    user_obj = User(user_id, email)
    login_user(user_obj)
    app.logger.debug(f"User logged in: {email}")
    return jsonify({"success": True, "email": email})

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))

# -------------------------------------------------
# 6) Main App / Cards
# -------------------------------------------------
@app.route("/app/<user_email>", methods=["GET"])
@login_required
def main_app(user_email):
    """
    Shows the main interface for the given user_email.
    """
    if current_user.email.lower() != user_email.lower():
        return redirect(url_for("login_page"))

    # get all cards from DB for the current user
    all_cards = get_cards_for_user(current_user.id)
    return render_template("main.html",
                           username=current_user.email,
                           cards=json.dumps(all_cards, ensure_ascii=False, indent=2))

@app.route("/load_cards", methods=["GET"])
def load_cards_route():
    """
    Returns the user's cards as JSON. 
    Must pass ?user=the_user_email
    """
    email = request.args.get("user", "").strip().lower()
    if not email:
        return jsonify({"error": "No user specified"}), 400

    # security check
    if not current_user.is_authenticated or current_user.email.lower() != email:
        return jsonify({"error": "Not authorized"}), 403

    user_id = current_user.id
    user_cards = get_cards_for_user(user_id)

    response = jsonify({"cards": user_cards})
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/manual_card", methods=["POST"])
def manual_card():
    """
    Add/edit/delete a card in DB.
    JSON: { user, question, solution, [editIndex or DB card id], formula? }
    To delete: question/solution empty
    """
    data = request.get_json()
    email = data.get("user", "").strip().lower()
    if not email:
        return jsonify({"error": "No user specified"}), 400

    if not current_user.is_authenticated or current_user.email.lower() != email:
        return jsonify({"error": "Not authorized"}), 403

    # Load all user cards
    user_id = current_user.id
    all_cards = get_cards_for_user(user_id)

    edit_index = data.get("editIndex")  # This is the front-end's index in 'cards' array
    question = data.get("question", "").strip()
    solution = data.get("solution", "").strip()

    # We'll match the card in the DB by ID, not index. So let's do a safe approach:
    # The front-end, for each card, includes 'id'. We'll read data.get("id") if we want direct DB references.

    # If the front-end doesn't have the DB ID, let's use the index approach for now:
    if edit_index is not None:
        try:
            edit_index = int(edit_index)
            if edit_index < 0 or edit_index >= len(all_cards):
                return jsonify({"error": "Invalid card index"}), 400

            card_db_id = all_cards[edit_index]["id"]
            original_card = all_cards[edit_index]
            if question == "" and solution == "":
                # Delete
                delete_card(card_db_id)
            else:
                lecture = original_card.get("lecture", email)
                formula = original_card.get("formula", "")
                update_card(card_db_id, question, solution, lecture, formula)
        except Exception as e:
            return jsonify({"error": f"Error editing/deleting card: {e}"}), 400
    else:
        # new card
        if not (question and solution):
            return jsonify({"error": "Please provide both question and solution"}), 400
        # lecture default to email, or you can pass data
        lecture = data.get("lecture", email)
        create_card(user_id, question, solution, lecture, formula=None)

    # return updated list
    updated_cards = get_cards_for_user(user_id)
    return jsonify({"cards": updated_cards})

@app.route("/upload", methods=["POST"])
def upload_pdf():
    """
    Upload a PDF, parse it, generate index-cards via OpenAI, store them in DB.
    Return new card list as JSON.
    """
    email = request.form.get("user", "").strip().lower()
    if not email:
        return jsonify({"error": "No user specified"}), 400

    if not current_user.is_authenticated or current_user.email.lower() != email:
        return jsonify({"error": "Not authorized"}), 403

    if "pdf_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    user_id = current_user.id

    try:
        reader = PdfReader(file)
        voller_text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                voller_text += page_text + "\n"

        lecture_name, _ = os.path.splitext(file.filename)

        prompt = (
            "Extrahiere die wichtigsten Lernpunkte aus folgendem Vorlesungstext und generiere Index-Karten. "
            "Falls der Text mathematische Gleichungen enthält oder Gleichungen teilweise unvollständig sind, kombiniere "
            "das mathematische Wissen von OpenAI mit dem Vorlesungsinhalt. Falls möglich, gebe diese Gleichungen in einem eigenen "
            "Feld \"formula\" im LaTeX-Format zurück. **Wichtig:** Verwende in allen LaTeX-Formeln doppelte Backslashes (\\\\) für "
            "LaTeX-Befehle. Jede Karte soll ein JSON-Objekt mit den Schlüsseln \"question\", \"solution\" und, falls vorhanden, "
            "\"formula\" sein.\n\n"
            "Vorlesungstext:\n" + voller_text + "\n\n"
            "Gib das Ergebnis als JSON-Liste zurück, z.B.:\n"
            "[{\"question\": \"Was ist ...?\", \"solution\": \"Es ist ...\", \"formula\": \"\\\\(E=mc^2\\\\)\"}, "
            "{\"question\": \"Wie funktioniert ...?\", \"solution\": \"So funktioniert es ...\"}]"
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",  # or whichever model you want
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
                {"role": "user", "content": prompt}
            ]
        )

        generierter_text = completion.choices[0].message.content

        import re
        def extract_json(text):
            start = text.find("```json")
            if start != -1:
                start = text.find("\n", start)
                end = text.find("```", start)
                if end != -1:
                    return text[start:end].strip()
            return text.strip()

        cleaned_text = extract_json(generierter_text)
        cleaned_text = re.sub(r'(?<!\\)\\\(', r'\\\\(', cleaned_text)
        cleaned_text = re.sub(r'(?<!\\)\\\)', r'\\\\)', cleaned_text)

        try:
            karten_json = json.loads(cleaned_text)
        except Exception as json_err:
            app.logger.error("Error parsing JSON: %s", traceback.format_exc())
            return jsonify({
                "error": "Fehler beim Parsen des generierten JSON.",
                "generated_text": cleaned_text,
                "exception": str(json_err)
            }), 500

        # Insert each card in DB
        new_count = 0
        for karte in karten_json:
            q = karte.get("question", "").strip()
            s = karte.get("solution", "").strip()
            f = karte.get("formula", "").strip() if karte.get("formula") else ""
            if not q or not s:
                continue
            create_card(user_id, q, s, lecture_name, formula=f)
            new_count += 1

        updated_cards = get_cards_for_user(user_id)
        return jsonify({"cards": updated_cards, "new_count": new_count})

    except Exception as e:
        app.logger.error("Error in PDF upload: %s", traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/export_anki", methods=["GET"])
@login_required
def export_anki():
    email = request.args.get("user", "").strip().lower()
    if not email:
        return jsonify({"error": "No user specified"}), 400

    if current_user.email.lower() != email:
        return jsonify({"error": "Not authorized"}), 403

    lecture = request.args.get("lecture", "").strip()
    user_id = current_user.id

    all_cards = get_cards_for_user(user_id)
    if not lecture:
        lecture = "all"
    if lecture.lower() != "all":
        all_cards = [c for c in all_cards if c.get("lecture", "").lower() == lecture.lower()]

    lines = []
    for card in all_cards:
        q = card["question"].replace("\t", "    ").replace("\n", "<br>")
        s = card["solution"].replace("\t", "    ").replace("\n", "<br>")
        f_raw = card.get("formula", "").replace("\t", "    ").replace("\n", "<br>")
        if f_raw.strip():
            # remove \(\) from formula
            latex_inner = f_raw.replace("\\(", "").replace("\\)", "").strip()
            latex_formula = f"$$ {latex_inner} $$"
        else:
            latex_formula = ""
        if latex_formula:
            back_field = f"{s}<br>{latex_formula}"
        else:
            back_field = s

        lect = card.get("lecture", "").replace("\t", " ").replace("\n", " ")
        line = f"{q}\t{back_field}\t{lect}"
        lines.append(line)

    file_content = "\n".join(lines)
    resp = make_response(file_content)
    resp.headers["Content-Disposition"] = f'attachment; filename=anki_{lecture}.txt'
    resp.mimetype = "text/tab-separated-values"
    return resp

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
