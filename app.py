from flask import Flask, request, redirect, render_template_string
from datetime import datetime

app = Flask(__name__)

# Redirect root URL to /scan
@app.route("/")
def home():
    return redirect("/scan")

# Check-in form at /scan
@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        name = request.form.get("name")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Just print for now — replace this with Excel logging later
        print(f"✅ {name} checked in at {timestamp}")
        return f"<h2>Thanks, {name}! Checked in at {timestamp}</h2><a href='/scan'>Go back</a>"

    # Simple form HTML
    html = """
    <h1>Check-In Form</h1>
    <form method="POST">
        <input name="name" placeholder="Enter your name" required>
        <button type="submit">Check In</button>
    </form>
    """
    return render_template_string(html)

# Run the app (optional if using gunicorn)
if __name__ == "__main__":
    app.run()
