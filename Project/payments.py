from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app
from .models import db, Team, User
from .decorators import admin_required
import stripe
import os

payments_bp = Blueprint('payments', __name__)


# In app/Project/payments.py

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
            
            # --- THIS IS THE CRITICAL CHANGE ---
            # We tell Stripe to include its own session ID in the return URL.
            success_url=url_for('payments.success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            # --- END OF CHANGE ---

            cancel_url=url_for('payments.cancel', _external=True),
            client_reference_id=g.user.team_id 
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Error communicating with Stripe: {e}", "error")
        return redirect(url_for('auth.pricing'))

@payments_bp.route("/success")
def success():
    """
    Handles the user's return from a successful Stripe payment.
    It uses the session_id from the URL to verify the purchase and
    provide a clear next step, regardless of the user's login status.
    """
    stripe_session_id = request.args.get('session_id')
    if not stripe_session_id:
        flash("Could not verify payment session. Please log in to see your plan status.", "error")
        return redirect(url_for('auth.login'))

    try:
        # Use the session_id to get the purchase details from Stripe
        session_data = stripe.checkout.Session.retrieve(stripe_session_id)
        # Get the team_id we stored when the process started
        team_id = session_data.get('client_reference_id')
        
        if not team_id:
            flash("Could not identify the team for this payment. Please log in to confirm your status.", "error")
            return redirect(url_for('auth.login'))

        # Find the admin for that team
        team_admin = User.query.filter_by(team_id=team_id, role='Admin').first()
        
        # We now know who the customer is, even if they were logged out.
        flash("Payment successful! Your team is now on the Pro plan. Please log in to continue.", "success")
        
        if team_admin:
            # For a great user experience, we can pre-fill their email on the login page.
            return redirect(url_for('auth.login', email=team_admin.email))
        else:
            # Fallback if we can't find the admin for some reason
            return redirect(url_for('auth.login'))

    except Exception as e:
        # This will catch errors if someone tries to use a fake session_id
        flash(f"An error occurred while verifying your payment.", "error")
        return redirect(url_for('auth.home'))

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
    Handles new subscriptions and cancellations.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    if not sig_header:
        current_app.logger.warning("Stripe webhook: missing signature header")
        return "Missing Stripe signature header", 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.error(f"Stripe webhook error: {e}")
        return "Invalid payload or signature", 400

    event_type = event.get("type")
    obj = event["data"]["object"]

    try:
        # --- New subscription created ---
        if event_type == "checkout.session.completed":
            team_id = obj.get("client_reference_id")
            customer_id = obj.get("customer")

            if team_id and customer_id:
                team = Team.query.get(int(team_id))
                if team:
                    team.plan = "Pro"
                    team.stripe_customer_id = customer_id
                    db.session.commit()
                    current_app.logger.info(f"Team {team.id} upgraded to Pro")

        # --- Subscription fully cancelled ---
        elif event_type == "customer.subscription.deleted":
            customer_id = obj.get("customer")

            team = Team.query.filter_by(stripe_customer_id=customer_id).first()
            if team and team.plan == "Pro":
                team.plan = "Free"
                db.session.commit()
                current_app.logger.info(f"Team {team.id} downgraded to Free")

    except Exception as e:
        current_app.logger.error(f"Error handling Stripe webhook ({event_type}): {e}")
        return "Webhook processing error", 500

    return "OK", 200
