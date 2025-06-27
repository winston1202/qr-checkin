from flask import Flask, request, render_template_string, redirect
from datetime import datetime
import openpyxl
import os

app = Flask(__name__)
EXCEL_FILE = "attendance_log.xlsx"

# Create Excel file if it doesn't exist
if not os.path.exists(EXCEL_FILE):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Date", "Time"])
    wb.save(EXCEL_FILE)

# HTML Form Page
form_html = """
<!doctype html>
<title>Check In</title>
<h1>Scan Log Form</h1>
<form method="POST">
  <label>Name:</label><br>
  <input name="name" required><br><br>
  <button type="submit">Submit</button>
</form>
"""

# Confirmation Page
success_html = """
<!doctype html>
<title>Success</title>
<h1>Thank you, {{ name }}!</h1>
<p>Checked in at {{ time }}</p>
"""

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        name = request.form["name"]
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active
        ws.append([name, date_str, time_str])
        wb.save(EXCEL_FILE)

        return render_template_string(success_html, name=name, time=f"{date_str} {time_str}")
    
    return form_html

if __name__ == "__main__":
    app.run(port=5000)
