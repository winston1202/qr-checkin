# app/Project/payments.py

from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app, session
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
    Restores the user's session if it was lost and redirects to the dashboard.
    """
    stripe_session_id = request.args.get('session_id')
    try:
        # Best Case: The user's session survived the redirect.
        if g.user and g.user.role == 'Admin':
            flash("Payment successful! Your team has been upgraded to the Pro plan.", "success")
            return redirect(url_for('admin.dashboard'))

        # Fallback Case: The session was lost. Use the Stripe session_id to find the user.
        if not stripe_session_id:
            flash("Payment successful, but we couldn't log you back in automatically. Please log in.", "error")
            return redirect(url_for('auth.login'))

        session_data = stripe.checkout.Session.retrieve(stripe_session_id)
        team_id = int(session_data.get('client_reference_id'))
        team_admin = User.query.filter_by(team_id=team_id, role='Admin').first()

        if team_admin:
            # This is the key: we log the user back in by setting their session ID.
            session['user_id'] = team_admin.id
            flash("Payment successful! Your team has been upgraded to the Pro plan.", "success")
            return redirect(url_for('admin.dashboard'))
        else:
            flash("Payment successful! Please log in to access your dashboard.", "success")
            return redirect(url_for('auth.login'))

    except Exception as e:
        current_app.logger.error(f"Error in Stripe success route: {e}")
        flash("We couldn’t confirm your payment automatically, but your subscription is likely active. Please log in.", "warning")
        return redirect(url_for('auth.login'))

@payments_bp.route("/cancel")
def cancel():
    flash("Payment was cancelled. Your plan has not been changed.", "error")
    return redirect(url_for('auth.pricing'))

@payments_bp.route("/create-portal-session", methods=["POST"])
@admin_required
def create_portal_session():
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
        if event_type == "checkout.session.completed":
            session = stripe.checkout.Session.retrieve(obj.id)
            team_id = session.client_reference_id
            customer_id = session.customer

            if team_id:
                team = Team.query.get(int(team_id))
                if team:
                    team.plan = "Pro"
                    team.stripe_customer_id = customer_id
                    db.session.commit()
                    current_app.logger.info(f"Team {team.id} successfully upgraded to Pro.")
        
        elif event_type == "invoice.paid":
            customer_id = obj.get("customer")
            if customer_id and obj.get("billing_reason") in ["subscription_cycle", "subscription_create"]:
                team = Team.query.filter_by(stripe_customer_id=customer_id).first()
                if team:
                    period_end = obj.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
                    if period_end:
                        team.pro_access_expires_at = datetime.fromtimestamp(period_end)
                        db.session.commit()

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