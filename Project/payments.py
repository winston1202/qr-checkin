# app/Project/payments.py

from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app
from .extensions import db
from .models import Team, User
from .decorators import admin_required
import stripe
import os
from datetime import datetime

payments_bp = Blueprint('payments', __name__)

@payments_bp.route("/create-checkout-session", methods=["POST"])
@admin_required
def create_checkout_session():
    price_id = os.environ.get('STRIPE_PRICE_ID')
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            allow_promotion_codes=True,
            # This simple URL points to our unbreakable success route
            success_url=url_for('payments.success', _external=True),
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
    This is a simple confirmation page. It trusts the webhook to handle the
    database update and safely directs the user to the login page.
    """
    flash("Payment successful! Your plan has been upgraded. Please log in to view your dashboard.", "success")
    return redirect(url_for('auth.login'))

@payments_bp.route("/cancel")
def cancel():
    flash("Payment was cancelled. Your plan has not been changed.", "error")
    return redirect(url_for('auth.pricing'))

@payments_bp.route("/create-portal-session", methods=["POST"])
@admin_required
def create_portal_session():
    # --- THIS IS THE NEW SECURITY CHECK ---
    # We now check if the logged-in user's ID matches the team's owner_id.
    if g.user.id != g.user.team.owner_id:
        flash("Only the team owner can manage the subscription.", "error")
        return redirect(url_for('admin.dashboard'))
    # --- END OF SECURITY CHECK ---

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
    This version is robust for Live Mode and correctly handles the full subscription lifecycle,
    including scheduled cancellations and renewals.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    # If webhook secret is not configured, log warning but don't fail
    if not webhook_secret:
        current_app.logger.warning("STRIPE_WEBHOOK_SECRET not configured")
        return "Webhook secret not configured", 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        current_app.logger.error(f"Stripe webhook invalid payload: {e}")
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError as e:
        current_app.logger.error(f"Stripe webhook signature verification failed: {e}")
        return "Invalid signature", 400
    except Exception as e:
        current_app.logger.error(f"Unexpected error verifying webhook: {e}")
        return "Webhook verification error", 400

    event_type = event.get("type")
    obj = event["data"]["object"]

    current_app.logger.info(f"Processing Stripe webhook event: {event_type}")

    try:
        # Event 1: A new subscription is successfully created.
        if event_type == "checkout.session.completed":
            session = stripe.checkout.Session.retrieve(obj.id)
            team_id = session.client_reference_id
            customer_id = session.customer

            if team_id:
                team = Team.query.get(int(team_id))
                if team:
                    team.plan = "Pro"
                    team.stripe_customer_id = customer_id
                    # On a new subscription, there is no cancellation date.
                    team.pro_access_expires_at = None
                    db.session.commit()
                    current_app.logger.info(f"Team {team.id} successfully upgraded to Pro.")
                else:
                    current_app.logger.warning(f"Team {team_id} not found for checkout session")

        # Event 2: A subscription is updated (e.g., a user cancels or renews).
        elif event_type == "customer.subscription.updated":
            customer_id = obj.get("customer")
            team = Team.query.filter_by(stripe_customer_id=customer_id).first()

            if team:
                # Check if the user has scheduled the subscription to cancel at the end of the period.
                if obj.get("cancel_at_period_end"):
                    # If yes, save the exact date it will expire.
                    # 'cancel_at' is a reliable timestamp for this.
                    cancel_at = obj.get("cancel_at")
                    if cancel_at:
                        expiration_date = datetime.fromtimestamp(cancel_at)
                        team.pro_access_expires_at = expiration_date
                        current_app.logger.info(f"Team {team.id} has scheduled their subscription to cancel on {expiration_date}.")
                else:
                    # If no, it means they have renewed or reactivated the plan.
                    # We must clear the expiration date.
                    team.pro_access_expires_at = None
                    current_app.logger.info(f"Team {team.id} has renewed/reactivated their subscription.")
                
                db.session.commit()
            else:
                current_app.logger.warning(f"Team not found for customer {customer_id}")

        # Event 3: The subscription is TRULY deleted by Stripe at the period end.
        elif event_type == "customer.subscription.deleted":
            customer_id = obj.get("customer")
            team = Team.query.filter_by(stripe_customer_id=customer_id).first()
            if team:
                team.plan = "Free"
                team.pro_access_expires_at = None
                db.session.commit()
                current_app.logger.info(f"Team {team.id} has been successfully downgraded to Free.")
            else:
                current_app.logger.warning(f"Team not found for customer {customer_id}")

    except Exception as e:
        current_app.logger.error(f"Error handling Stripe webhook ({event_type}): {str(e)}", exc_info=True)
        # Even if processing fails, return 200 to prevent Stripe from retrying
        # Log the error for manual investigation
        return "OK", 200

    return "OK", 200