from flask import Flask, request, redirect, render_template_string
from datetime import datetime
from openpyxl import Workbook, load_workbook
import os

app = Flask(__name__)

EXCEL_FILE = "checkins.xlsx"

# Create the Excel file if it doesn't exist
if not os.path.exists(EXCEL_FILE):
    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "Timestamp"])
    wb.save(EXCEL_FILE)

@app.route("/")
def home():
    return redirect("/scan")

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        name = request.form.get("name")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save to Excel
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
        ws.append([name, timestamp])
        wb.save(EXCEL_FILE)

        return f"<h2>Thanks, {name}! Checked in at {timestamp}</h2><a href='/scan'>Go back</a>"

    html = """
    <h1>Check-In Form</h1>
    <form method="POST">
        <input name="name" placeholder="Enter your name" required>
        <button type="submit">Check In</button>
    </form>
    """
    return render_template_string(html)
