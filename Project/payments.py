from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app
from .models import db, Team, User  # <-- ENSURE USER IS IMPORTED
from .decorators import admin_required
import stripe
import os

payments_bp = Blueprint('payments', __name__)

@payments_bp.route("/create-checkout-session", methods=["POST"])
@admin_required
def create_checkout_session():
    """Creates a Stripe Checkout session to upgrade the user to Pro."""
    price_id = os.environ.get('STRIPE_PRICE_ID')
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            allow_promotion_codes=True,
            success_url=url_for('payments.success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payments.cancel', _external=True),
            client_reference_id=g.user.team_id 
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        current_app.logger.error(f"Error creating checkout session: {e}")
        flash(f"Error communicating with Stripe. Please try again.", "error")
        return redirect(url_for('auth.pricing'))

# In app/Project/payments.py

# In app/Project/payments.py

@payments_bp.route("/success")
def success():
    # This is a simple test to confirm if the new code is actually deployed.
    # It does not do any logic. It just returns a message.
    return "<h1>The new success function is live.</h1>"

@payments_bp.route("/cancel")
def cancel():
    flash("Payment was cancelled. Your plan has not been changed.", "error")
    return redirect(url_for('auth.pricing'))

@payments_bp.route("/create-portal-session", methods=["POST"])
@admin_required
def create_portal_session():
    """Creates a Stripe Customer Portal session for the user to manage their subscription."""
    if not g.user.team.stripe_customer_id:
        flash("No subscription found to manage.", "error")
        return redirect(url_for('admin.dashboard'))
    
    portal_session = stripe.billing_portal.Session.create(
        customer=g.user.team.stripe_customer_id,
        return_url=url_for('admin.dashboard', _external=True),
    )
    return redirect(portal_session.url, code=303)

@payments_bp.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """
    Listens for events from Stripe to update the database reliably.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.error(f"Stripe webhook error: {e}")
        return "Invalid payload or signature", 400

    event_type = event.get("type")
    obj = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            team_id = obj.get("client_reference_id")
            customer_id = obj.get("customer")
            if team_id:
                team = Team.query.get(int(team_id))
                if team:
                    team.plan = "Pro"
                    team.stripe_customer_id = customer_id
                    db.session.commit()
                    current_app.logger.info(f"Team {team.id} upgraded to Pro")
        
        elif event_type == "customer.subscription.deleted":
            customer_id = obj.get("customer")
            team = Team.query.filter_by(stripe_customer_id=customer_id).first()
            if team:
                team.plan = "Free"
                db.session.commit()
                current_app.logger.info(f"Team {team.id} downgraded to Free")

    except Exception as e:
        current_app.logger.error(f"Error handling Stripe webhook ({event_type}): {e}")
        return "Webhook processing error", 500

    return "OK", 200