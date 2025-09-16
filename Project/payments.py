from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app
from datetime import datetime  # <-- Import datetime on its own line
from .extensions import db
from .models import Team, User
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
            # This token is the key to our reliable success route
            success_url=url_for('payments.success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payments.cancel', _external=True),
            client_reference_id=g.user.team_id 
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        current_app.logger.error(f"Error creating checkout session: {e}")
        flash("Error communicating with Stripe. Please try again.", "error")
        return redirect(url_for('auth.pricing'))

@payments_bp.route("/success")
def success():
    """
    Handles the user's return from a successful Stripe payment.
    This route is robust against session loss and logs errors gracefully.
    """
    # Best Case: The user is still logged in.
    if g.user and g.user.role == 'Admin':
        flash("Payment successful! Your team is now on the Pro plan.", "success")
        return redirect(url_for('admin.dashboard'))

    # Fallback Case: The user's session was lost. Use the session_id from the URL.
    stripe_session_id = request.args.get('session_id')
    if not stripe_session_id:
        flash("Could not verify payment session. Please log in to see your plan status.", "error")
        return redirect(url_for('auth.login'))

    try:
        session_data = stripe.checkout.Session.retrieve(stripe_session_id)
        team_id_str = session_data.get('client_reference_id')
        
        if not team_id_str:
            flash("Could not identify the team for this payment. Please log in to confirm status.", "error")
            return redirect(url_for('auth.login'))

        # Safely convert team_id from string to integer
        team_id = int(team_id_str)
        team_admin = User.query.filter_by(team_id=team_id, role='Admin').first()
        
        flash("Payment successful! Your team is now on the Pro plan. Please log in to continue.", "success")
        
        # Pre-fill the admin's email for a smooth login experience
        if team_admin:
            return redirect(url_for('auth.login', email=team_admin.email))
        else:
            return redirect(url_for('auth.login'))

    except Exception as e:
        # Log the real error for debugging and show a helpful message
        current_app.logger.error(f"Error in Stripe success route: {e}")
        flash("We couldn’t confirm your payment details automatically, but your subscription is likely active. Please log in to check your status.", "warning")
        return redirect(url_for('auth.login'))

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
    This version is robust for Live Mode and correctly handles the subscription lifecycle.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.error(f"Stripe webhook signature error: {e}")
        return "Invalid payload or signature", 400

    event_type = event.get("type")
    obj = event["data"]["object"]

    try:
        # Event 1: A new subscription is successfully created.
        if event_type == "checkout.session.completed":
            session = stripe.checkout.Session.retrieve(obj.id, expand=["subscription"])
            
            team_id = session.client_reference_id
            customer_id = session.customer
            subscription = session.get('subscription') # Use .get() for safety

            if team_id:
                team = Team.query.get(int(team_id))
                if team:
                    team.plan = "Pro"
                    team.stripe_customer_id = customer_id
                    
                    # --- THIS IS THE FIX ---
                    # Only try to get the expiration date IF the subscription object exists
                    if subscription and subscription.get('current_period_end'):
                        team.pro_access_expires_at = datetime.fromtimestamp(subscription.current_period_end)
                    # --- END OF FIX ---
                    
                    db.session.commit()
                    current_app.logger.info(f"Team {team.id} successfully upgraded to Pro.")

        # Event 2: A recurring payment succeeds (subscription is renewed).
        elif event_type == "invoice.paid":
            customer_id = obj.get("customer")
            subscription_id = obj.get("subscription")
            if customer_id and obj.get("billing_reason") == "subscription_cycle":
                team = Team.query.filter_by(stripe_customer_id=customer_id).first()
                if team:
                    # Get the full subscription object to get the end date
                    subscription = stripe.Subscription.retrieve(subscription_id)
                    if subscription and subscription.get('current_period_end'):
                        team.pro_access_expires_at = datetime.fromtimestamp(subscription.current_period_end)
                        db.session.commit()
                        current_app.logger.info(f"Team {team.id} successfully renewed Pro plan.")

        # Event 3: The subscription is TRULY deleted by Stripe at the period end.
        elif event_type == "customer.subscription.deleted":
            customer_id = obj.get("customer")
            team = Team.query.filter_by(stripe_customer_id=customer_id).first()
            if team:
                team.plan = "Free"
                team.pro_access_expires_at = None
                db.session.commit()
                current_app.logger.info(f"Team {team.id} successfully downgraded to Free.")

    except Exception as e:
        current_app.logger.error(f"Error handling Stripe webhook ({event_type}): {e}")
        return "Webhook processing error", 500

    return "OK", 200